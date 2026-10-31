"""The site's database: one SQLite file with a full-text index (FTS5).

`load_congress` reads unitedstates/congress output for one Congress and replaces
that Congress's rows. `search` answers the site's bill search.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import billdtd
from .congress_data import BILL_TYPES, BillRecord, iter_bills
from .matching import TitleIndex, short_title_key

SCHEMA = """
create table if not exists bills (
    bill_id text primary key,
    congress integer not null,
    bill_type text not null,
    number integer not null,
    introduced_at text,
    official_title text,
    short_title text,
    display_title text,
    status text,
    status_at text,
    enacted integer not null default 0,
    enacted_as text,
    related_enacted text,          -- json: [{bill_id, law, reason}]
    sponsor_id text,
    sponsor_name text,
    sponsor_party text,
    sponsor_state text,
    cosponsor_count integer,
    policy_area text,
    latest_action text,
    latest_action_date text,
    text_version text,
    text_date text,
    text_path text,
    reintroduced_as text,          -- json: [bill_id] in later Congresses
    updated_at text
);
create index if not exists bills_congress on bills(congress, bill_type, number);
create table if not exists bill_people (
    bill_id text not null, bioguide_id text, name text, party text, state text,
    role text not null, original integer, sponsored_at text
);
create index if not exists bill_people_bill on bill_people(bill_id);
create index if not exists bill_people_person on bill_people(bioguide_id);
create table if not exists bill_subjects (bill_id text not null, subject text not null);
create index if not exists bill_subjects_subject on bill_subjects(subject);
create table if not exists bill_titles (bill_id text not null, title text not null, type text, as_of text);
create virtual table if not exists bills_fts using fts5(
    bill_id unindexed, number, titles, subjects, people, body,
    tokenize = 'porter unicode61'
);
create table if not exists loads (congress integer primary key, loaded_at text, bills integer, with_text integer);
"""

TYPE_LABEL = {"hr": "H.R.", "s": "S.", "hres": "H.Res.", "sres": "S.Res.", "hjres": "H.J.Res.",
              "sjres": "S.J.Res.", "hconres": "H.Con.Res.", "sconres": "S.Con.Res."}
NUMBER_RX = re.compile(r"^\s*(h\.?\s*r\.?|s\.?|h\.?\s*res\.?|s\.?\s*res\.?|h\.?\s*j\.?\s*res\.?|s\.?\s*j\.?\s*res\.?|"
                       r"h\.?\s*con\.?\s*res\.?|s\.?\s*con\.?\s*res\.?)\s*(\d+)\s*(?:\(?(\d+)(?:st|nd|rd|th)?\)?)?\s*$", re.I)


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    # WAL: the daily update writes from another process while the site reads.
    if str(path) != ":memory:":
        con.execute("pragma journal_mode=wal")
    con.executescript(SCHEMA)
    return con


def citation(bill_type: str, number: int | str, congress: int | None = None) -> str:
    c = f"{TYPE_LABEL.get(bill_type, bill_type.upper())} {number}"
    return f"{c} ({congress}th)" if congress else c


def parse_number(q: str) -> tuple[str, int, int | None] | None:
    m = NUMBER_RX.match(q)
    if not m:
        return None
    label = re.sub(r"[\s.]", "", m.group(1)).lower()
    t = {"hr": "hr", "s": "s", "hres": "hres", "sres": "sres", "hjres": "hjres", "sjres": "sjres",
         "hconres": "hconres", "sconres": "sconres"}.get(label)
    if not t:
        return None
    return t, int(m.group(2)), int(m.group(3)) if m.group(3) else None


def _people(rec: BillRecord, parties: dict[str, str]):
    sp = rec.data.get("sponsor") or {}
    if sp:
        yield (sp.get("bioguide_id"), sp.get("name"), parties.get(sp.get("bioguide_id")), sp.get("state"), "sponsor", 1, rec.introduced_at)
    for c in rec.data.get("cosponsors") or []:
        if c.get("withdrawn_at"):
            continue
        yield (c.get("bioguide_id"), c.get("name"), parties.get(c.get("bioguide_id")), c.get("state"), "cosponsor",
               1 if c.get("original_cosponsor") else 0, c.get("sponsored_at"))


_PARTY_RX = re.compile(r"<bioguideId>(\w+)</bioguideId>.*?<party>(\w+)</party>", re.S)


def _parties(rec: BillRecord) -> dict[str, str]:
    """Party by bioguide id, from the bill status XML (`data.json` leaves it out)."""
    f = rec.path / "fdsys_billstatus.xml"
    if not f.exists():
        return {}
    out = {}
    for item in re.split(r"<item>", f.read_text(errors="replace")):
        m = _PARTY_RX.search(item)
        if m:
            out.setdefault(m.group(1), m.group(2))
    return out


def load_congress(con: sqlite3.Connection, data_dir: Path, congress: int, *, types=BILL_TYPES,
                  with_text: bool = True, progress=None) -> dict:
    """Replace one Congress's rows from unitedstates/congress output."""
    recs = [r for r in iter_bills(data_dir, congress, types)]
    ids = [r.bill_id for r in recs]
    cur = con.cursor()
    cur.execute("delete from bills where congress = ?", (congress,))
    for table in ("bill_people", "bill_subjects", "bill_titles"):
        cur.execute(f"delete from {table} where bill_id like ?", (f"%-{congress}",))
    cur.execute("delete from bills_fts where bill_id like ?", (f"%-{congress}",))
    n_text = 0
    for i, r in enumerate(recs):
        d = r.data
        text = r.latest_text()
        text_version = text_date = text_path = None
        body = ""
        if text:
            text_version, p = text
            text_path = str(p)
            if with_text:
                try:
                    body = billdtd.body_text(p)
                    text_date = billdtd.read_bill(p).action_date
                except Exception:  # noqa: BLE001 — one malformed file must not stop a load
                    body = ""
            n_text += 1
        parties = _parties(r)
        ppl = list(_people(r, parties))
        enacted_as = d.get("enacted_as")
        latest = d.get("actions")[-1] if d.get("actions") else {}
        cur.execute(
            "insert into bills values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.bill_id, congress, r.bill_type, r.number, r.introduced_at, r.official_title,
             d.get("short_title"), d.get("popular_title") or d.get("short_title") or r.official_title,
             d.get("status") or ("RESERVED" if not d else None), d.get("status_at"), 1 if r.enacted else 0,
             json.dumps(enacted_as) if enacted_as else None, None,
             (d.get("sponsor") or {}).get("bioguide_id"), (d.get("sponsor") or {}).get("name"),
             parties.get((d.get("sponsor") or {}).get("bioguide_id")), (d.get("sponsor") or {}).get("state"),
             len([c for c in d.get("cosponsors") or [] if not c.get("withdrawn_at")]),
             d.get("subjects_top_term"), latest.get("text"), (latest.get("acted_at") or "")[:10] or None,
             text_version, text_date, text_path, None, d.get("updated_at")))
        cur.executemany("insert into bill_people values (?,?,?,?,?,?,?,?)", [(r.bill_id, *p) for p in ppl])
        cur.executemany("insert into bill_subjects values (?,?)", [(r.bill_id, s) for s in (d.get("subjects") or [])])
        cur.executemany("insert into bill_titles values (?,?,?,?)",
                        [(r.bill_id, t.get("title"), t.get("type"), t.get("as")) for t in d.get("titles") or [] if t.get("title")])
        titles = " ".join(dict.fromkeys([r.official_title, *r.short_titles]))
        people = " ".join(p[1] or "" for p in ppl)
        number = f"{r.bill_type}{r.number} {TYPE_LABEL.get(r.bill_type, '')} {r.number}"
        cur.execute("insert into bills_fts values (?,?,?,?,?,?)",
                    (r.bill_id, number, titles, " ".join(d.get("subjects") or []), people, body))
        if progress and i % 500 == 0:
            progress(i, len(recs))
    _mark_related_enacted(con, recs)
    cur.execute("insert or replace into loads values (?, datetime('now'), ?, ?)", (congress, len(ids), n_text))
    con.commit()
    return {"congress": congress, "bills": len(ids), "with_text": n_text}


def _mark_related_enacted(con, recs: list[BillRecord]):
    """A bill whose identical or companion bill became law, or whose title an
    enacted bill of the same Congress shares, is marked with that law."""
    by_id = {r.bill_id: r for r in recs}
    enacted = [r for r in recs if r.enacted]
    idx = TitleIndex(r.key() for r in enacted)
    for r in recs:
        if r.enacted or not r.data:
            continue
        hits = []
        for rb in r.data.get("related_bills") or []:
            other = by_id.get(rb.get("bill_id", ""))
            if other is not None and other.enacted and rb.get("reason") in ("identical", "related"):
                hits.append({"bill_id": other.bill_id, "law": other.data.get("enacted_as"),
                             "reason": f"{rb.get('reason')} bill ({rb.get('identified_by') or 'CRS'})"})
        m = idx.best(r.key(), threshold=0.6)
        if m and m.candidate.bill_id not in {h["bill_id"] for h in hits}:
            other = by_id[m.candidate.bill_id]
            hits.append({"bill_id": other.bill_id, "law": other.data.get("enacted_as"),
                         "reason": "same title: " + ", ".join(dict.fromkeys(m.reasons))})
        if hits:
            con.execute("update bills set related_enacted = ? where bill_id = ?", (json.dumps(hits), r.bill_id))


def mark_reintroductions(con: sqlite3.Connection, data_dir: Path, older: int, newer: int, types=("hr", "s")) -> int:
    """Record, on each bill of `older`, the bills of `newer` that match it by title."""
    newer_recs = [r for r in iter_bills(data_dir, newer, types) if r.data]
    idx = TitleIndex(r.key() for r in newer_recs)
    n = 0
    for row in con.execute("select bill_id from bills where congress = ?", (older,)).fetchall():
        rec = _record_from_db(con, row["bill_id"])
        m = idx.best(rec)
        if m:
            con.execute("update bills set reintroduced_as = ? where bill_id = ?", (json.dumps([m.candidate.bill_id]), row["bill_id"]))
            n += 1
    con.commit()
    return n


def _record_from_db(con, bill_id):
    from .matching import BillKey
    b = con.execute("select * from bills where bill_id = ?", (bill_id,)).fetchone()
    shorts = [t["title"] for t in con.execute("select title from bill_titles where bill_id = ? and type = 'short'", (bill_id,))]
    return BillKey(bill_id, b["official_title"] or "", tuple(shorts), b["sponsor_id"], b["introduced_at"])


@dataclass
class SearchResult:
    total: int
    rows: list[sqlite3.Row]


def fts_query(q: str) -> str:
    """User words as an FTS5 query: quoted phrases kept, other words ANDed."""
    parts = re.findall(r'"([^"]+)"|(\S+)', q)
    out = []
    for phrase, word in parts:
        if phrase:
            out.append('"' + phrase.replace('"', "") + '"')
        else:
            w = re.sub(r"[^\w\-–']", "", word)
            if w:
                out.append('"' + w + '"')
    return " ".join(out)


# Result orders: the relevance order needs a text query; the others do not.
SORTS = {
    "relevance": None,
    "number": "b.congress desc, b.bill_type, b.number",
    "number-desc": "b.congress desc, b.bill_type, b.number desc",
    "introduced-desc": "b.introduced_at desc, b.bill_type, b.number",
    "introduced": "b.introduced_at, b.bill_type, b.number",
    "action-desc": "b.latest_action_date desc, b.bill_type, b.number",
}
MARK_START, MARK_END = "\x02", "\x03"


def search(con: sqlite3.Connection, *, q: str = "", congress: int | None = None, chamber: str | None = None,
           subject: str | None = None, sponsor: str | None = None, cosponsor: str | None = None,
           status: str | None = None, bill_type: str | None = None, introduced_from: str | None = None,
           introduced_to: str | None = None, sort: str | None = None,
           limit: int = 25, offset: int = 0) -> SearchResult:
    """Bills matching the filters. With a text query each row carries `snippet`: the passage of
    the bill text that matched, its matched words between MARK_START and MARK_END."""
    where, args = [], []
    order = SORTS["number"]
    join = ""
    cols = "b.*, null as snippet"
    num = parse_number(q) if q else None
    if num:
        t, n, c = num
        where += ["b.bill_type = ?", "b.number = ?"]
        args += [t, n]
        if c:
            where.append("b.congress = ?")
            args.append(c)
    elif q:
        join = "join bills_fts f on f.bill_id = b.bill_id"
        where.append("bills_fts match ?")
        args.append(fts_query(q))
        order = "bm25(bills_fts, 0, 10.0, 8.0, 3.0, 2.0, 1.0)"
        cols = f"b.*, snippet(bills_fts, 5, '{MARK_START}', '{MARK_END}', '…', 28) as snippet"
    if congress:
        where.append("b.congress = ?")
        args.append(congress)
    if chamber in ("house", "senate"):
        where.append("substr(b.bill_type, 1, 1) = ?")
        args.append("h" if chamber == "house" else "s")
    if subject:
        where.append("(b.policy_area = ? or b.bill_id in (select bill_id from bill_subjects where subject = ?))")
        args += [subject, subject]
    for role, who in (("sponsor", sponsor), ("cosponsor", cosponsor)):
        if who:
            where.append(f"b.bill_id in (select bill_id from bill_people where role = '{role}' and (bioguide_id = ? or name like ?))")
            args += [who, f"%{who}%"]
    if status == "enacted":
        where.append("b.enacted = 1")
    elif status == "not-enacted":
        where.append("b.enacted = 0 and b.related_enacted is null")
    elif status == "related-enacted":
        where.append("b.related_enacted is not null")
    if bill_type in TYPE_LABEL:
        where.append("b.bill_type = ?")
        args.append(bill_type)
    if introduced_from:
        where.append("b.introduced_at >= ?")
        args.append(introduced_from)
    if introduced_to:
        where.append("b.introduced_at <= ?")
        args.append(introduced_to)
    if SORTS.get(sort or ""):
        order = SORTS[sort]
    sql_where = ("where " + " and ".join(where)) if where else ""
    total = con.execute(f"select count(*) from bills b {join} {sql_where}", args).fetchone()[0]
    rows = con.execute(f"select {cols} from bills b {join} {sql_where} order by {order} limit ? offset ?",
                       [*args, limit, offset]).fetchall()
    return SearchResult(total, rows)


def query_terms(q: str) -> list[str]:
    """The words and quoted phrases of a text query, for highlighting outside the index."""
    out = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', q or ""):
        t = (phrase or re.sub(r"[^\w\-–']", "", word)).strip()
        if t and t.lower() not in ("and", "or", "not"):
            out.append(t)
    return out


def get_bill(con, bill_id: str):
    return con.execute("select * from bills where bill_id = ?", (bill_id,)).fetchone()


def people(con, bill_id: str):
    return con.execute("select * from bill_people where bill_id = ? order by role desc, original desc, name", (bill_id,)).fetchall()


def subjects(con, bill_id: str):
    return [r["subject"] for r in con.execute("select subject from bill_subjects where bill_id = ? order by subject", (bill_id,))]


def subject_list(con, congress: int | None = None, limit: int = 500):
    if congress:
        return con.execute("select policy_area as s, count(*) n from bills where congress = ? and policy_area is not null "
                           "group by policy_area order by policy_area", (congress,)).fetchall()
    return con.execute("select policy_area as s, count(*) n from bills where policy_area is not null "
                       "group by policy_area order by policy_area limit ?", (limit,)).fetchall()


def congresses(con):
    return [r[0] for r in con.execute("select congress from loads order by congress desc")]


def same_title_in(con, bill_id: str, congress: int):
    """Bills of `congress` sharing a short title (year removed) with `bill_id`."""
    keys = {short_title_key(t["title"]) for t in con.execute(
        "select title from bill_titles where bill_id = ? and type = 'short'", (bill_id,))}
    keys.discard("")
    if not keys:
        return []
    rows = con.execute("select t.bill_id, t.title from bill_titles t join bills b on b.bill_id = t.bill_id "
                       "where b.congress = ? and t.type = 'short'", (congress,)).fetchall()
    hits = {}
    for r in rows:
        if short_title_key(r["title"]) in keys and r["bill_id"] != bill_id:
            hits[r["bill_id"]] = r["title"]
    return [(get_bill(con, b), t) for b, t in hits.items()]
