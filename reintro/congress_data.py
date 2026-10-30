"""Read the output of unitedstates/congress (`usc-run bills`, `usc-run govinfo`).

Layout, under the data directory:

    {congress}/bills/{type}/{type}{number}/data.json
    {congress}/bills/{type}/{type}{number}/text-versions/{version}/document.xml

`data.json` is the bill status (titles, sponsor, cosponsors, subjects, actions,
`enacted_as`); `document.xml` is GPO's bill DTD text of one version.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .matching import BillKey

BILL_TYPES = ("hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres")

# Text version codes, earliest stage first. Only the codes that can be the last
# version of a bill that did not become law matter for picking a source text.
VERSION_ORDER = [
    "ih", "is", "rih", "ris", "rfh", "rfs", "rh", "rs", "rch", "rcs", "rth", "rts",
    "pch", "pcs", "cph", "cps", "eh", "es", "eah", "eas", "eh1s", "rds", "rdh",
    "ath", "ats", "cdh", "cds", "enr",
]
INTRODUCED = {"ih", "is"}


@dataclass
class BillRecord:
    bill_id: str
    congress: int
    bill_type: str
    number: int
    data: dict
    path: Path

    @property
    def introduced_at(self) -> str | None:
        return self.data.get("introduced_at")

    @property
    def official_title(self) -> str:
        return self.data.get("official_title") or ""

    @property
    def short_titles(self) -> list[str]:
        out = []
        for t in self.data.get("titles", []):
            if t.get("type") == "short" and t.get("title"):
                out.append(t["title"])
        if self.data.get("short_title"):
            out.append(self.data["short_title"])
        return list(dict.fromkeys(out))

    @property
    def sponsor_id(self) -> str | None:
        sp = self.data.get("sponsor") or {}
        return sp.get("bioguide_id")

    @property
    def enacted(self) -> bool:
        return bool(self.data.get("enacted_as")) or bool((self.data.get("history") or {}).get("enacted"))

    def key(self) -> BillKey:
        return BillKey(self.bill_id, self.official_title, tuple(self.short_titles),
                       self.sponsor_id, self.introduced_at)

    extra_texts: dict[str, Path] | None = None

    def text_versions(self) -> dict[str, Path]:
        out = dict(self.extra_texts or {})
        d = self.path / "text-versions"
        if d.is_dir():
            for v in d.iterdir():
                doc = v / "document.xml"
                if doc.exists():
                    out[v.name] = doc
        return out

    def latest_text(self) -> tuple[str, Path] | None:
        """The most advanced version short of enrollment, which is what a
        reintroduction starts from."""
        vs = self.text_versions()
        if not vs:
            return None
        rank = {v: i for i, v in enumerate(VERSION_ORDER)}
        pick = max((v for v in vs if v != "enr"), key=lambda v: rank.get(re.sub(r"\d+$", "", v), -1), default=None)
        if pick is None:
            pick = "enr"
        return pick, vs[pick]

    def introduced_text(self) -> tuple[str, Path] | None:
        vs = self.text_versions()
        for v in ("ih", "is"):
            if v in vs:
                return v, vs[v]
        return None


def bill_dir(data_dir: Path, congress: int, bill_type: str, number: int) -> Path:
    return Path(data_dir) / str(congress) / "bills" / bill_type / f"{bill_type}{number}"


_BULK_NAME = re.compile(r"BILLS-(\d+)([a-z]+)(\d+)([a-z][a-z0-9]*)\.xml$")


def bulk_texts(data_dir: Path, congress: int) -> dict[str, dict[str, Path]]:
    """Texts fetched with `usc-run govinfo --bulkdata=BILLS`, stored under
    `govinfo/BILLS/{congress}/{session}/{type}/BILLS-{congress}{type}{number}{version}.xml`."""
    out: dict[str, dict[str, Path]] = {}
    base = Path(data_dir) / "govinfo" / "BILLS" / str(congress)
    if not base.is_dir():
        return out
    for f in base.glob("*/*/BILLS-*.xml"):
        m = _BULK_NAME.match(f.name)
        if m:
            out.setdefault(f"{m.group(2)}{m.group(3)}-{m.group(1)}", {})[m.group(4)] = f
    return out


def iter_bills(data_dir: Path, congress: int, types=BILL_TYPES) -> Iterator[BillRecord]:
    bulk = bulk_texts(data_dir, congress)
    for t in types:
        d = Path(data_dir) / str(congress) / "bills" / t
        if not d.is_dir():
            continue
        for bd in d.iterdir():
            m = re.fullmatch(rf"{t}(\d+)", bd.name)
            if not m:
                continue
            f = bd / "data.json"
            data = json.loads(f.read_text()) if f.exists() else {}
            bid = f"{t}{m.group(1)}-{congress}"
            yield BillRecord(bid, congress, t, int(m.group(1)), data, bd, bulk.get(bid))


def parse_bill_id(bill_id: str) -> tuple[str, int, int]:
    m = re.fullmatch(r"([a-z]+)(\d+)-(\d+)", bill_id)
    if not m:
        raise ValueError(f"not a bill id: {bill_id}")
    return m.group(1), int(m.group(2)), int(m.group(3))
