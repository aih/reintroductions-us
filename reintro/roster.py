"""Who sits in the next Congress: the 120th, from the elections of 3 November 2026.

`build` reads three sources and writes `reintro/data/congress120.json`:

- Wikipedia's "120th United States Congress" article: one line per seat, the member-elect
  or `TBD` while the race is not called (`parse_roster`).
- Wikipedia's 2026 House and Senate election articles: the candidates in each race and the
  status line of each incumbent (`parse_races`), so an incumbent in a race not yet called
  is `pending` rather than `outgoing`, and an outgoing incumbent carries the reason.
- unitedstates/congress-legislators: the current members, and every member's Wikipedia
  title, which maps a name on the roster to a bioguide id. Former members are read too, so a
  former member elected again (Jerry Carl, AL-1) is matched.

Each current member, and each former member elected again, gets a `status`:

- `returning`: holds a seat in the 120th (re-elected, elected to the other chamber, or a
  senator whose term continues);
- `pending`: a candidate in a race the roster still lists as `TBD`;
- `outgoing`: serving in the 119th and not in the 120th.

A person in the database who is in none of these is a former member (`db.MEMBER_STATUSES`).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import httpx

DATA_FILE = Path(__file__).parent / "data" / "congress120.json"
USER_AGENT = "reintroductions-us/1.0 (https://github.com/aih/reintroductions-us)"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_INDEX = "https://en.wikipedia.org/w/index.php"
ROSTER_PAGE = "120th United States Congress"
HOUSE_PAGE = "2026 United States House of Representatives elections"
SENATE_PAGE = "2026 United States Senate elections"
LEGISLATORS = "https://unitedstates.github.io/congress-legislators/legislators-{}.json"

STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "American Samoa": "AS", "District of Columbia": "DC", "Guam": "GU",
    "Northern Mariana Islands": "MP", "Puerto Rico": "PR", "United States Virgin Islands": "VI",
    "U.S. Virgin Islands": "VI", "Virgin Islands": "VI",
}
ABBRS = set(STATES.values())


def state_code(s: str) -> str | None:
    s = s.strip().replace("_", " ")
    return s.upper() if s.upper() in ABBRS else STATES.get(s)


def party_group(code: str | None) -> str | None:
    """`democratic`, `republican` or `other` for a party as Congress.gov, the legislators file or
    Wikipedia writes it (`D`, `Democrat`, `DFL`, `PPD/D`, `I`, `ID`, `Independent`, …)."""
    if not code:
        return None
    c = code.strip().upper()
    if c in ("D", "DEMOCRAT", "DEMOCRATIC", "DFL") or c.endswith("/D"):
        return "democratic"
    if c in ("R", "REPUBLICAN") or c.endswith("/R"):
        return "republican"
    return "other"


def party_letter(party: str | None) -> str | None:
    """`D`, `R` or `I` for the legislators file's `Democrat`, `Republican`, `Independent`; other
    forms (`DFL`, `PPD/D`) as written."""
    if not party:
        return None
    return {"democrat": "D", "democratic": "D", "republican": "R", "independent": "I"}.get(party.lower(), party)


# ---------------------------------------------------------------- wikitext


def _strip_refs(s: str) -> str:
    """`s` without <ref>…</ref>, <ref …/>, and {{efn…}} / {{Cite…}} templates (which nest)."""
    s = re.sub(r"<ref[^>/]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    out, i = [], 0
    while i < len(s):
        m = re.compile(r"\{\{\s*(efn|cite|refn|sfn)", re.I).search(s, i)
        if not m:
            out.append(s[i:])
            break
        out.append(s[i:m.start()])
        depth, j = 0, m.start()
        while j < len(s):
            if s.startswith("{{", j):
                depth, j = depth + 1, j + 2
            elif s.startswith("}}", j):
                depth, j = depth - 1, j + 2
                if depth == 0:
                    break
            else:
                j += 1
        i = j
    return "".join(out)


def plain(s: str) -> str:
    """Wikitext as reading text: links as their label, templates and markup dropped."""
    s = _strip_refs(s)
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"\{\{small\|([^{}]*)\}\}", r"\1", s, flags=re.I)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"'''?|<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip(" |")


def _link(s: str) -> tuple[str | None, str]:
    """(title, label) of the first wiki link in `s`, else (None, plain text)."""
    m = re.search(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", s)
    if not m:
        return None, plain(s)
    return m.group(1).strip(), (m.group(2) or m.group(1)).strip()


def _sortname_title(args: str) -> tuple[str, str]:
    """(title, name) of a {{sortname|First|Last|link|dab=…}}."""
    parts = [p.strip() for p in args.split("|")]
    pos = [p for p in parts if "=" not in p]
    named = dict(p.split("=", 1) for p in parts if "=" in p)
    name = " ".join(pos[:2])
    title = pos[2] if len(pos) > 2 and pos[2] and pos[2] != "nolink" else name
    if named.get("dab"):
        title = f"{title} ({named['dab'].strip()})"
    return title, name


_SENATE_STATE = re.compile(r"^====\s*\[\[List of United States senators from ([^|\]]+)")
_HOUSE_STATE = re.compile(r"^====\s*\[\[List of United States representatives from ([^|\]]+)")
_SENATE_SEAT = re.compile(r"^:\s*(?:\{\{Party stripe\|[^}]*\}\}\s*)+(\d)\.\s*(.*)$")
_HOUSE_SEAT = re.compile(r"^:\s*(?:\{\{Party stripe\|[^}]*\}\}\s*)+\{\{ushr\|([^|}]+)\|([^|}]+)(?:\|[^}]*)?\}\}\s*[.:]\s*(.*)$")


def _seat_holder(rest: str) -> dict:
    rest = _strip_refs(rest).strip()
    m = re.search(r"\(([^()]+)\)\s*$", rest)
    party = m.group(1).strip() if m else None
    who = rest[:m.start()].strip() if m else rest
    if re.match(r"(TBD|Vacant|TBA)\b", who, re.I) or not who:
        return {"called": False, "name": None, "wikipedia": None, "party": party}
    title, label = _link(who)
    return {"called": True, "name": label, "wikipedia": title, "party": party}


def parse_roster(text: str) -> list[dict]:
    """The seats of the 120th Congress article: {chamber, state, seat, called, name, wikipedia,
    party}. `seat` is the Senate class (`1`, `2`, `3`) or the House district (`AL` at large)."""
    seats, chamber, state = [], None, None
    for line in text.splitlines():
        if re.match(r"^===\s*Senate\s*===", line):
            chamber = "senate"
        elif re.match(r"^===\s*Representatives\s*===", line):
            chamber = "house"
        elif re.match(r"^==\s*[^=]", line) and chamber:
            chamber = None if not re.match(r"^==\s*Members", line) else chamber
        m = _SENATE_STATE.match(line) or _HOUSE_STATE.match(line)
        if m:
            state = state_code(m.group(1))
            continue
        if chamber == "senate" and state and (m := _SENATE_SEAT.match(line)):
            seats.append({"chamber": "senate", "state": state, "seat": m.group(1), **_seat_holder(m.group(2))})
        elif chamber == "house" and (m := _HOUSE_SEAT.match(line)):
            st = state_code(m.group(1))
            if st:
                seats.append({"chamber": "house", "state": st, "seat": m.group(2).strip().upper(),
                              **_seat_holder(m.group(3))})
    return seats


def _rows(table_text: str) -> list[str]:
    return re.split(r"\n\|-[^\n]*", table_text)


def _candidates(row: str) -> list[str]:
    """Wikipedia titles of the candidates listed in a race row (lines starting with `*`)."""
    out = []
    for line in row.splitlines():
        if line.lstrip().startswith("*"):
            t, _ = _link(re.sub(r"\{\{Party stripe\|[^}]*\}\}", "", _strip_refs(line)))
            if t:
                out.append(t)
    return out


_CELL_ATTRS = re.compile(r"^\s*(?:\{\{[^{}]*\}\}|(?:style|class|data-sort-value|rowspan|colspan)\s*=\s*(?:\"[^\"]*\"|\S+))"
                         r"(?:\s*(?:\{\{[^{}]*\}\}|(?:style|class|data-sort-value|rowspan|colspan)\s*=\s*(?:\"[^\"]*\"|\S+)))*\s*\|")


def _status_line(row: str) -> str | None:
    """The plain text of the row's first cell that starts `Incumbent` (or `Interim appointee`),
    after its attributes (`style=…|`, `{{Party shading/…}} |`)."""
    for line in row.splitlines():
        if not line.startswith("|"):
            continue
        cell = line[1:]
        cell = _CELL_ATTRS.sub("", cell, count=1)
        if re.match(r"\s*(Incumbent|Interim appointee|Appointee)\b", cell):
            return plain(cell)
    return None


def parse_races(house: str, senate: str) -> list[dict]:
    """Each race of the two election articles: {chamber, state, seat, special, called,
    candidates (titles), incumbents [{wikipedia, name, status}]}. `seat` is the district for the
    House and None for the Senate. A race is called when a candidate carries {{aye}}."""
    races = []
    start = house.find("\n==Alabama==")
    cur = None
    for row in _rows(house[start:] if start >= 0 else house):
        ended = "\n|}" in row
        row = row.split("\n|}", 1)[0]
        m = re.search(r"^!\s*(?:rowspan=\d+\s*\|\s*)?\{\{ushr\|([^|}]+)\|([^|}]+)", row, re.M)
        if m:
            cur = {"chamber": "house", "state": state_code(m.group(1)), "seat": m.group(2).strip().upper(),
                   "special": False, "called": False, "candidates": [], "incumbents": []}
            races.append(cur)
        if cur is not None:
            _add_row(cur, row)
        if ended:
            cur = None
    start = senate.find("== Race summary")
    body = senate[start:] if start >= 0 else senate
    end = re.search(r"\n==[^=]", body[3:])
    body = body[:end.start() + 3] if end else body
    cur = None
    for row in _rows(body):
        ended = "\n|}" in row
        row = row.split("\n|}", 1)[0]
        m = re.search(r"^!\s*\[\[2026 United States Senate (special )?election in ([^|\]]+)", row, re.M)
        if m:
            name = re.sub(r"\s*\(.*\)$", "", m.group(2))
            cur = {"chamber": "senate", "state": state_code(name), "seat": None, "special": bool(m.group(1)),
                   "called": False, "candidates": [], "incumbents": []}
            races.append(cur)
        if cur is not None:
            _add_row(cur, row)
        if ended:
            cur = None
    return races


def _add_row(race: dict, row: str) -> None:
    race["candidates"] += _candidates(row)
    if re.search(r"\{\{aye\}\}", row, re.I):
        race["called"] = True
    for m in re.finditer(r"\{\{sortname\|([^}]*)\}\}", row):
        title, name = _sortname_title(m.group(1))
        race["incumbents"].append({"wikipedia": title, "name": name, "status": _status_line(row)})
    for m in re.finditer(r"^\|\s*\[\[([^|\]\d][^|\]]*)(?:\|([^\]]+))?\]\]\s*$", row, re.M):  # a linked incumbent
        race["incumbents"].append({"wikipedia": m.group(1).strip(), "name": (m.group(2) or m.group(1)).strip(),
                                   "status": _status_line(row)})


# ---------------------------------------------------------------- sources


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)


def _get(client: httpx.Client, url: str, params: dict, tries: int = 6) -> httpx.Response:
    """GET with Wikimedia's `Retry-After` honoured on a 429 (the API limits shared addresses)."""
    for i in range(tries):
        r = client.get(url, params=params)
        if r.status_code != 429 or i == tries - 1:
            r.raise_for_status()
            return r
        time.sleep(min(float(r.headers.get("retry-after") or 2 ** i), 60))
    raise AssertionError("unreachable")


def fetch_page(client: httpx.Client, title: str) -> dict:
    """The current wikitext of a Wikipedia article (`action=raw`) with its revision id and time
    from the API, or None for both when the API stays rate-limited."""
    r = _get(client, WIKI_INDEX, {"title": title, "action": "raw"})
    page = {"title": title, "text": r.text, "revid": None, "timestamp": r.headers.get("last-modified")}
    try:
        q = _get(client, WIKI_API, {"action": "query", "prop": "revisions", "rvprop": "ids|timestamp",
                                    "titles": title, "format": "json", "formatversion": "2"}).json()
        rev = q["query"]["pages"][0]["revisions"][0]
        page.update(revid=rev["revid"], timestamp=rev["timestamp"])
    except (httpx.HTTPError, KeyError, IndexError):
        pass
    return page


def resolve_titles(client: httpx.Client, titles: list[str]) -> dict[str, str]:
    """Each title's target after Wikipedia's redirects and normalization (titles unchanged are
    left out). Best effort: a batch the API refuses is skipped, and `_same_name` matches instead."""
    out = {}
    for i in range(0, len(titles), 50):
        try:
            r = _get(client, WIKI_API, {"action": "query", "titles": "|".join(titles[i:i + 50]), "redirects": "1",
                                        "format": "json", "formatversion": "2"})
        except httpx.HTTPError:
            continue
        q = r.json().get("query") or {}
        for kind in ("normalized", "redirects"):
            for x in q.get(kind) or []:
                out[x["from"]] = x["to"]
        time.sleep(1)
    for t in list(out):  # a normalized title that is also a redirect
        seen = {t}
        while out[t] in out and out[t] not in seen:
            seen.add(out[t])
            out[t] = out[out[t]]
    return out


def fetch_legislators(client: httpx.Client, which: str) -> list[dict]:
    r = client.get(LEGISLATORS.format(which))
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- build


def _norm(title: str | None) -> str | None:
    return title.replace("_", " ").strip() if title else None


def _display_name(p: dict) -> str:
    n = p.get("name") or {}
    return n.get("official_full") or " ".join(x for x in (n.get("first"), n.get("last")) if x)


def build_roster(roster_text: str, house_text: str, senate_text: str, current: list[dict],
                 historical: list[dict], redirects: dict[str, str] | None = None) -> dict:
    """The data file's `seats` and `members` from the parsed sources (no network)."""
    redirects = redirects or {}
    canon = lambda t: _norm(redirects.get(t, t)) if t else None  # noqa: E731
    by_title: dict[str, dict] = {}
    for p in historical + current:  # current last, so it wins a shared title
        t = _norm((p.get("id") or {}).get("wikipedia"))
        if t:
            by_title[t] = p
            by_title[canon(t)] = p
    seats = parse_roster(roster_text)
    races = parse_races(house_text, senate_text)
    cur_ids = {p["id"]["bioguide"]: p for p in current}

    elected: dict[str, dict] = {}
    for s in seats:
        p = by_title.get(canon(s["wikipedia"])) if s["called"] else None
        if p is None and s["called"]:
            p = _same_name(current, s)
        s["bioguide"] = p["id"]["bioguide"] if p else None
        if p:
            elected[p["id"]["bioguide"]] = s

    pending_seats = {(s["chamber"], s["state"], s["seat"]) for s in seats if not s["called"]}
    open_races = []
    for r in races:
        if r["chamber"] == "house":
            if ("house", r["state"], r["seat"]) in pending_seats:
                open_races.append(r)
            continue
        classes = {seat for c, st, seat in pending_seats if c == "senate" and st == r["state"]}
        if (r["special"] and classes - {"2"}) or (not r["special"] and "2" in classes):
            open_races.append(r)
    candidate_of: dict[str, dict] = {}
    for r in open_races:
        for t in r["candidates"]:
            p = by_title.get(canon(t))
            if p:
                candidate_of.setdefault(p["id"]["bioguide"], r)
    reason: dict[str, str] = {}
    for r in races:
        for inc in r["incumbents"]:
            p = by_title.get(canon(inc["wikipedia"]))
            if p and inc["status"]:
                reason.setdefault(p["id"]["bioguide"], inc["status"])

    members: dict[str, dict] = {}
    for bid in sorted(set(cur_ids) | set(elected)):
        p = cur_ids.get(bid) or by_title.get(canon(elected[bid]["wikipedia"]))
        term = p["terms"][-1]
        now = None
        if bid in cur_ids:
            now = {"chamber": "senate" if term["type"] == "sen" else "house", "state": term["state"],
                   "seat": str(term.get("class")) if term["type"] == "sen" else _district(term)}
        m = {"name": _display_name(p), "party": party_letter(term.get("party")), "party_group": party_group(term.get("party")),
             "now": now}
        if bid in elected:
            s = elected[bid]
            m["status"] = "returning"
            m["next"] = {"chamber": s["chamber"], "state": s["state"], "seat": s["seat"]}
            m["party"] = party_letter(s["party"]) or m["party"]
            m["party_group"] = party_group(s["party"]) or m["party_group"]
            if now is None:
                m["detail"] = "Former member, elected again"
            elif now["chamber"] != s["chamber"]:
                m["detail"] = f"Elected to the {s['chamber'].capitalize()}"
        elif bid in candidate_of:
            r = candidate_of[bid]
            m["status"] = "pending"
            m["race"] = {"chamber": r["chamber"], "state": r["state"], "seat": r["seat"], "special": r["special"]}
            where = f"{r['state']}-{r['seat']}" if r["chamber"] == "house" else f"Senate in {r['state']}"
            m["detail"] = f"Race not yet called ({where})"
        else:
            m["status"] = "outgoing"
            r = reason.get(bid)
            m["detail"] = r if r and LEAVING_RX.search(r) else "Not elected to the 120th Congress"
        members[bid] = m
    return {"seats": seats, "members": members}


def _same_name(current: list[dict], seat: dict) -> dict | None:
    """The one current member of the seat's state and chamber whose last name ends the seat's
    name, for a title the legislators file spells differently."""
    words = re.sub(r",?\s+(Jr\.?|Sr\.?|I{2,3}|IV)$", "", seat["name"] or "").split()
    if not words:
        return None
    last = words[-1].lower()
    kind = "sen" if seat["chamber"] == "senate" else "rep"
    first = NICKNAMES.get(words[0].lower(), words[0].lower())
    hits = []
    for c in current:
        n = c.get("name") or {}
        if (c["terms"][-1]["state"] == seat["state"] and c["terms"][-1]["type"] == kind
                and n.get("last", "").lower().split()[-1:] == [last]
                and first in {NICKNAMES.get(x, x) for x in (n.get("first", "").lower(), (n.get("nickname") or "").lower())}):
            hits.append(c)
    return hits[0] if len(hits) == 1 else None


NICKNAMES = {"bill": "william", "will": "william", "bob": "robert", "rob": "robert", "jim": "james",
             "mike": "michael", "tom": "thomas", "dick": "richard", "rick": "richard", "chuck": "charles",
             "dan": "daniel", "dave": "david", "joe": "joseph", "ed": "edward", "andy": "andrew",
             "chris": "christopher", "pat": "patrick", "tony": "anthony", "steve": "steven", "ben": "benjamin",
             "nick": "nicholas", "greg": "gregory", "jeff": "jeffrey", "jon": "jonathan", "matt": "matthew",
             "ted": "edward", "liz": "elizabeth", "beth": "elizabeth", "kathy": "kathleen", "sam": "samuel"}


LEAVING_RX = re.compile(r"retir|lost|withdr|resign|died|run for|ran for|elected to|appointed|defeated", re.I)


def _district(term: dict) -> str:
    d = term.get("district")
    return "AL" if d in (0, None) else str(d)


PAGES = {"roster": ROSTER_PAGE, "house": HOUSE_PAGE, "senate": SENATE_PAGE}


def fetch(to: Path) -> None:
    """Download the sources into `to`: roster.wiki, house.wiki, senate.wiki (with sources.json
    holding their revision ids) and legislators-current.json, legislators-historical.json."""
    to.mkdir(parents=True, exist_ok=True)
    meta = {}
    with _client() as c:
        for key, title in PAGES.items():
            page = fetch_page(c, title)
            (to / f"{key}.wiki").write_text(page.pop("text"))
            meta[key] = page
        for which in ("current", "historical"):
            (to / f"legislators-{which}.json").write_text(json.dumps(fetch_legislators(c, which)))
    (to / "sources.json").write_text(json.dumps(meta, indent=1))


def build(source: Path, out: Path = DATA_FILE, resolve: bool = True) -> dict:
    """The data file from the sources `fetch` wrote into `source`. With `resolve`, Wikipedia's
    redirects are looked up for the titles that match no member. Returns the summary."""
    texts = {k: (source / f"{k}.wiki").read_text() for k in PAGES}
    current = json.loads((source / "legislators-current.json").read_text())
    historical = json.loads((source / "legislators-historical.json").read_text())
    meta = json.loads((source / "sources.json").read_text()) if (source / "sources.json").exists() else {}
    redirects = {}
    if resolve:
        known = {_norm((p.get("id") or {}).get("wikipedia")) for p in historical + current}
        seats = parse_roster(texts["roster"])
        races = parse_races(texts["house"], texts["senate"])
        titles = {s["wikipedia"] for s in seats if s["wikipedia"]}
        titles |= {t for r in races for t in r["candidates"]} | {i["wikipedia"] for r in races for i in r["incumbents"]}
        with _client() as c:
            redirects = resolve_titles(c, sorted(t for t in titles - known if t))
    data = build_roster(texts["roster"], texts["house"], texts["senate"], current, historical, redirects)
    sources = []
    for key, title in PAGES.items():
        m = meta.get(key) or {}
        url = (f"https://en.wikipedia.org/w/index.php?oldid={m['revid']}" if m.get("revid")
               else f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}")
        sources.append({"title": title, "revid": m.get("revid"), "timestamp": m.get("timestamp"), "url": url})
    sources.append({"title": "congress-legislators", "url": "https://github.com/unitedstates/congress-legislators"})
    data = {"congress": 120, "election": "2026-11-03",
            "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sources": sources, "summary": summary(data), **data}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return data["summary"]


def summary(data: dict) -> dict:
    seats, members = data["seats"], data["members"]
    by = lambda xs, k: {v: sum(1 for x in xs if x == v) for v in sorted(set(xs))}  # noqa: E731
    return {
        "seats": len(seats),
        "seats_called": {c: sum(1 for s in seats if s["chamber"] == c and s["called"]) for c in ("senate", "house")},
        "seats_pending": {c: sum(1 for s in seats if s["chamber"] == c and not s["called"]) for c in ("senate", "house")},
        "new_members_elect": sum(1 for s in seats if s["called"] and not s.get("bioguide")),
        "members": by([m["status"] for m in members.values()], "status"),
    }


def load(path: Path = DATA_FILE) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None
