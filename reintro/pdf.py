"""A PDF of a draft, marked on every page as a draft not to be introduced."""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

from .render import render

CSS_PATH = Path(__file__).parent / "web" / "static" / "bill.css"

WATERMARK_CSS = """
@page {
  size: letter;
  margin: 1in 1in 0.9in 1in;
  @top-center { content: "DRAFT — NOT FOR INTRODUCTION"; font: 600 9pt sans-serif; color: #90302e; letter-spacing: 0.08em; }
  @bottom-left { content: string(source); font: 8pt sans-serif; color: #555; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 8pt sans-serif; color: #555; }
}
.watermark {
  position: fixed; top: 3.6in; left: -0.6in; width: 7.7in;
  transform: rotate(-35deg); text-align: center;
  font: 700 54pt sans-serif; color: rgba(144, 48, 46, 0.13); letter-spacing: 0.05em;
  z-index: -1;
}
.source-line { string-set: source content(); font-size: 0; height: 0; }
body { font-family: "DejaVu Serif", Georgia, serif; font-size: 11pt; }
aside.warning { break-inside: avoid; }
a { color: inherit; text-decoration: none; }
"""


def draft_pdf(tree, *, source_line: str, warnings_on: bool = True) -> bytes:
    from weasyprint import HTML  # imported here: it loads Pango at import time

    body = render(tree, link=False)
    if not warnings_on:
        import re
        body = re.sub(r'<aside class="warning.*?</aside>', "", body, flags=re.S)
    base_css = CSS_PATH.read_text() if CSS_PATH.exists() else ""
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Draft reintroduction</title><style>{base_css}{WATERMARK_CSS}</style></head>
<body class="print">
<div class="watermark">DRAFT — NOT FOR INTRODUCTION</div>
<div class="source-line">{html.escape(source_line)} · generated {dt.date.today().isoformat()}</div>
{body}
</body></html>"""
    return HTML(string=doc).write_pdf()
