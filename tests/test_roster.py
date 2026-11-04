import json
from pathlib import Path

import pytest

from conftest import CONGRESS, FIX
from reintro import db, roster

SRC = FIX / "roster"


def test_parse_roster_reads_called_and_open_seats():
    seats = roster.parse_roster((SRC / "roster.wiki").read_text())
    by = {(s["chamber"], s["state"], s["seat"]): s for s in seats}
    assert by[("senate", "VT", "1")] == {"chamber": "senate", "state": "VT", "seat": "1", "called": True,
                                         "name": "Bernie Sanders", "wikipedia": "Bernie Sanders", "party": "I"}
    assert by[("senate", "MS", "2")]["called"] is False
    assert by[("house", "CO", "4")]["called"] is False
    assert by[("house", "NY", "7")]["name"] == "Claire Valdez"
    assert {s["chamber"] for s in seats} == {"senate", "house"}


def test_parse_races_reads_candidates_and_incumbent_status():
    races = roster.parse_races((SRC / "house.wiki").read_text(), (SRC / "senate.wiki").read_text())
    co4 = next(r for r in races if r["chamber"] == "house" and r["state"] == "CO" and r["seat"] == "4")
    assert co4["called"] is False and "Lauren Boebert" in co4["candidates"]
    ny7 = next(r for r in races if r["chamber"] == "house" and r["state"] == "NY" and r["seat"] == "7")
    assert ny7["incumbents"][0]["status"].startswith("Incumbent retired")
    oh = next(r for r in races if r["chamber"] == "senate" and r["state"] == "OH")
    assert oh["special"] is True and oh["called"] is True


def build(tmp_path) -> dict:
    out = tmp_path / "congress120.json"
    roster.build(SRC, out=out, resolve=False)
    return json.loads(out.read_text())


def test_build_marks_returning_pending_and_outgoing(tmp_path):
    data = build(tmp_path)
    m = data["members"]
    assert m["S000033"]["status"] == "returning" and m["S000033"]["party_group"] == "other"  # Sanders, class 1
    assert m["B000825"]["status"] == "pending" and m["B000825"]["detail"] == "Race not yet called (CO-4)"
    assert m["H001079"]["status"] == "pending"  # Hyde-Smith, Mississippi not called
    assert m["V000081"]["status"] == "outgoing" and m["V000081"]["detail"].startswith("Incumbent retired")
    assert m["N000026"]["status"] == "outgoing"  # TX-22 went to Trever Nehls, his brother
    assert m["C001056"]["status"] == "outgoing"  # Cornyn lost renomination
    assert m["H001104"]["status"] == "outgoing"  # Husted, Ohio special
    assert m["B000944"]["status"] == "returning" and m["B000944"]["detail"] == "Former member, elected again"
    assert m["B000944"]["now"] is None and m["B000944"]["party"] == "D"
    assert data["summary"]["seats_pending"]["senate"] == 1
    assert {s["title"] for s in data["sources"]} >= {roster.ROSTER_PAGE, roster.HOUSE_PAGE, roster.SENATE_PAGE}


def test_party_group():
    assert [roster.party_group(p) for p in ("D", "Democrat", "DFL", "PPD/D", "R", "Republican", "I", "ID", "L", None)] == \
        ["democratic"] * 4 + ["republican"] * 2 + ["other"] * 3 + [None]


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("roster")
    out = tmp / "congress120.json"
    roster.build(SRC, out=out, resolve=False)
    c = db.connect(tmp / "t.db")
    db.load_congress(c, CONGRESS, 118)
    db.load_congress(c, CONGRESS, 119)
    assert db.sync_roster(c, out, force=True)
    assert not db.sync_roster(c, out)
    return c


def test_party_filters(con):
    ids = lambda **kw: sorted(r["bill_id"] for r in db.search(con, limit=100, **kw).rows)
    assert ids(congress=119, sponsor_party="democratic") == ["hr206-119", "s231-119"]
    assert ids(congress=119, sponsor_party="republican") == ["hr695-119"]
    assert ids(sponsor_party="other") == []
    assert ids(cosponsor_party="other") == ["s231-119", "s3401-118"]  # Sanders
    assert ids(congress=119, cosponsor_party="republican") == ["hr695-119"]


def test_next_congress_filters(con):
    ids = lambda **kw: sorted(r["bill_id"] for r in db.search(con, limit=100, **kw).rows)
    assert ids(sponsor_status="returning") == ["s231-119", "s3401-118"]  # Welch
    assert ids(sponsor_status="outgoing") == ["hr1514-118", "hr206-119", "hr695-119"]
    assert ids(sponsor_status="not-returning") == ["hr1514-118", "hr206-119", "hr695-119"]
    assert ids(sponsor_status="former") == []
    assert ids(cosponsor_status="pending") == ["hr1514-118", "hr695-119"]  # Boebert
    assert ids(cosponsor_status="returning") == ["s231-119", "s3401-118"]
    assert "hr1514-118" in ids(cosponsor_status="former")
    people = {p["bioguide_id"]: p for p in db.people(con, "hr695-119")}
    assert people["N000026"]["next_status"] == "outgoing" and people["B000825"]["next_status"] == "pending"
    assert people["D000594"]["next_status"] is None  # not in the fixture roster: a former member
    assert {m["id"]: m["next"] for m in db.members(con)}["W000800"] == "returning"


def test_a_linked_incumbent_carries_the_reason(tmp_path):
    assert build(tmp_path)["members"]["H001104"]["detail"] == "Interim appointee lost election. Democratic gain."


def test_a_redirect_to_a_relative_is_not_a_match():
    """Wikipedia's "Trever Nehls" redirects to "Troy Nehls"; TX-22's member-elect is Trever."""
    src = lambda n: (SRC / n).read_text()  # noqa: E731
    current = json.loads(src("legislators-current.json"))
    data = roster.build_roster(src("roster.wiki"), src("house.wiki"), src("senate.wiki"), current,
                               json.loads(src("legislators-historical.json")), {"Trever Nehls": "Troy Nehls"})
    assert data["members"]["N000026"]["status"] == "outgoing"
    tx22 = next(s for s in data["seats"] if s["state"] == "TX" and s["seat"] == "22")
    assert tx22["bioguide"] is None
    data = roster.build_roster(src("roster.wiki"), src("house.wiki"), src("senate.wiki"), current,
                               json.loads(src("legislators-historical.json")), {"Lauren Boebert (politician)": "Lauren Boebert"})
    assert data["members"]["B000825"]["status"] == "pending"
