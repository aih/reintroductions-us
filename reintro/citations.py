"""Check a draft's citations against current law and mark what needs review.

For every United States Code section the draft cites (a `<ref href="/us/usc/…">`
or a written citation such as "section 1562 of title 38, United States Code"):

- `citation-missing`: the section is not found, or is repealed, transferred,
  omitted or renumbered.
- `citation-amended`: the section's text changed after the source text's date,
  with the laws the classification tables attribute the change to.
- `struck-text-not-found`: an instruction strikes words that the section, as it
  reads now, does not contain.

For every Public Law cited (`<ref href="/us/pl/…">`), `law-amended` when
statutes.linkedlegislation.org knows of an amendment enacted after the source
text's date.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass

from lxml import etree

from .linkedleg import Statutes, USCode
from .reintroduce import Draft, Warning, add_warning, in_quoted
from .uslm import U

USC_HREF = re.compile(r"^/us/usc/t(\d+a?)/s([^/]+)")
PL_HREF = re.compile(r"^/us/pl/(\d+)/(\d+)")
USC_WRITTEN = re.compile(r"\bsection ([0-9][0-9A-Za-z\-–]*)(?:\([^)]{1,8}\))*(?: and [^,]{0,30})? of title (\d+),? United States Code", re.I)
USC_SHORT = re.compile(r"\b(\d+) U\.S\.C\. ([0-9][0-9A-Za-z\-–]*)")
PL_SECTION = re.compile(r"\bsection ([0-9][0-9A-Za-z]*)(?:\([^)]{1,8}\))* of (?:the )?[^()]{0,160}\($")
GONE = {"repealed", "transferred", "omitted", "renumbered", "reserved"}
TEXT_BLOCKS = ("content", "chapeau", "continuation", "p")


@dataclass
class Cite:
    title: str
    section: str
    el: etree._Element     # the text block holding the citation


def _block_of(el):
    n = el
    while n is not None and n.tag not in tuple(U(t) for t in TEXT_BLOCKS):
        n = n.getparent()
    return n if n is not None else el


def _own_text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext()))


def collect_usc(root) -> list[Cite]:
    out: list[Cite] = []
    for r in root.iter(U("ref")):
        m = USC_HREF.match(r.get("href", ""))
        if m:
            out.append(Cite(m.group(1), m.group(2).replace("–", "-"), _block_of(r)))
    for blk in root.iter(*(U(t) for t in TEXT_BLOCKS)):
        txt = _own_text(blk)
        for rx in (USC_WRITTEN,):
            for m in rx.finditer(txt):
                out.append(Cite(m.group(2), m.group(1).replace("–", "-"), blk))
        for m in USC_SHORT.finditer(txt):
            out.append(Cite(m.group(1), m.group(2).replace("–", "-").rstrip("."), blk))
    return out


def _date(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def _section_text(xml: str) -> str:
    """The statutory text of a section: notes and the source credit left out."""
    try:
        el = etree.fromstring(xml.encode())
    except etree.XMLSyntaxError:
        return ""
    for bad in el.xpath("//*[local-name()='notes' or local-name()='note' or local-name()='sourceCredit']"):
        bad.getparent().remove(bad)
    return _norm("".join(el.itertext()))


def _norm(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def amended_target(el) -> tuple[str, str] | None:
    """The Code section an amending instruction applies to: the nearest enclosing
    level whose own chapeau or content says "is amended" and cites a section."""
    n = el
    while n is not None:
        if isinstance(n.tag, str):
            for blk in [c for c in n if isinstance(c.tag, str) and c.tag in (U("chapeau"), U("content"))]:
                if blk.find(U("amendingAction")) is None or not any(
                        a.get("type") == "amend" for a in blk.iter(U("amendingAction"))):
                    continue
                for r in blk.iter(U("ref")):
                    m = USC_HREF.match(r.get("href", ""))
                    if m:
                        return m.group(1), m.group(2).replace("–", "-")
                txt = _own_text(blk)
                m = USC_WRITTEN.search(txt)
                if m:
                    return m.group(2), m.group(1).replace("–", "-")
                m = USC_SHORT.search(txt)
                if m:
                    return m.group(1), m.group(2).rstrip(".")
        n = n.getparent()
    return None


def check(draft: Draft, uscode: USCode | None, statutes: Statutes | None, *, since: str | None,
          max_sections: int = 200) -> dict:
    """Add citation warnings to `draft`. `since` is the date of the source text."""
    root = draft.tree.getroot()
    since_d = _date(since)
    report = {"usc_sections": 0, "public_laws": 0, "checked_at": dt.date.today().isoformat(), "errors": 0}

    if uscode is not None:
        by_section: dict[tuple[str, str], list[Cite]] = defaultdict(list)
        for c in collect_usc(root):
            by_section[(c.title, c.section)].append(c)
        report["usc_sections"] = len(by_section)
        current_text: dict[tuple[str, str], str | None] = {}
        for (title, section), cites in list(by_section.items())[:max_sections]:
            first = cites[0].el
            cite = f"{title} U.S.C. {section}"
            h = uscode.history(title, section)
            if h.status == 404:
                add_warning(draft, first, Warning("citation-missing", f"{cite} is not in the United States Code as published now.", excerpt=cite))
                continue
            if h.status != 200 or not h.data:
                report["errors"] += 1
                continue
            versions = h.data.get("versions") or []
            if not versions:
                continue
            latest = versions[-1]
            status = (latest.get("status") or "").lower()
            if status in GONE:
                add_warning(draft, first, Warning(
                    "citation-missing", f"{cite} is {status} (as of {latest['last_release']['label']}).", excerpt=cite))
                continue
            if since_d:
                later = [v for v in versions if v.get("text_changed") and (_date((v.get("first_release") or {}).get("currency_date")) or dt.date.min) > since_d]
                if later:
                    laws = []
                    for v in later:
                        for law in v.get("laws") or []:
                            laws.append(f"Pub. L. {law['pl_congress']}-{law['pl_num']}")
                    laws = list(dict.fromkeys(laws))
                    when = ", ".join(v["first_release"]["label"] for v in later)
                    msg = (f"{cite} ({latest.get('heading') or ''}) has changed since {since}: "
                           f"new text at release point {when}")
                    msg += f"; laws classified to it: {', '.join(laws)}." if laws else "."
                    msg += " Review this provision against current law."
                    add_warning(draft, first, Warning("citation-amended", msg, excerpt=cite))
            current_text[(title, section)] = None

        # Struck text: the quoted words of each "striking" instruction.
        for aa in list(root.iter(U("amendingAction"))):
            if aa.get("type") != "delete" or in_quoted(aa):
                continue
            nxt = aa.getnext()
            # "striking “<quotedText>…</quotedText>”" — the quotedText is the next element.
            if nxt is None or nxt.tag != U("quotedText"):
                continue
            words = _norm("".join(nxt.itertext()))
            if len(words) < 3:
                continue
            target = amended_target(aa.getparent())
            if not target:
                continue
            if target not in current_text or current_text[target] is None:
                s = uscode.section(*target)
                current_text[target] = _section_text(s.data.get("xml", "")) if s.status == 200 and s.data else ""
            body = current_text[target]
            if body and words not in body:
                add_warning(draft, _block_of(aa), Warning(
                    "struck-text-not-found",
                    f"The words “{words}” are not in {target[0]} U.S.C. {target[1]} as it reads now; "
                    "the provision may have been amended since this text was written.", excerpt=words))

    if statutes is not None:
        seen = set()
        for r in root.iter(U("ref")):
            m = PL_HREF.match(r.get("href", ""))
            if not m:
                continue
            cong, num = int(m.group(1)), int(m.group(2))
            before = _text_before(r)
            sm = PL_SECTION.search(before)
            sec = sm.group(1) if sm else None
            key = (cong, num, sec)
            if key in seen:
                continue
            seen.add(key)
            a = statutes.law(cong, num, sec)
            if a.status != 200 or not a.data:
                if a.status not in (404,):
                    report["errors"] += 1
                continue
            amended = ((a.data.get("currency") or {}).get("amended") or {})
            latest = amended.get("latest") or {}
            when = _date(latest.get("enacted"))
            if amended.get("status") == "known_amended" and since_d and when and when > since_d:
                what = f"Section {sec} of Public Law {cong}-{num}" if sec else f"Public Law {cong}-{num}"
                add_warning(draft, _block_of(r), Warning(
                    "law-amended",
                    f"{what} has been amended since {since}; the latest amendment known is {latest.get('label')} "
                    f"({latest.get('enacted')}).", excerpt=what))
        report["public_laws"] = len(seen)
    return report


def _text_before(el, limit: int = 200) -> str:
    blk = _block_of(el)
    out = []
    for t in blk.itertext():
        out.append(t)
    full = "".join(out)
    own = "".join(el.itertext())
    i = full.find(own)
    return full[max(0, i - limit): i] if i >= 0 else ""
