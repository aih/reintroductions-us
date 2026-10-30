"""Read a bill in GPO's bill DTD XML (the form the BILLS collection publishes every
version before enrollment in).

`read_bill` returns the header facts and the citations in the body; `body_text`
returns the legislative text as plain text for comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

# Bill DTD elements whose text is structural chrome rather than statutory text.
_SKIP_IN_TEXT = {"toc", "toc-entry"}

_LEGIS_NUM = re.compile(r"(H\.\s*R\.|S\.|H\.\s*RES\.|S\.\s*RES\.|H\.\s*J\.\s*RES\.|S\.\s*J\.\s*RES\.|H\.\s*CON\.\s*RES\.|S\.\s*CON\.\s*RES\.)\s*(\d+)", re.I)
_TYPE_BY_LABEL = {
    "H.R.": "hr", "S.": "s", "H.RES.": "hres", "S.RES.": "sres",
    "H.J.RES.": "hjres", "S.J.RES.": "sjres", "H.CON.RES.": "hconres", "S.CON.RES.": "sconres",
}


@dataclass
class Citation:
    """One `<external-xref>` in the bill: what the drafter linked and where."""

    legal_doc: str          # usc, public-law, statute-at-large, act, usc-chapter, ...
    parsable_cite: str      # e.g. usc/42/1395w-4
    text: str               # the words as printed
    path: str               # enum path of the enclosing provision, e.g. "2(b)(1)"


@dataclass
class BillDoc:
    congress: int | None
    session: int | None
    bill_type: str | None
    number: int | None
    stage: str | None
    root: str               # bill, resolution, amendment-doc
    action_date: str | None  # YYYY-MM-DD
    sponsor: tuple[str, str] | None             # (name-id, printed name)
    cosponsors: list[tuple[str, str]] = field(default_factory=list)
    committees: list[tuple[str, str]] = field(default_factory=list)
    official_title: str = ""
    short_titles: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)


def parse(path_or_bytes: str | Path | bytes) -> etree._ElementTree:
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True)
    if isinstance(path_or_bytes, bytes):
        return etree.ElementTree(etree.fromstring(path_or_bytes, parser))
    return etree.parse(str(path_or_bytes), parser)


def text_of(el: etree._Element) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _first(root, xpath):
    r = root.xpath(xpath)
    return r[0] if r else None


def enum_path(el: etree._Element) -> str:
    """The designators of `el` and its ancestors, outermost first: `2(b)(1)`."""
    parts = []
    node = el
    while node is not None:
        enum = node.find("enum") if isinstance(node.tag, str) else None
        if enum is not None and node.tag != "quoted-block":
            parts.append((enum.text or "").strip().rstrip("."))
        if node.tag in ("quoted-block", "legis-body", "resolution-body"):
            break
        node = node.getparent()
    return "".join(reversed(parts))


def read_bill(path_or_bytes) -> BillDoc:
    tree = parse(path_or_bytes)
    root = tree.getroot()
    form = root.find("form")

    congress = session = number = None
    bill_type = None
    if form is not None:
        c = form.find("congress")
        if c is not None and (m := re.search(r"(\d+)", text_of(c))):
            congress = int(m.group(1))
        s = form.find("session")
        if s is not None and (m := re.search(r"(\d+)", text_of(s))):
            session = int(m.group(1))
        ln = form.find("legis-num")
        if ln is not None and (m := _LEGIS_NUM.search(text_of(ln))):
            label = re.sub(r"\s+", "", m.group(1)).upper()
            bill_type = _TYPE_BY_LABEL.get(label)
            number = int(m.group(2))

    action_date = None
    ad = _first(root, "./form/action/action-date")
    if ad is not None and (d := ad.get("date")) and len(d) == 8:
        action_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"

    sponsor = None
    sp = _first(root, "./form/action/action-desc/sponsor")
    if sp is not None:
        sponsor = (sp.get("name-id", ""), text_of(sp))
    cos = [(c.get("name-id", ""), text_of(c)) for c in root.xpath("./form/action/action-desc/cosponsor")]
    cmts = [(c.get("committee-id", ""), text_of(c)) for c in root.xpath("./form/action/action-desc/committee-name")]

    ot = _first(root, "./form/official-title")
    body = _first(root, "./legis-body|./resolution-body")
    short_titles = []
    if body is not None:
        for st in body.xpath(".//short-title[not(ancestor::quoted-block)]"):
            short_titles.append(text_of(st))

    cites = []
    for x in root.xpath(".//external-xref"):
        cites.append(Citation(
            legal_doc=x.get("legal-doc", ""),
            parsable_cite=x.get("parsable-cite", ""),
            text=text_of(x),
            path=enum_path(x.getparent()),
        ))

    return BillDoc(
        congress=congress, session=session, bill_type=bill_type, number=number,
        stage=root.get("bill-stage"), root=root.tag, action_date=action_date,
        sponsor=sponsor, cosponsors=cos, committees=cmts,
        official_title=text_of(ot) if ot is not None else "",
        short_titles=short_titles, citations=cites,
    )


def body_text(path_or_bytes) -> str:
    """The legislative body as plain text, one line per enumerated element, with the
    table of contents left out."""
    root = parse(path_or_bytes).getroot()
    body = _first(root, "./legis-body|./resolution-body")
    if body is None:
        return ""
    lines: list[str] = []

    def walk(el, buf):
        if not isinstance(el.tag, str):
            return
        if el.tag in _SKIP_IN_TEXT:
            if el.tail:
                buf.append(el.tail)
            return
        starts_line = el.find("enum") is not None or el.tag in ("text", "header", "quoted-block")
        if starts_line and buf:
            line = re.sub(r"\s+", " ", "".join(buf)).strip()
            if line:
                lines.append(line)
            buf.clear()
        if el.text:
            buf.append(el.text)
        for ch in el:
            walk(ch, buf)
        if el.tail:
            buf.append(" " + el.tail if el.tag in ("enum", "header") else el.tail)

    buf: list[str] = []
    walk(body, buf)
    line = re.sub(r"\s+", " ", "".join(buf)).strip()
    if line:
        lines.append(line)
    return "\n".join(lines)
