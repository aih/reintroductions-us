"""USLM 2.0 to HTML, for the bill page and the PDF.

Each `REINTRO-WARNING` comment becomes an `<aside class="warning">` in place.
References to the US Code and Public Laws link to the two linkedlegislation sites.
"""

from __future__ import annotations

import html
import re

from lxml import etree

from .reintroduce import WARNING_PREFIX
from .uslm import DC_NS, HTML_NS, USLM_NS

USCODE_SITE = "https://uscode.linkedlegislation.org"
STATUTES_SITE = "https://statutes.linkedlegislation.org"

LEVEL_TAGS = {"division", "title", "subtitle", "part", "subpart", "chapter", "subchapter", "section",
              "subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "subitem",
              "appropriations", "level"}
BLOCK_TEXT = {"content": "div", "chapeau": "div", "continuation": "div", "p": "p", "recital": "p"}
INLINE = {"i": "i", "b": "b", "sup": "sup", "sub": "sub", "term": "dfn", "inline": "span", "proviso": "span",
          "amendingAction": "span", "quotedText": "q", "shortTitle": "cite", "sponsor": "span",
          "cosponsor": "span", "committee": "span", "footnote": "small"}

WARNING_LABELS = {
    "chamber-header": "Left for introduction",
    "short-title-year": "Short title",
    "fiscal-year-past": "Fiscal year already begun",
    "date-set-by-bill": "Date set by the bill",
    "date-past": "Date already passed",
    "renamed-committee": "Renamed committee",
    "findings": "Findings",
    "citation-amended": "Cited law amended",
    "citation-missing": "Cited section not found",
    "struck-text-not-found": "Struck text not in current law",
    "law-amended": "Cited Public Law amended",
}


def local(tag) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else tag


def href_for(ref: str) -> str | None:
    if ref.startswith("/us/usc/"):
        return USCODE_SITE + re.sub(r"/etseq$", "", ref)
    if ref.startswith("/us/pl/") or ref.startswith("/us/stat/"):
        return STATUTES_SITE + ref
    return None


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def warning_html(text: str) -> str:
    body = text.strip()[len(WARNING_PREFIX):].strip()
    kind, _, msg = body.partition(":")
    kind = kind.strip()
    label = WARNING_LABELS.get(kind, kind)
    return (f'<aside class="warning warning--{html.escape(kind)}" role="note" data-kind="{html.escape(kind)}">'
            f'<span class="warning__kind">{esc(label)}</span> {esc(msg.strip())}</aside>')


class Renderer:
    def __init__(self, link: bool = True):
        self.link = link

    def render(self, tree) -> str:
        root = tree.getroot() if hasattr(tree, "getroot") else tree
        out: list[str] = ['<article class="uslm">']
        for c in root:
            self.node(c, out)
        out.append("</article>")
        return "".join(out)

    def children(self, el, out):
        if el.text:
            out.append(esc(el.text))
        for c in el:
            self.node(c, out)
            if c.tail:
                out.append(esc(c.tail))

    def node(self, el, out):
        if isinstance(el, etree._Comment):
            t = el.text or ""
            if t.strip().startswith(WARNING_PREFIX):
                out.append(warning_html(t))
            return
        if not isinstance(el.tag, str):
            return
        ns = el.tag.split("}")[0][1:] if "}" in el.tag else ""
        t = local(el.tag)
        if ns == HTML_NS:
            out.append(f"<{t}>")
            self.children(el, out)
            out.append(f"</{t}>")
            return
        if ns == DC_NS:
            if t == "title" and local(el.getparent().tag) == "preface":
                return  # shown as the official title in the long title
            if t == "type":
                out.append(f'<span class="doc-type">{esc(el.text)}</span>')
            return
        if t in ("meta", "endMarker"):
            return
        if t == "preface":
            out.append('<header class="preface">')
            self.children(el, out)
            out.append("</header>")
        elif t in ("slugLine",):
            return
        elif t in ("congress", "session", "currentChamber", "distributionCode", "docNumber"):
            cls = {"currentChamber": "chamber", "distributionCode": "dist"}.get(t, t)
            out.append(f'<span class="pf-{cls}">{esc("".join(el.itertext()))}</span>')
        elif t == "action":
            out.append('<div class="pf-action">')
            self.children(el, out)
            out.append("</div>")
        elif t == "date":
            out.append(f'<span class="pf-date">{esc("".join(el.itertext()))}</span> ')
        elif t == "actionDescription":
            out.append('<span class="pf-desc">')
            self.children(el, out)
            out.append("</span>")
        elif t == "main":
            out.append('<div class="main">')
            self.children(el, out)
            out.append("</div>")
        elif t == "longTitle":
            out.append('<div class="long-title">')
            self.children(el, out)
            out.append("</div>")
        elif t == "docTitle":
            out.append(f'<h2 class="doc-title">{esc("".join(el.itertext()))}</h2>')
        elif t == "officialTitle":
            out.append('<p class="official-title">')
            self.children(el, out)
            out.append("</p>")
        elif t in ("enactingFormula", "resolvingClause"):
            out.append('<p class="enacting">')
            self.children(el, out)
            out.append("</p>")
        elif t == "preamble":
            out.append('<div class="preamble">')
            self.children(el, out)
            out.append("</div>")
        elif t in LEVEL_TAGS:
            ident = el.get("identifier")
            attrs = f' id="{html.escape(ident)}"' if ident else ""
            lvl = el.get("level")
            out.append(f'<div class="lv lv-{t}{" lv-" + lvl if lvl else ""}"{attrs}>')
            self.children(el, out)
            out.append("</div>")
        elif t == "num":
            out.append(f'<span class="num">{esc("".join(el.itertext()))}</span>')
        elif t == "heading":
            out.append('<span class="heading">')
            self.children(el, out)
            out.append("</span>")
        elif t in BLOCK_TEXT:
            tag = BLOCK_TEXT[t]
            out.append(f'<{tag} class="{t}">')
            self.children(el, out)
            out.append(f"</{tag}>")
        elif t == "quotedContent":
            out.append('<div class="quoted">')
            self.children(el, out)
            out.append("</div>")
        elif t == "ref":
            h = href_for(el.get("href", "")) if self.link else None
            if h:
                out.append(f'<a class="ref" href="{html.escape(h)}">')
                self.children(el, out)
                out.append("</a>")
            else:
                out.append('<span class="ref">')
                self.children(el, out)
                out.append("</span>")
        elif t == "toc":
            out.append('<ul class="toc">')
            for ri in el:
                if local(ri.tag) == "referenceItem":
                    out.append(f'<li class="toc-{html.escape(ri.get("role", ""))}">{esc(" ".join(ri.itertext()).strip())}</li>')
            out.append("</ul>")
        elif t in INLINE:
            tag = INLINE[t]
            cls = t if t != "inline" else (el.get("class") or el.get("role") or "inline")
            out.append(f'<{tag} class="{html.escape(cls)}">')
            self.children(el, out)
            out.append(f"</{tag}>")
        else:
            out.append(f'<span class="u-{t}">')
            self.children(el, out)
            out.append("</span>")


def render(tree, link: bool = True) -> str:
    return Renderer(link=link).render(tree)


def draft_banner(tree) -> str | None:
    for c in tree.getroot().iter(etree.Comment):
        if "NOT FOR INTRODUCTION" in (c.text or ""):
            return (c.text or "").strip()
    return None


__all__ = ["render", "draft_banner", "USLM_NS"]
