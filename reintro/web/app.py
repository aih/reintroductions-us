"""The site: bill search, bill pages with the reintroduction draft, downloads."""

from __future__ import annotations

import io
import json
import os
import re
import threading
import zipfile
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import markdown
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .. import db, generate, render
from ..pregen import pdf_name
from ..linkedleg import default_clients
from ..reintroduce import ordinal
from ..version import GIT_COMMIT, SITE_VERSION

HERE = Path(__file__).parent
DOCS = Path(os.environ.get("REINTRO_DOCS", HERE.parents[1] / "docs"))
DB_PATH = os.environ.get("REINTRO_DB", "data/reintro.db")
CACHE_DIR = Path(os.environ.get("REINTRO_CACHE", "data/cache"))
CHECKS = os.environ.get("REINTRO_CHECKS", "on") != "off"
# A PDF is rendered on request only for a bill whose source text is at most this
# many bytes: WeasyPrint took 19 s and 420 MB at 0.97 MB, 150 s and 2.7 GB at 9 MB
# (docs/deploy.md). The PDFs of one zip share the same budget, each counted at
# least PDF_MIN_COST, the fixed cost of a render (about 1 s).
PDF_MAX_SOURCE = int(os.environ.get("REINTRO_PDF_MAX_SOURCE_BYTES", "1000000"))
PDF_MIN_COST = 50_000
# Renders at once; the others wait.
PDF_RENDERS = threading.Semaphore(int(os.environ.get("REINTRO_PDF_CONCURRENCY", "1")))
PAGE = 25


@asynccontextmanager
async def lifespan(_app):
    """Computes the member list in the background, so the first type-ahead does not wait for it."""
    def warm():
        try:
            members_json()
        except Exception:  # noqa: BLE001 — no database yet; the first request computes it
            pass
    threading.Thread(target=warm, daemon=True).start()
    yield


app = FastAPI(title="Reintroductions", version=SITE_VERSION, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["ordinal"] = ordinal
templates.env.globals["citation"] = db.citation
templates.env.globals["site_version"] = SITE_VERSION
templates.env.globals["git_commit"] = GIT_COMMIT


_CON_LOCK = threading.Lock()


def con():
    """The one connection. The first call can take seconds (`db.connect` adds indexes and fills
    `bills_fts_rows` in a database loaded before they existed); the others wait for it."""
    with _CON_LOCK:
        return _connect()


@lru_cache(maxsize=1)
def _connect():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return db.connect(DB_PATH)


@lru_cache(maxsize=1)
def clients():
    if not CHECKS:
        return None, None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return default_clients(str(CACHE_DIR / "http.db"))


@lru_cache(maxsize=2)
def _members(loads: tuple) -> bytes:
    return json.dumps({"members": db.members(con())}, ensure_ascii=False, separators=(",", ":")).encode()


def members_json() -> bytes:
    """`db.members` as JSON, computed once per state of the `loads` table (1.6 s over two Congresses)."""
    return _members(tuple(tuple(r) for r in con().execute("select congress, loaded_at, bills from loads order by congress")))


def latest_congress() -> int | None:
    cs = db.congresses(con())
    return cs[0] if cs else None


def bill_label(row) -> str:
    return db.citation(row["bill_type"], row["number"])


templates.env.globals["bill_label"] = bill_label
templates.env.filters["loads"] = lambda s: json.loads(s) if s else None


def _stem(term: str) -> str:
    t = term.lower()
    for suf in ("ing", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)]
    return t


def highlight(text: str | None, terms: list[str]) -> Markup:
    """`text` escaped, with each word that starts like a query term (or each quoted phrase) in <mark>."""
    if not text:
        return Markup("")
    if not terms:
        return Markup.escape(text)
    pats = [re.escape(t) if " " in t else re.escape(_stem(t)) + r"\w*" for t in terms]
    rx = re.compile(r"\b(?:" + "|".join(sorted(pats, key=len, reverse=True)) + ")", re.I)
    out, last = [], 0
    for m in rx.finditer(text):
        out += [str(Markup.escape(text[last:m.start()])), "<mark>", str(Markup.escape(m.group(0))), "</mark>"]
        last = m.end()
    out.append(str(Markup.escape(text[last:])))
    return Markup("".join(out))


def snippet_html(s: str | None) -> Markup:
    """An FTS snippet (matches between db.MARK_START and db.MARK_END) as escaped HTML with <mark>."""
    if not s or db.MARK_START not in s:
        return Markup("")
    s = re.sub(r"\s+", " ", s).strip()
    return Markup(str(Markup.escape(s)).replace(db.MARK_START, "<mark>").replace(db.MARK_END, "</mark>"))


templates.env.filters["highlight"] = highlight
templates.env.filters["snippet"] = snippet_html


def _doc(name: str) -> tuple[str, str]:
    p = DOCS / name
    if not p.exists():
        raise HTTPException(404)
    text = p.read_text()
    title = name
    if text.startswith("---"):
        _, fm, text = text.split("---", 2)
        m = re.search(r"^title:\s*(.+)$", fm, re.M)
        if m:
            title = m.group(1).strip()
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc"])
    return title, body


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response((HERE / "static" / "favicon-32.png").read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True, "congresses": db.congresses(con()), "version": SITE_VERSION, "commit": GIT_COMMIT}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    c = con()
    counts = c.execute("select congress, count(*) n, sum(enacted) enacted, sum(related_enacted is not null) related "
                       "from bills group by congress order by congress desc").fetchall()
    subjects = db.subject_list(c)
    return templates.TemplateResponse(request, "home.html", {
        "counts": counts, "latest": latest_congress(), "subjects": subjects, "congresses": db.congresses(c),
        "types": db.TYPE_LABEL, "committees": db.committee_list(c), "params": {}, "labels": {}})


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", congress: str | None = None, chamber: str | None = None,
                subject: str | None = None, sponsor: str | None = None, cosponsor: str | None = None,
                status: str | None = None, type: str | None = None, introduced_from: str | None = Query(None, alias="from"),
                introduced_to: str | None = Query(None, alias="to"), reported: str | None = None,
                passed: str | None = None, sort: str | None = None, page: int = Query(1, ge=1)):
    c = con()
    congress = int(congress) if congress and congress.isdigit() else None
    sort = sort if sort in db.SORTS and sort != "relevance" else None
    res = db.search(c, q=q, congress=congress, chamber=chamber, subject=subject or None, sponsor=sponsor or None,
                    cosponsor=cosponsor or None, status=status or None, bill_type=type or None,
                    introduced_from=_date(introduced_from), introduced_to=_date(introduced_to),
                    reported=reported or None, passed=passed or None, sort=sort, limit=PAGE, offset=(page - 1) * PAGE)
    pages = max(1, (res.total + PAGE - 1) // PAGE)
    params = {k: v for k, v in {"q": q, "congress": congress, "chamber": chamber, "subject": subject, "sponsor": sponsor,
                                "cosponsor": cosponsor, "status": status, "type": type, "from": _date(introduced_from),
                                "to": _date(introduced_to), "reported": reported, "passed": passed,
                                "sort": sort}.items() if v}
    text_query = bool(q.strip()) and db.parse_number(q) is None
    labels = {role: (db.member_name(c, v) if re.fullmatch(r"[A-Z]\d{6}", v) else None) or v
              for role, v in (("sponsor", sponsor), ("cosponsor", cosponsor)) if v}
    return templates.TemplateResponse(request, "search.html", {
        "labels": labels,
        "res": res, "params": params, "page": page, "pages": pages, "congresses": db.congresses(c),
        "subjects": db.subject_list(c, congress), "committees": db.committee_list(c, congress),
        "types": db.TYPE_LABEL, "terms": db.query_terms(q) if text_query else [],
        "text_query": text_query, "first": (page - 1) * PAGE + 1})


def _date(s: str | None) -> str | None:
    """A YYYY-MM-DD date from a form field, or None."""
    return s if s and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


@app.get("/bills/{bill_id}", response_class=HTMLResponse)
def bill_page(request: Request, bill_id: str, check: int = 1):
    c = con()
    row = db.get_bill(c, bill_id)
    if row is None:
        raise HTTPException(404, f"no bill {bill_id}")
    draft = error = None
    if not row["enacted"] and row["text_path"]:
        try:
            draft = _draft(bill_id, check=bool(check))
        except generate.NotEligible as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001 — show the page without a draft
            error = f"The draft could not be built: {e}"
    later = db.same_title_in(c, bill_id, row["congress"] + 1) if row["congress"] + 1 in db.congresses(c) else []
    ctx = {
        "b": row, "people": db.people(c, bill_id), "subjects": db.subjects(c, bill_id), "progress": db.progress(c, bill_id),
        "related_enacted": json.loads(row["related_enacted"]) if row["related_enacted"] else [],
        "enacted_as": json.loads(row["enacted_as"]) if row["enacted_as"] else None,
        "draft": draft, "draft_html": render.render(draft.tree) if draft else None, "error": error,
        "later": later, "check": check, "to_congress": row["congress"] + 1,
        "pdf_allowed": pdf_allowed(row) or bool(draft and check and _pdf_path(draft, True).exists()),
    }
    return templates.TemplateResponse(request, "bill.html", ctx)


def _draft(bill_id: str, check: bool = True, force: bool = False):
    us, st = clients() if check else (None, None)
    return generate.build(con(), bill_id, uscode=us, statutes=st, cache_dir=CACHE_DIR, force=force)


def source_size(row) -> int | None:
    try:
        return Path(row["text_path"]).stat().st_size
    except (OSError, TypeError):
        return None


def pdf_allowed(row) -> bool:
    size = source_size(row)
    return size is not None and size <= PDF_MAX_SOURCE


def _pdf_path(d, warnings_on: bool) -> Path:
    return CACHE_DIR / "pdf" / pdf_name(d.bill_id, d.xml, warnings_on)


def cached_pdf(d, warnings_on: bool = True) -> bytes | None:
    p = _pdf_path(d, warnings_on)
    return p.read_bytes() if p.exists() else None


def render_pdf(d, warnings_on: bool = True) -> bytes:
    """The draft's PDF, from `{cache}/pdf/` by the draft XML's hash (where `reintro pregen-sync`
    also puts the PDFs rendered ahead of time), else rendered and kept there."""
    from ..pdf import draft_pdf as make_pdf
    data = cached_pdf(d, warnings_on)
    if data is not None:
        return data
    with PDF_RENDERS:
        data = cached_pdf(d, warnings_on)
        if data is None:
            data = make_pdf(d.tree, source_line=f"Draft from {d.source['citation']}", warnings_on=warnings_on)
            p = _pdf_path(d, warnings_on)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
    return data


PDF_TOO_LARGE = ("This bill is too long for its PDF to be rendered on request. "
                 "Download the USLM XML instead.")
PDF_OVER_BUDGET = ("The PDFs in one download are limited to about 1 MB of bill text in all. "
                   "Download this one from its bill page.")


def _filename(bill_id: str, ext: str) -> str:
    return f"reintroduction-draft-{bill_id}.{ext}"


def _eligible(bill_id: str):
    try:
        return _draft(bill_id)
    except KeyError:
        raise HTTPException(404, f"no bill {bill_id}")
    except generate.NotEligible as e:
        raise HTTPException(409, str(e))


@app.get("/bills/{bill_id}/draft.xml")
def draft_xml(bill_id: str):
    d = _eligible(bill_id)
    return Response(d.xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(bill_id, "xml")}"'})


@app.get("/bills/{bill_id}/draft.pdf")
def draft_pdf(bill_id: str, warnings: int = 1):
    d = _eligible(bill_id)
    data = cached_pdf(d, bool(warnings))
    if data is None:
        if not pdf_allowed(db.get_bill(con(), bill_id)):
            raise HTTPException(413, PDF_TOO_LARGE)
        data = render_pdf(d, bool(warnings))
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(bill_id, "pdf")}"'})


@app.get("/bills/{bill_id}/warnings.csv")
def draft_warnings(bill_id: str):
    d = _eligible(bill_id)
    return PlainTextResponse(d.warnings_csv(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{_filename(bill_id, "csv")}"'})


@app.get("/selection", response_class=HTMLResponse)
def selection(request: Request):
    return templates.TemplateResponse(request, "selection.html", {})


@app.get("/download")
def download(ids: str = Query(..., description="Comma-separated bill ids, at most 50"), pdf: int = 1):
    want = [i for i in ids.split(",") if i][:50]
    buf = io.BytesIO()
    skipped = []
    budget = PDF_MAX_SOURCE
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        rows = ["bill,kind,where,message"]
        for bid in want:
            try:
                d = _draft(bid)
            except (KeyError, generate.NotEligible) as e:
                skipped.append(f"{bid}: {e}")
                continue
            z.writestr(_filename(bid, "xml"), d.xml)
            row = db.get_bill(con(), bid)
            data = cached_pdf(d) if pdf else None
            if data is not None:
                z.writestr(_filename(bid, "pdf"), data)
            elif pdf and not pdf_allowed(row):
                skipped.append(f"{bid}: PDF not included. {PDF_TOO_LARGE}")
            elif pdf:
                cost = max(source_size(row), PDF_MIN_COST)
                if cost > budget:
                    skipped.append(f"{bid}: PDF not included. {PDF_OVER_BUDGET}")
                else:
                    budget -= cost
                    z.writestr(_filename(bid, "pdf"), render_pdf(d))
            rows += d.warnings_csv().splitlines()[1:]
        z.writestr("warnings.csv", "\n".join(rows) + "\n")
        if skipped:
            z.writestr("skipped.txt", "\n".join(skipped) + "\n")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="reintroduction-drafts.zip"'})


@app.get("/guideline", response_class=HTMLResponse)
def guideline(request: Request):
    title, body = _doc("guideline.md")
    return templates.TemplateResponse(request, "doc.html", {"title": title, "body": body})


@app.get("/research", response_class=HTMLResponse)
def research(request: Request):
    title, body = _doc("research/README.md")
    return templates.TemplateResponse(request, "doc.html", {"title": title, "body": body})


@app.get("/research/{name}.json", include_in_schema=False)
def research_json(name: str):
    p = DOCS / "research" / f"{name}.json"
    if not re.fullmatch(r"[a-z\-]+", name) or not p.exists():
        raise HTTPException(404)
    return Response(p.read_bytes(), media_type="application/json")


@app.get("/plan", response_class=HTMLResponse)
def plan(request: Request):
    title, body = _doc("plan.md")
    return templates.TemplateResponse(request, "doc.html", {"title": title, "body": body})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    title, body = _doc("about.md")
    return templates.TemplateResponse(request, "doc.html", {"title": title, "body": body})


# ---------------------------------------------------------------- JSON API


def _bill_json(row) -> dict:
    d = {k: row[k] for k in row.keys() if k not in ("text_path",)}
    for k in ("enacted_as", "related_enacted", "reintroduced_as"):
        d[k] = json.loads(d[k]) if d[k] else None
    d["enacted"] = bool(d["enacted"])
    d["citation"] = bill_label(row)
    return d


@app.get("/api/v1/search")
def api_search(q: str = "", congress: int | None = None, chamber: str | None = None, subject: str | None = None,
               sponsor: str | None = None, cosponsor: str | None = None, status: str | None = None,
               type: str | None = None, introduced_from: str | None = Query(None, alias="from"),
               introduced_to: str | None = Query(None, alias="to"),
               reported: str | None = Query(None, description="a committee id (HSGO, SSFR, …) that reported the bill "
                                                               "favorably, or `any`"),
               passed: str | None = Query(None, description="house, senate, either or both"),
               sort: str | None = Query(None, description="relevance, number, number-desc, introduced, "
                                                          "introduced-desc, action-desc"),
               limit: int = Query(25, le=100), offset: int = 0):
    res = db.search(con(), q=q, congress=congress, chamber=chamber, subject=subject, sponsor=sponsor,
                    cosponsor=cosponsor, status=status, bill_type=type, introduced_from=_date(introduced_from),
                    introduced_to=_date(introduced_to), reported=reported, passed=passed,
                    sort=sort if sort in db.SORTS else None,
                    limit=limit, offset=offset)
    return {"total": res.total, "results": [_bill_json(r) for r in res.rows]}


@app.get("/api/v1/members")
def api_members():
    """Everyone who sponsored or cosponsored a bill in the database: `id` (bioguide), `name`,
    `party` and `state` as of their latest Congress, `chambers` (Congress → house or senate) and
    `counts` (Congress → bills sponsored and cosponsored). The search filters take the `id`."""
    return Response(members_json(), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/v1/bills/{bill_id}")
def api_bill(bill_id: str):
    row = db.get_bill(con(), bill_id)
    if row is None:
        raise HTTPException(404)
    d = _bill_json(row)
    d["people"] = [dict(p) for p in db.people(con(), bill_id)]
    d["subjects"] = db.subjects(con(), bill_id)
    d["progress"] = [{k: p[k] for k in ("kind", "chamber", "committee_id", "committee", "at")}
                     for p in db.progress(con(), bill_id)]
    return d


@app.get("/api/v1/bills/{bill_id}/draft")
def api_draft(bill_id: str, check: int = 1):
    try:
        d = _draft(bill_id, check=bool(check))
    except KeyError:
        raise HTTPException(404)
    except generate.NotEligible as e:
        return JSONResponse({"detail": str(e)}, status_code=409)
    return {"bill_id": bill_id, "source": d.source, "changes": d.changes, "warnings": d.warnings,
            "report": d.report, "xml_url": f"/bills/{bill_id}/draft.xml", "pdf_url": f"/bills/{bill_id}/draft.pdf"}
