"""Convert a bill or resolution in GPO's bill DTD XML to USLM 2.0.

The output follows GPO's own conversions of pre-enrollment bill versions in
usgpo/uslm `bill-version-samples-september-2024` (schema `uslm-2.1.0.xsd`):
`<meta>`, `<preface>`, `<main>` with `<longTitle>` and `<enactingFormula>`, levels
carrying `@identifier` (`/us/bill/116/hr/2157/tVII/s701/a/1`) outside quoted
content, `<num value>`, `<heading>`, `<chapeau>`/`<content>`/`<continuation>`,
`<quotedContent>`, `<quotedText>`, `<amendingAction>`, `<shortTitle>`, and `<ref>`
for every cross reference.

An element this module does not know keeps its text inside
`<inline class="dtd-{name}">` and is listed in `Conversion.unknown`.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from lxml import etree

from . import billdtd

USLM_NS = "http://schemas.gpo.gov/xml/uslm"
DC_NS = "http://purl.org/dc/elements/1.1/"
HTML_NS = "http://www.w3.org/1999/xhtml"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
NSMAP = {None: USLM_NS, "dc": DC_NS, "html": HTML_NS, "uslm": USLM_NS, "xsi": XSI_NS}
SCHEMA_LOCATION = "http://schemas.gpo.gov/xml/uslm uslm-2.1.0.xsd"
PROCESSED_BY = "reintroductions bill DTD to USLM converter"


def U(tag: str) -> str:
    return f"{{{USLM_NS}}}{tag}"


def DC(tag: str) -> str:
    return f"{{{DC_NS}}}{tag}"


# Levels above the section, with their identifier prefix and printed designator.
BIG_LEVELS = {
    "division": ("d", "DIVISION {}—"),
    "title": ("t", "TITLE {}—"),
    "subtitle": ("st", "Subtitle {}—"),
    "part": ("pt", "PART {}—"),
    "subpart": ("spt", "Subpart {}—"),
    "chapter": ("ch", "CHAPTER {}—"),
    "subchapter": ("sch", "SUBCHAPTER {}—"),
}
SMALL_LEVELS = ["subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "subitem"]
LEVELS = set(BIG_LEVELS) | {"section"} | set(SMALL_LEVELS)
TEXT_BEARING_INLINE = {
    "italic": "i", "bold": "b", "superscript": "sup", "subscript": "sub",
}
TYPE_LABELS = {
    "hr": ("House Bill", "H. R.", "HR"), "s": ("Senate Bill", "S.", "S"),
    "hres": ("House Simple Resolution", "H. RES.", "HRES"), "sres": ("Senate Simple Resolution", "S. RES.", "SRES"),
    "hjres": ("House Joint Resolution", "H. J. RES.", "HJRES"), "sjres": ("Senate Joint Resolution", "S. J. RES.", "SJRES"),
    "hconres": ("House Concurrent Resolution", "H. CON. RES.", "HCONRES"), "sconres": ("Senate Concurrent Resolution", "S. CON. RES.", "SCONRES"),
}
STAGE_LABELS = {
    "ih": "Introduced in House", "is": "Introduced in Senate", "rh": "Reported in House",
    "rs": "Reported in Senate", "eh": "Engrossed in House", "es": "Engrossed in Senate",
    "rfs": "Referred in Senate", "rfh": "Referred in House", "pcs": "Placed on Calendar Senate",
    "pch": "Placed on Calendar House", "enr": "Enrolled Bill",
}
ENACTING = {
    "bill": "Be it enacted by the Senate and House of Representatives of the United States of America in Congress assembled, ",
    "hjres": "Resolved by the Senate and House of Representatives of the United States of America in Congress assembled, ",
    "sjres": "Resolved by the Senate and House of Representatives of the United States of America in Congress assembled, ",
}

AMENDING_WORDS = [
    (re.compile(r"\b(is|are) (hereby )?(further )?amended\b"), "amend"),
    (re.compile(r"\bstriking\b"), "delete"),
    (re.compile(r"\binserting\b"), "insert"),
    (re.compile(r"\bredesignating\b"), "redesignate"),
    (re.compile(r"\badding\b(?= at the end| after| before)"), "add"),
    (re.compile(r"\bis repealed\b"), "repeal"),
]
QUOTED_TEXT_BEFORE = re.compile(r"(striking|inserting|insert|strike|the term|the words?|after|before)\W*$", re.I)


@dataclass
class Conversion:
    tree: etree._ElementTree
    unknown: dict[str, int] = field(default_factory=dict)
    identifiers: int = 0


def roman_or_value(enum_text: str) -> str:
    v = enum_text.strip()
    v = re.sub(r"^(SECTION|SEC\.|TITLE|DIVISION|Subtitle|PART|Subpart|CHAPTER|SUBCHAPTER)\s+", "", v, flags=re.I)
    return v.strip().strip("().—–-").rstrip(".").strip()


class _Converter:
    def __init__(self, src_root, bill_type: str | None, congress: int | None, number: int | None, version: str | None):
        self.src = src_root
        self.bill_type = bill_type or "hr"
        self.congress = congress
        self.number = number
        self.version = version
        self.unknown: dict[str, int] = {}
        self.identifiers = 0
        kind = "bill" if self.bill_type in ("hr", "s") else "resolution"
        self.base = f"/us/{kind}/{congress}/{self.bill_type}/{number}"

    # ---------------------------------------------------------------- document

    def convert(self) -> etree._Element:
        is_res = self.src.tag == "resolution"
        root = etree.Element(U("resolution" if is_res else "bill"), nsmap=NSMAP)
        root.set(f"{{{XSI_NS}}}schemaLocation", SCHEMA_LOCATION)
        root.set("{http://www.w3.org/XML/1998/namespace}lang", "en")
        if self.src.get("dms-id"):
            root.set("id", self.src.get("dms-id"))
        root.append(self.meta())
        root.append(self.preface())
        main = etree.SubElement(root, U("main"))
        body = self.src.find("legis-body")
        if body is None:
            body = self.src.find("resolution-body")
        if body is not None:
            if body.get("id"):
                main.set("id", body.get("id"))
            if body.get("style"):
                main.set("styleType", body.get("style"))
        main.append(self.long_title())
        pre = self.src.find("preamble")
        if pre is not None:
            main.append(self.preamble(pre))
        ef = self.enacting_formula()
        if ef is not None:
            main.append(ef)
        if body is not None:
            for child in body:
                self.block(child, main, path=self.base, in_quote=False)
        etree.SubElement(root, U("endMarker")).text = "○"
        return root

    def meta(self):
        m = etree.Element(U("meta"))
        dctype, label, short = TYPE_LABELS.get(self.bill_type, ("Bill", self.bill_type.upper(), self.bill_type.upper()))
        stage = (self.version or "").upper()
        ot = self._official_title_text()
        etree.SubElement(m, DC("title")).text = f"{self.congress} {short} {self.number} {stage}: {ot}".strip()
        etree.SubElement(m, DC("type")).text = dctype
        etree.SubElement(m, U("docNumber")).text = str(self.number)
        for c in (f"{self.congress} {short} {self.number} {stage}", f"{self.congress}{self.bill_type}{self.number}{self.version or ''}",
                  f"{self.congress} {label} {self.number} {stage}"):
            etree.SubElement(m, U("citableAs")).text = c.strip()
        etree.SubElement(m, U("docStage")).text = STAGE_LABELS.get(self.version or "", self.src.get("bill-stage") or self.src.get("resolution-stage") or "")
        etree.SubElement(m, U("currentChamber")).text = "SENATE" if self.bill_type.startswith("s") else "HOUSE"
        etree.SubElement(m, DC("creator")).text = "United States Senate" if self.bill_type.startswith("s") else "United States House of Representatives"
        etree.SubElement(m, U("processedBy")).text = PROCESSED_BY
        etree.SubElement(m, U("processedDate")).text = dt.date.today().isoformat()
        etree.SubElement(m, DC("publisher")).text = "United States Government Publishing Office"
        etree.SubElement(m, DC("format")).text = "text/xml"
        etree.SubElement(m, DC("language")).text = "EN"
        etree.SubElement(m, DC("rights")).text = "Pursuant to Title 17 Section 105 of the United States Code, this file is not subject to copyright protection and is in the public domain."
        etree.SubElement(m, U("congress")).text = str(self.congress)
        sess = self.src.find("form/session")
        if sess is not None and (mm := re.search(r"\d+", billdtd.text_of(sess))):
            etree.SubElement(m, U("session")).text = mm.group(0)
        etree.SubElement(m, U("publicPrivate")).text = self.src.get("public-private", "public")
        return m

    def _official_title_text(self) -> str:
        ot = self.src.find("form/official-title")
        return billdtd.text_of(ot) if ot is not None else ""

    def preface(self):
        p = etree.Element(U("preface"))
        form = self.src.find("form")
        _, label, short = TYPE_LABELS.get(self.bill_type, ("", self.bill_type.upper(), self.bill_type.upper()))
        etree.SubElement(p, U("slugLine")).text = f"•{short} {self.number} {(self.version or '').upper()}"
        if form is None:
            return p
        for el in form:
            if not isinstance(el.tag, str):
                continue
            t = el.tag
            if t == "distribution-code":
                d = etree.SubElement(p, U("distributionCode"), display=el.get("display", "yes"))
                d.text = billdtd.text_of(el)
            elif t == "congress":
                txt = billdtd.text_of(el)
                c = etree.SubElement(p, U("congress"), value=re.sub(r"\D", "", txt) or str(self.congress))
                c.text = txt
            elif t == "session":
                txt = billdtd.text_of(el)
                c = etree.SubElement(p, U("session"), value=re.sub(r"\D", "", txt))
                c.text = txt
            elif t == "legis-num":
                etree.SubElement(p, DC("type")).text = label + " "
                etree.SubElement(p, U("docNumber")).text = str(self.number)
            elif t == "current-chamber":
                txt = billdtd.text_of(el)
                c = etree.SubElement(p, U("currentChamber"), value="SENATE" if "SENATE" in txt.upper() else "HOUSE")
                c.text = txt
            elif t == "official-title":
                etree.SubElement(p, DC("title")).text = billdtd.text_of(el)
            elif t == "action":
                p.append(self.action(el))
            elif t in ("legis-type", "associated-doc", "calendar", "relating-to"):
                pass
            else:
                self._unknown(t)
        return p

    def action(self, el):
        a = etree.Element(U("action"))
        ad = el.find("action-date")
        if ad is not None:
            d = etree.SubElement(a, U("date"))
            raw = ad.get("date") or ""
            if len(raw) == 8:
                d.set("date", f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")
            d.text = billdtd.text_of(ad)
        desc = el.find("action-desc")
        if desc is not None:
            ad_el = etree.SubElement(a, U("actionDescription"))
            self.inline_content(desc, ad_el, in_quote=False, amending=False)
        return a

    def long_title(self):
        lt = etree.Element(U("longTitle"))
        legis_type = self.src.find("form/legis-type")
        etree.SubElement(lt, U("docTitle")).text = billdtd.text_of(legis_type) if legis_type is not None else (
            "RESOLUTION" if self.src.tag == "resolution" else "A BILL")
        ot = self.src.find("form/official-title")
        o = etree.SubElement(lt, U("officialTitle"))
        if ot is not None:
            self.inline_content(ot, o, in_quote=False, amending=False)
        return lt

    def preamble(self, pre):
        out = etree.Element(U("preamble"))
        for w in pre:
            if not isinstance(w.tag, str):
                continue
            if w.tag != "whereas":
                self._unknown(w.tag)
                continue
            r = etree.SubElement(out, U("recital"))
            kids = [k for k in w if isinstance(k.tag, str)]
            if all(k.tag == "text" for k in kids):
                for t in kids:
                    self.inline_content(t, r, in_quote=False, amending=False)
                continue
            for k in kids:
                if k.tag == "text":
                    p_ = etree.SubElement(r, U("p"), {"class": "inline"})
                    self.inline_content(k, p_, in_quote=False, amending=False)
                else:
                    self.block(k, r, path=None, in_quote=False)
        return out

    def enacting_formula(self):
        if self.src.tag == "resolution":
            if self.bill_type in ENACTING:
                ef = etree.Element(U("resolvingClause"))
                etree.SubElement(ef, U("i")).text = ENACTING[self.bill_type]
                return ef
            return None
        ef = etree.Element(U("enactingFormula"))
        etree.SubElement(ef, U("i")).text = ENACTING["bill"]
        return ef

    # ---------------------------------------------------------------- body

    def block(self, el, parent, path: str, in_quote: bool):
        if not isinstance(el.tag, str):
            return
        t = el.tag
        if t in LEVELS:
            self.level(el, parent, path, in_quote)
        elif t == "quoted-block":
            self.quoted_block(el, parent, path)
        elif t in ("text",):
            c = etree.SubElement(parent, U("content"))
            self.inline_content(el, c, in_quote, amending=not in_quote)
        elif t.startswith("appropriations-"):
            a = etree.SubElement(parent, U("appropriations"), level=t.split("-", 1)[1])
            if el.get("id"):
                a.set("id", el.get("id"))
            header = el.find("header")
            if header is not None:
                h = etree.SubElement(a, U("heading"))
                self.inline_content(header, h, in_quote, amending=False)
            self.children(el, a, path, in_quote)
        elif t == "toc":
            self.toc(el, parent)
        elif t == "table":
            parent.append(self.table(el))
        elif t in ("after-quoted-block",):
            i = etree.SubElement(parent, U("inline"), role="after-quoted-block")
            self.inline_content(el, i, in_quote, amending=False)
        elif t in ("header", "enum"):
            pass
        elif t == "legis-comment":
            pass
        else:
            self._unknown(t)
            i = etree.SubElement(parent, U("inline"), {"class": f"dtd-{t}"})
            self.inline_content(el, i, in_quote, amending=False)

    def level(self, el, parent, path: str, in_quote: bool):
        t = el.tag
        lv = etree.SubElement(parent, U(t))
        enum = el.find("enum")
        value = roman_or_value(enum.text or "") if enum is not None and enum.text else ""
        child_path = path
        if not in_quote and value and path is not None:
            if t in BIG_LEVELS:
                child_path = f"{path}/{BIG_LEVELS[t][0]}{value}"
            elif t == "section":
                child_path = f"{path}/s{value}"
            elif re.search(r"/s\d", path[len(self.base):]) or "/resolution/" in self.base:
                child_path = f"{path}/{value}"
            else:
                # A paragraph outside any section (in an appropriations title) has
                # no address of its own.
                child_path = None
            if child_path:
                lv.set("identifier", child_path)
                self.identifiers += 1
        if not in_quote and t == "section" and not value:
            lv.set("class", "inline")
        if el.get("id"):
            lv.set("id", el.get("id"))
        if el.get("commented") == "yes":
            lv.set("status", "proposed")
        if enum is not None and (enum.text or "").strip():
            n = etree.SubElement(lv, U("num"), value=value)
            n.text = self.designator(t, enum.text.strip(), el, in_quote)
        header = el.find("header")
        if header is not None:
            h = etree.SubElement(lv, U("heading"))
            if t in BIG_LEVELS or t == "section":
                self.inline_content(header, h, in_quote, amending=False)
                if t == "section" and h.text is not None and not in_quote:
                    pass
            else:
                sc = etree.SubElement(h, U("inline"), {"class": "smallCaps"})
                self.inline_content(header, sc, in_quote, amending=False)
                sc.tail = ".—"
        self.children(el, lv, child_path, in_quote)
        return lv

    def children(self, el, out, path: str, in_quote: bool):
        """The body of a level: `<text>` becomes `<chapeau>` before sub-levels,
        `<content>` when none follow, `<continuation>` after them. A quoted block
        or table of contents that follows a `<content>` goes inside it."""
        kids = [c for c in el if isinstance(c.tag, str) and c.tag not in ("enum", "header")]
        seen_level = False
        consumed: set[int] = set()
        for i, c in enumerate(kids):
            if i in consumed:
                continue
            if c.tag in ("text", "continuation-text"):
                if seen_level or c.tag == "continuation-text":
                    tag = "continuation"
                elif any(_is_level(k.tag) for k in kids[i + 1:]):
                    tag = "chapeau"
                else:
                    tag = "content"
                w = etree.SubElement(out, U(tag))
                self.inline_content(c, w, in_quote, amending=not in_quote)
                if tag == "content":
                    j = i + 1
                    while j < len(kids) and kids[j].tag in ("quoted-block", "toc", "after-quoted-block"):
                        if kids[j].tag == "toc":
                            self.toc(kids[j], w)
                        elif kids[j].tag == "after-quoted-block":
                            self.block(kids[j], w, path, in_quote)
                        else:
                            self.quoted_block(kids[j], w, path)
                        consumed.add(j)
                        j += 1
            else:
                if _is_level(c.tag):
                    seen_level = True
                self.block(c, out, path, in_quote)

    def designator(self, tag: str, enum_text: str, el, in_quote: bool) -> str:
        e = enum_text.strip()
        q = "“" if in_quote else ""
        if tag == "section":
            first = el.get("section-type") == "section-one"
            if in_quote:
                return f"{q}SEC. {e.rstrip('.')}. "
            return f"SECTION {e.rstrip('.')}. " if first else f"SEC. {e.rstrip('.')}. "
        if tag in BIG_LEVELS:
            v = roman_or_value(e)
            return q + BIG_LEVELS[tag][1].format(v)
        return f"{q}{e} "

    def quoted_block(self, el, parent, path: str):
        qc = etree.SubElement(parent, U("quotedContent"))
        if el.get("id"):
            qc.set("id", el.get("id"))
        if el.get("style"):
            qc.set("styleType", el.get("style"))
        after = None
        for c in el:
            if not isinstance(c.tag, str):
                continue
            if c.tag == "after-quoted-block":
                after = c
                continue
            self.block(c, qc, path, in_quote=True)
        # The closing quotation mark goes on the last text in the block.
        texts = [t for t in qc.iter(U("content"), U("continuation"), U("chapeau"), U("heading")) if True]
        if texts:
            last = texts[-1]
            _append_text(last, "”")
        if after is not None:
            i = etree.Element(U("inline"), role="after-quoted-block")
            self.inline_content(after, i, in_quote=False, amending=False)
            idx = list(parent).index(qc)
            parent.insert(idx + 1, i)
        return qc

    # ---------------------------------------------------------------- inline

    def inline_content(self, src, dst, in_quote: bool, amending: bool):
        """Copy `src`'s mixed content into `dst`, mapping inline elements."""
        _append_text(dst, src.text or "")
        for ch in src:
            if not isinstance(ch.tag, str):
                _append_text(dst, ch.tail or "")
                continue
            t = ch.tag
            if t == "quote":
                before = _tail_text(dst)
                if amending and QUOTED_TEXT_BEFORE.search(before):
                    _append_text(dst, "“")
                    q = etree.SubElement(dst, U("quotedText"))
                    self.inline_content(ch, q, in_quote, amending=False)
                    q.tail = "”"
                else:
                    _append_text(dst, "“")
                    self._inline_into_tail(ch, dst, in_quote)
                    _append_text(dst, "”")
            elif t == "short-title":
                s = etree.SubElement(dst, U("shortTitle"), role="act")
                self.inline_content(ch, s, in_quote, amending=False)
            elif t == "external-xref":
                r = etree.SubElement(dst, U("ref"))
                href = xref_href(ch.get("legal-doc", ""), ch.get("parsable-cite", ""), billdtd.text_of(ch))
                if href:
                    r.set("href", href)
                self.inline_content(ch, r, in_quote, amending=False)
            elif t == "internal-xref":
                r = etree.SubElement(dst, U("ref"))
                if ch.get("idref"):
                    r.set("idref", ch.get("idref"))
                self.inline_content(ch, r, in_quote, amending=False)
            elif t in TEXT_BEARING_INLINE:
                e = etree.SubElement(dst, U(TEXT_BEARING_INLINE[t]))
                self.inline_content(ch, e, in_quote, amending)
            elif t == "term":
                e = etree.SubElement(dst, U("term"))
                self.inline_content(ch, e, in_quote, amending=False)
            elif t == "sponsor":
                e = etree.SubElement(dst, U("sponsor"))
                if ch.get("name-id"):
                    e.set(_member_attr(ch.get("name-id")), ch.get("name-id"))
                self.inline_content(ch, e, in_quote, amending=False)
            elif t == "cosponsor":
                e = etree.SubElement(dst, U("cosponsor"))
                if ch.get("name-id"):
                    e.set(_member_attr(ch.get("name-id")), ch.get("name-id"))
                self.inline_content(ch, e, in_quote, amending=False)
            elif t == "committee-name":
                e = etree.SubElement(dst, U("committee"))
                if ch.get("committee-id"):
                    e.set("committeeId", ch.get("committee-id"))
                self.inline_content(ch, e, in_quote, amending=False)
            elif t in ("enum-in-header", "act-name", "pagebreak", "nonbreaking-hyphen"):
                self._inline_into_tail(ch, dst, in_quote)
            elif t == "quoted-block":
                self.quoted_block(ch, dst, self.base)
            elif t == "after-quoted-block":
                i = etree.SubElement(dst, U("inline"), role="after-quoted-block")
                self.inline_content(ch, i, in_quote, amending=False)
            elif t == "proviso":
                e = etree.SubElement(dst, U("proviso"))
                self.inline_content(ch, e, in_quote, amending=False)
            elif t == "fraction":
                _append_text(dst, billdtd.text_of(ch))
            elif t == "footnote":
                f = etree.SubElement(dst, U("footnote"))
                self.inline_content(ch, f, in_quote, amending=False)
            elif t == "table":
                dst.append(self.table(ch))
            else:
                self._unknown(t)
                e = etree.SubElement(dst, U("inline"), {"class": f"dtd-{t}"})
                self.inline_content(ch, e, in_quote, amending)
            _append_text(dst, ch.tail or "")
        if amending:
            mark_amending_actions(dst)

    def _inline_into_tail(self, ch, dst, in_quote):
        """Copy an element's content into `dst` without a wrapper element."""
        tmp = etree.Element(U("tmp"))
        self.inline_content(ch, tmp, in_quote, amending=False)
        _append_text(dst, tmp.text or "")
        for c in list(tmp):
            tail = c.tail
            c.tail = None
            dst.append(c)
            _append_text(dst, tail or "")

    def toc(self, el, parent):
        toc = etree.SubElement(parent, U("toc"))
        for e in el.iter("toc-entry"):
            ri = etree.SubElement(toc, U("referenceItem"))
            if e.get("idref"):
                ri.set("idref", e.get("idref"))
            ri.set("role", e.get("level", "section"))
            txt = billdtd.text_of(e)
            m = re.match(r"^((?:Sec\.|SECTION|Section|TITLE|DIVISION|Subtitle|PART|Subpart|CHAPTER)\s+[\w\-–]+\.?—?)\s*(.*)$", txt)
            if m:
                etree.SubElement(ri, U("designator")).text = m.group(1) + " "
                etree.SubElement(ri, U("label")).text = m.group(2)
            else:
                etree.SubElement(ri, U("label")).text = txt

    def table(self, el):
        """GPO's CALS-style table as an XHTML table, the form USLM embeds."""
        H = lambda t: f"{{{HTML_NS}}}{t}"  # noqa: E731
        tbl = etree.Element(H("table"))
        ttitle = el.find("ttitle")
        if ttitle is not None and billdtd.text_of(ttitle):
            etree.SubElement(tbl, H("caption")).text = billdtd.text_of(ttitle)
        for section_tag, out_tag in (("thead", "thead"), ("tbody", "tbody")):
            for sect in el.iter(section_tag):
                o = etree.SubElement(tbl, H(out_tag))
                for row in sect.iter("row"):
                    tr = etree.SubElement(o, H("tr"))
                    for entry in row.iter("entry"):
                        cell = etree.SubElement(tr, H("th" if out_tag == "thead" else "td"))
                        cell.text = billdtd.text_of(entry)
        return tbl

    def _unknown(self, tag: str):
        self.unknown[tag] = self.unknown.get(tag, 0) + 1


# ---------------------------------------------------------------- helpers


def _is_level(tag: str) -> bool:
    return tag in LEVELS or tag.startswith("appropriations-")


def _member_attr(name_id: str) -> str:
    # House files carry bioguide ids (L000586); Senate files carry LIS ids (S345).
    return "bioGuideId" if re.fullmatch(r"[A-Z]\d{6}", name_id) else "senateId"


def _append_text(el, text: str):
    if not text:
        return
    kids = list(el)
    if kids:
        kids[-1].tail = (kids[-1].tail or "") + text
    else:
        el.text = (el.text or "") + text


def _tail_text(el) -> str:
    kids = list(el)
    if kids:
        return kids[-1].tail or ""
    return el.text or ""


_SUBDIV = re.compile(r"\(([A-Za-z0-9]+)\)")


def xref_href(legal_doc: str, parsable: str, text: str) -> str | None:
    """`usc/42/5302` printed "42 U.S.C. 5302(17)" → `/us/usc/t42/s5302/17`."""
    parts = parsable.split("/")
    if legal_doc == "usc" and len(parts) >= 3:
        href = f"/us/usc/t{parts[1]}/s{parts[2]}"
        tail = text.split(parts[2], 1)[1] if parts[2] in text else ""
        subs = _SUBDIV.findall(tail.split(" ")[0]) if tail else []
        for s in subs:
            href += f"/{s}"
        if "et seq" in text:
            href += "/etseq"
        return href
    if legal_doc == "usc-chapter" and len(parts) >= 3:
        return f"/us/usc/t{parts[1]}/ch{parts[2]}"
    if legal_doc == "usc-appendix" and len(parts) >= 3:
        return f"/us/usc/t{parts[1]}a/s{parts[2]}"
    if legal_doc == "public-law" and len(parts) >= 3:
        return f"/us/pl/{parts[1]}/{parts[2]}"
    if legal_doc == "statute-at-large" and len(parts) >= 3:
        return f"/us/stat/{parts[1]}/{parts[2]}"
    return None


def mark_amending_actions(el):
    """Wrap amending verbs in `<amendingAction type>` in `el`'s own text and the
    tails of its children. Text inside child elements is not touched."""

    def split(text: str):
        if not text:
            return text, []
        hits = []
        for rx, typ in AMENDING_WORDS:
            for m in rx.finditer(text):
                hits.append((m.start(), m.end(), typ))
        hits.sort()
        out = []
        pos = 0
        last_end = -1
        for s, e, typ in hits:
            if s < last_end:
                continue
            out.append((text[pos:s], typ, text[s:e]))
            pos = e
            last_end = e
        return text[pos:], out

    def rebuild(head_setter, text, insert_at):
        rest, pieces = split(text)
        if not pieces:
            return
        head_setter(pieces[0][0])
        new_nodes = []
        for i, (_, typ, word) in enumerate(pieces):
            a = etree.Element(U("amendingAction"), type=typ)
            a.text = word
            nxt = pieces[i + 1][0] if i + 1 < len(pieces) else rest
            a.tail = nxt
            new_nodes.append(a)
        for k, n in enumerate(new_nodes):
            el.insert(insert_at + k, n)

    if el.text:
        t = el.text
        rebuild(lambda s: setattr(el, "text", s), t, 0)
    for child in list(el):
        if child.tag == U("amendingAction"):
            continue
        if child.tail:
            idx = list(el).index(child)
            t = child.tail
            rebuild(lambda s, c=child: setattr(c, "tail", s), t, idx + 1)


def convert(path_or_bytes, version: str | None = None) -> Conversion:
    tree = billdtd.parse(path_or_bytes)
    src = tree.getroot()
    doc = billdtd.read_bill(path_or_bytes if not isinstance(path_or_bytes, bytes) else path_or_bytes)
    if src.tag == "resolution" and doc.bill_type is None:
        doc.bill_type = {"house-resolution": "hres", "senate-resolution": "sres"}.get(src.get("resolution-type", ""))
    c = _Converter(src, doc.bill_type, doc.congress, doc.number, version)
    root = c.convert()
    root.addprevious(etree.ProcessingInstruction("xml-stylesheet", 'type="text/css" href="uslm.css"'))
    return Conversion(etree.ElementTree(root), c.unknown, c.identifiers)


def to_bytes(tree: etree._ElementTree) -> bytes:
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", pretty_print=False)
