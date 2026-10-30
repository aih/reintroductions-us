"""Turn a bill in USLM 2.0 into a draft for reintroduction in a later Congress.

Changed in the text: the Congress and session, the bill number (left blank), the
date, sponsor, cosponsors and referral (left blank), the identifiers, and the year
in the bill's own short titles wherever they occur. Everything else is left as it
was and marked with a warning comment where a person should look
(`REINTRO-WARNING kind: message`, directly before the element it is about).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

from lxml import etree

from .uslm import DC, U, USLM_NS

WARNING_PREFIX = "REINTRO-WARNING"
NOTE_PREFIX = "REINTRO-NOTE"
DRAFT_BANNER = "DRAFT — NOT FOR INTRODUCTION"
DRAFT_NUMBER = "____"

ORDINAL = {1: "st", 2: "nd", 3: "rd"}

# Committees whose names a later Congress no longer uses, with the Congress that
# renamed them and the new name.
RENAMED_COMMITTEES = [
    ("Committee on Oversight and Accountability", "Committee on Oversight and Government Reform", 119),
    ("Committee on Oversight and Reform", "Committee on Oversight and Government Reform", 119),
    ("Committee on Education and Labor", "Committee on Education and the Workforce", 118),
]

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RX = re.compile(rf"\b({MONTHS}) (\d{{1,2}}), ((?:19|20)\d\d)\b")
FY_RX = re.compile(r"\bfiscal years? ((?:19|20)\d\d)(?:(?:,| and| through| to|-|–)\s*(?:fiscal year )?((?:19|20)\d\d))*", re.I)
FY_IN_NAME = re.compile(r"(Act|Appropriations|Authorization)[^.;]{0,40}\bfor\s*$", re.I)
TAXABLE_RX = re.compile(rf"\btaxable years? (beginning|ending) (after|before|on or after|on or before) ({MONTHS}) \d{{1,2}}, ((?:19|20)\d\d)", re.I)
DEADLINE_RX = re.compile(rf"\b(not later than|no later than|before|by|beginning on|ending on|after|on or after|until|through|effective)\s+({MONTHS}) (\d{{1,2}}), ((?:19|20)\d\d)", re.I)
RETRO_RX = re.compile(r"\bif this Act is enacted after\b", re.I)
SHORT_TITLE_YEAR = re.compile(r"(\bof )((?:19|20)\d\d)\s*$")

TEXT_ELEMENTS = ("content", "chapeau", "continuation", "p", "heading", "recital")
BLOCK_TEXT_TAGS = {U(t) for t in ("content", "chapeau", "continuation", "heading", "num")}
LEVEL_TAGS = {U(t) for t in ("division", "title", "subtitle", "part", "subpart", "chapter", "subchapter", "section",
                             "subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "subitem",
                             "appropriations", "level")}


@dataclass
class Warning:
    kind: str
    message: str
    where: str | None = None     # identifier of the nearest enclosing level
    excerpt: str = ""

    def comment_text(self) -> str:
        return f" {WARNING_PREFIX} {self.kind}: {_comment_safe(self.message)} "


@dataclass
class Draft:
    tree: etree._ElementTree
    warnings: list[Warning] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)

    def warnings_as_dicts(self):
        return [asdict(w) for w in self.warnings]


def _comment_safe(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace("--", "—")


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ORDINAL.get(n % 10, 'th')}"


def congress_start(congress: int) -> dt.date:
    """January 3 of the Congress's first year (the 20th Amendment's default)."""
    return dt.date(2 * congress + 1787, 1, 3)


def fiscal_year(d: dt.date) -> int:
    return d.year + 1 if d.month >= 10 else d.year


def text_of(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def nearest_identifier(el) -> str | None:
    n = el
    while n is not None:
        if isinstance(n.tag, str) and n.get("identifier"):
            return n.get("identifier")
        n = n.getparent()
    return None


def in_quoted(el) -> bool:
    return any(a.tag == U("quotedContent") for a in el.iterancestors())


def add_warning(draft: Draft, el, w: Warning):
    """Insert the warning as a comment directly before `el` and record it."""
    w.where = w.where or nearest_identifier(el)
    # A warning about a level's text goes before the level, so it does not come
    # between the level's number and its words.
    parent = el.getparent()
    if parent is not None and el.tag in BLOCK_TEXT_TAGS and parent.tag in LEVEL_TAGS:
        el = parent
    comment = etree.Comment(w.comment_text())
    if el.getparent() is None:
        el.insert(0, comment)
    else:
        # Directly before `el`, after any warnings already placed there.
        el.addprevious(comment)
    draft.warnings.append(w)


def _set_text(el, text):
    if el is not None:
        el.text = text
        for c in list(el):
            el.remove(c)


def _first(root, path):
    r = root.find(path, {"u": USLM_NS, "dc": "http://purl.org/dc/elements/1.1/"})
    return r


def reintroduce(tree: etree._ElementTree, *, to_congress: int, source: dict,
                introduction_date: dt.date | None = None, chamber: str | None = None) -> Draft:
    """`source` carries what is known about the bill being reintroduced:
    bill_id, congress, bill_type, number, version, date (YYYY-MM-DD), title."""
    root = tree.getroot()
    draft = Draft(tree, source=source)
    intro = introduction_date or congress_start(to_congress)
    bill_type = source.get("bill_type") or "hr"
    if chamber == "senate" and bill_type.startswith("h"):
        bill_type = "s" + bill_type[1:] if bill_type != "hr" else "s"
    elif chamber == "house" and bill_type.startswith("s"):
        bill_type = "h" + bill_type[1:] if bill_type != "s" else "hr"
    senate = bill_type.startswith("s")
    old_base = None
    for e in root.iter():
        if isinstance(e.tag, str) and e.get("identifier", "").startswith("/us/"):
            m = re.match(r"(/us/(?:bill|resolution)/\d+/[a-z]+/\d+)", e.get("identifier"))
            if m:
                old_base = m.group(1)
                break
    kind = "bill" if bill_type in ("hr", "s") else "resolution"
    new_base = f"/us/{kind}/{to_congress}/{bill_type}/draft"

    # -- banner and provenance
    banner = etree.Comment(
        f" {DRAFT_BANNER}. Prepared by reintroductions from {source.get('citation', source.get('bill_id'))}"
        f" (version {str(source.get('version', '')).upper()}, {source.get('date', 'date unknown')}) for the {ordinal(to_congress)} Congress"
        f" on {dt.date.today().isoformat()}. Warnings are marked {WARNING_PREFIX}. ")
    root.insert(0, banner)

    # -- meta
    meta = root.find(U("meta"))
    if meta is not None:
        stage = "IS" if senate else "IH"
        typ_short = {"hr": "HR", "s": "S"}.get(bill_type, bill_type.upper())
        t = meta.find(DC("title"))
        if t is not None:
            t.text = re.sub(r"^\d+ \S+ \d+ \S*:", f"{to_congress} {typ_short} {DRAFT_NUMBER} {stage}:", t.text or "")
        for c in meta.findall(U("citableAs")):
            meta.remove(c)
        dn = meta.find(U("docNumber"))
        if dn is not None:
            dn.text = DRAFT_NUMBER
            dn.addnext(etree.Element(U("citableAs")))
            dn.getnext().text = f"{to_congress} {typ_short} {DRAFT_NUMBER} {stage}"
        ds = meta.find(U("docStage"))
        if ds is not None:
            ds.text = "Introduced in Senate" if senate else "Introduced in House"
        cc = meta.find(U("currentChamber"))
        if cc is not None:
            cc.text = "SENATE" if senate else "HOUSE"
        for tag, val in ((U("congress"), str(to_congress)), (U("session"), "1")):
            e = meta.find(tag)
            if e is not None:
                e.text = val
        pd = meta.find(U("processedDate"))
        if pd is not None:
            pd.text = dt.date.today().isoformat()
        rel = etree.SubElement(meta, U("relatedDocument"), role="reintroductionOf")
        if source.get("href"):
            rel.set("href", source["href"])
        rel.text = source.get("citation", source.get("bill_id", ""))

    # -- preface
    pre = root.find(U("preface"))
    if pre is not None:
        sl = pre.find(U("slugLine"))
        if sl is not None:
            sl.text = f"•{bill_type.upper()} {DRAFT_NUMBER} {'IS' if senate else 'IH'}"
        c = pre.find(U("congress"))
        if c is not None:
            c.set("value", str(to_congress))
            _set_text(c, f"{ordinal(to_congress)} CONGRESS")
        s = pre.find(U("session"))
        if s is not None:
            s.set("value", "1")
            _set_text(s, "1st Session")
        dn = pre.find(U("docNumber"))
        if dn is not None:
            dn.text = DRAFT_NUMBER
        cc = pre.find(U("currentChamber"))
        if cc is not None:
            cc.set("value", "SENATE" if senate else "HOUSE")
            _set_text(cc, "IN THE SENATE OF THE UNITED STATES" if senate else "IN THE HOUSE OF REPRESENTATIVES")
        for a in pre.findall(U("action")):
            pre.remove(a)
        action = etree.SubElement(pre, U("action"))
        d = etree.SubElement(action, U("date"))
        d.text = "_______________"
        desc = etree.SubElement(action, U("actionDescription"))
        sp = etree.SubElement(desc, U("sponsor"))
        sp.text = "M__. _______"
        what = "bill" if kind == "bill" else "resolution"
        sp.tail = f" introduced the following {what}; which was referred to the "
        cm = etree.SubElement(desc, U("committee"))
        cm.text = "Committee on _______________"
        add_warning(draft, action, Warning(
            "chamber-header",
            "The bill number, date of introduction, sponsor, cosponsors and committee referral "
            "are assigned at introduction and are left blank.", where=None))
        draft.changes.append(f"Congress set to the {ordinal(to_congress)}, 1st session; number, date, sponsor and referral left blank")

    # -- identifiers
    if old_base:
        for e in root.iter():
            if not isinstance(e.tag, str):
                continue
            i = e.get("identifier")
            if i and i.startswith(old_base):
                e.set("identifier", new_base + i[len(old_base):])

    # -- short titles
    year = intro.year
    _update_short_titles(draft, root, year)

    # -- warnings from the text
    _scan_text(draft, root, intro, to_congress)
    return draft


def _update_short_titles(draft: Draft, root, year: int):
    replacements: dict[str, str] = {}
    for st in root.iter(U("shortTitle")):
        if in_quoted(st):
            continue
        old = text_of(st)
        m = SHORT_TITLE_YEAR.search(old)
        if not m or int(m.group(2)) == year:
            continue
        new = old[: m.start(2)] + str(year) + old[m.end(2):]
        replacements[old] = new
    if not replacements:
        return
    # Every occurrence of the title in the text, inside quoted content too: the
    # bill's own name does not change meaning when it is quoted.
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in ("text", "tail"):
            v = getattr(el, attr)
            if not v:
                continue
            nv = v
            for old, new in replacements.items():
                nv = nv.replace(old, new)
            if nv != v:
                setattr(el, attr, nv)
    for st in root.iter(U("shortTitle")):
        if in_quoted(st):
            continue
        new = text_of(st)
        if new in replacements.values():
            old = next(k for k, v in replacements.items() if v == new)
            target = st
            while target.getparent() is not None and target.tag not in (U("content"), U("chapeau"), U("p")):
                target = target.getparent()
            add_warning(draft, target, Warning(
                "short-title-year", f"Short title changed from “{old}” to “{new}”, here and wherever the bill names itself.",
                excerpt=new))
    for old, new in replacements.items():
        draft.changes.append(f"Short title “{old}” → “{new}”")


def _scan_text(draft: Draft, root, intro: dt.date, to_congress: int):
    fy_now = fiscal_year(intro)
    for el in list(root.iter(*(U(t) for t in TEXT_ELEMENTS))):
        if any(a.tag in (U("preface"), U("meta"), U("toc")) for a in el.iterancestors()):
            continue
        # Only the element's own text runs, so a warning is reported once, on the
        # innermost element holding the words.
        own = "".join(_own_text(el))
        if not own.strip():
            continue
        quoted = in_quoted(el)
        found: list[Warning] = []

        for m in FY_RX.finditer(own):
            if FY_IN_NAME.search(own[: m.start()]):
                continue
            years = [int(y) for y in re.findall(r"(?:19|20)\d\d", m.group(0))]
            past = [y for y in years if y <= fy_now]
            if past:
                found.append(Warning(
                    "fiscal-year-past",
                    f"“{m.group(0)}”: fiscal year {', '.join(map(str, past))} will have begun by "
                    f"{intro.isoformat()}. Move the period if the bill sets it.", excerpt=m.group(0)))
            else:
                found.append(Warning(
                    "date-set-by-bill",
                    f"“{m.group(0)}”: a fiscal-year period. Most reintroductions move it forward one or two years.",
                    excerpt=m.group(0)))
        for m in TAXABLE_RX.finditer(own):
            found.append(Warning(
                "date-set-by-bill",
                f"“{m.group(0)}”: the taxable years the bill applies to. Most reintroductions move the year forward.",
                excerpt=m.group(0)))
        for m in DEADLINE_RX.finditer(own):
            try:
                d = dt.datetime.strptime(f"{m.group(2)} {m.group(3)} {m.group(4)}", "%B %d %Y").date()
            except ValueError:
                continue
            if TAXABLE_RX.search(own[max(0, m.start() - 40): m.end()]):
                continue
            kind = "date-past" if d < intro else "date-set-by-bill"
            msg = (f"“{m.group(0)}” is before {intro.isoformat()}." if d < intro
                   else f"“{m.group(0)}”: a date the bill sets.") + " Check it against the new timetable."
            found.append(Warning(kind, msg, excerpt=m.group(0)))
        if RETRO_RX.search(own):
            found.append(Warning("date-set-by-bill",
                                 "A retroactive effective date that depends on when the bill is enacted.",
                                 excerpt=RETRO_RX.search(own).group(0)))
        for old, new, cong in RENAMED_COMMITTEES:
            if old in own and to_congress >= cong:
                found.append(Warning(
                    "renamed-committee",
                    f"“{old}” is called the “{new}” from the {ordinal(cong)} Congress.", excerpt=old))
        if el.tag == U("heading") and re.fullmatch(r"\s*(findings?|findings and purposes?|sense of congress)\.?\s*", own, re.I) and not quoted:
            found.append(Warning("findings", "Findings: statistics, dates and events may need updating.",
                                 excerpt=own.strip()))

        for w in found:
            target = el.getparent() if el.tag == U("heading") else el
            add_warning(draft, target, w)


def _own_text(el):
    """Text directly in `el` and in its inline descendants, not in nested blocks."""
    blocks = {U(t) for t in TEXT_ELEMENTS} | {U("quotedContent")}
    if el.text:
        yield el.text
    for c in el:
        if isinstance(c.tag, str) and c.tag not in blocks:
            yield from _own_text_inline(c, blocks)
        if c.tail:
            yield c.tail


def _own_text_inline(el, blocks):
    if el.text:
        yield el.text
    for c in el:
        if isinstance(c.tag, str) and c.tag not in blocks:
            yield from _own_text_inline(c, blocks)
        if c.tail:
            yield c.tail


def warnings_in(tree) -> list[tuple[str, str]]:
    """Read the warnings back out of a draft: (kind, message) per comment."""
    out = []
    for c in tree.getroot().iter(etree.Comment):
        t = (c.text or "").strip()
        if t.startswith(WARNING_PREFIX):
            kind, _, msg = t[len(WARNING_PREFIX):].strip().partition(":")
            out.append((kind.strip(), msg.strip()))
    return out
