"""Build the reintroduction draft of one bill: convert, reintroduce, check.

Drafts are cached on disk under `{cache_dir}/drafts/` by bill, source version,
target Congress and the US Code release point the checks read.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from . import citations, db, reintroduce, uslm
from .congress_data import parse_bill_id
from .linkedleg import Statutes, USCode


class NotEligible(Exception):
    """The bill became law, or has no text to start from."""


@dataclass
class DraftResult:
    bill_id: str
    xml: bytes
    warnings: list[dict]
    changes: list[str]
    report: dict
    source: dict

    @property
    def tree(self):
        return etree.ElementTree(etree.fromstring(self.xml))

    def warnings_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["bill", "kind", "where", "message"])
        for x in self.warnings:
            w.writerow([self.source.get("citation"), x["kind"], x.get("where") or "", x["message"]])
        return buf.getvalue()


def source_info(row) -> dict:
    t, n, c = parse_bill_id(row["bill_id"])
    return {
        "bill_id": row["bill_id"], "congress": c, "bill_type": t, "number": n,
        "version": row["text_version"], "date": row["text_date"] or row["introduced_at"],
        "citation": f"{db.citation(t, n)}, {reintroduce.ordinal(c)} Congress",
        "href": f"/us/bill/{c}/{t}/{n}",
        "title": row["display_title"],
    }


def build(con, bill_id: str, *, to_congress: int | None = None, uscode: USCode | None = None,
          statutes: Statutes | None = None, cache_dir: Path | None = None, chamber: str | None = None,
          force: bool = False, allow_enacted: bool = False) -> DraftResult:
    row = db.get_bill(con, bill_id)
    if row is None:
        raise KeyError(bill_id)
    if row["enacted"] and not allow_enacted:
        raise NotEligible(f"{bill_id} became law")
    if not row["text_path"]:
        raise NotEligible(f"{bill_id} has no published text")
    src = source_info(row)
    to = to_congress or src["congress"] + 1

    release = None
    if uscode is not None:
        s = uscode.status()
        if s.status == 200 and s.data:
            release = (s.data.get("corpus") or {}).get("latest_release")
    key = hashlib.sha256(json.dumps([bill_id, row["text_version"], row["text_path"], to, chamber, release,
                                     uscode is not None, statutes is not None, VERSION]).encode()).hexdigest()[:16]
    cached = (cache_dir / "drafts" / f"{bill_id}-{key}.json") if cache_dir else None
    if cached and cached.exists() and not force:
        d = json.loads(cached.read_text())
        return DraftResult(bill_id, d["xml"].encode(), d["warnings"], d["changes"], d["report"], d["source"])

    conv = uslm.convert(row["text_path"], version=row["text_version"])
    draft = reintroduce.reintroduce(conv.tree, to_congress=to, source=src, chamber=chamber,
                                    introduction_date=max(dt.date.today(), reintroduce.congress_start(to)))
    report = {"unknown_elements": conv.unknown, "uscode_release": release}
    if uscode is not None or statutes is not None:
        report.update(citations.check(draft, uscode, statutes, since=src["date"]))
    res = DraftResult(bill_id, uslm.to_bytes(draft.tree), draft.warnings_as_dicts(), draft.changes, report, src)
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps({"xml": res.xml.decode(), "warnings": res.warnings, "changes": res.changes,
                                      "report": res.report, "source": res.source}))
    return res


VERSION = "1"
