import importlib
import json

import pytest
from fastapi.testclient import TestClient

from conftest import CONGRESS
from reintro import db


@pytest.fixture(scope="module")
def database(tmp_path_factory):
    p = tmp_path_factory.mktemp("db") / "t.db"
    con = db.connect(p)
    db.load_congress(con, CONGRESS, 118)
    db.load_congress(con, CONGRESS, 119)
    return p


def test_load_and_enacted(database):
    con = db.connect(database)
    hr695 = db.get_bill(con, "hr695-119")
    assert hr695["enacted"] == 1 and json.loads(hr695["enacted_as"])["number"] == "43"
    assert db.get_bill(con, "hr206-119")["enacted"] == 0
    assert db.get_bill(con, "hr8-119")["status"] == "RESERVED"


def test_search(database):
    con = db.connect(database)
    assert [r["bill_id"] for r in db.search(con, q="H.R. 206").rows] == ["hr206-119"]
    assert "hr206-119" in [r["bill_id"] for r in db.search(con, q="housing vouchers").rows]
    assert db.search(con, q="medal of honor", congress=118).rows[0]["bill_id"] == "hr1514-118"
    assert [r["bill_id"] for r in db.search(con, status="enacted").rows] == ["hr695-119"]
    nehls = db.search(con, sponsor="Nehls").rows
    assert {r["bill_id"] for r in nehls} >= {"hr1514-118"}
    assert db.search(con, chamber="senate", congress=119).total == 1


def test_same_title_in_next_congress(database):
    con = db.connect(database)
    later = db.same_title_in(con, "hr1514-118", 119)
    assert [b["bill_id"] for b, _ in later] == ["hr695-119"]


@pytest.fixture(scope="module")
def client(database, tmp_path_factory):
    import os
    os.environ["REINTRO_DB"] = str(database)
    os.environ["REINTRO_CACHE"] = str(tmp_path_factory.mktemp("cache"))
    os.environ["REINTRO_CHECKS"] = "off"
    from reintro.web import app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app)


def test_pages(client):
    assert client.get("/").status_code == 200
    r = client.get("/search", params={"q": "landlord"})
    assert r.status_code == 200 and "H.R. 206" in r.text
    r = client.get("/bills/hr206-119")
    assert r.status_code == 200
    assert "Draft for the 120th Congress" in r.text and 'class="warning' in r.text
    r = client.get("/bills/hr695-119")
    assert "This bill became law as Public Law 119-43" in r.text and "Draft for the" not in r.text
    for p in ("/guideline", "/research", "/plan", "/selection"):
        assert client.get(p).status_code == 200


def test_downloads(client):
    r = client.get("/bills/hr206-119/draft.xml")
    assert r.status_code == 200 and b"<congress>120</congress>" in r.content
    r = client.get("/bills/hr206-119/draft.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert client.get("/bills/hr695-119/draft.xml").status_code == 409
    r = client.get("/download", params={"ids": "hr206-119,hr695-119", "pdf": 0})
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"reintroduction-draft-hr206-119.xml", "warnings.csv", "skipped.txt"}


def test_api(client):
    r = client.get("/api/v1/search", params={"q": "hr206"}).json()
    assert r["total"] == 1 and r["results"][0]["citation"] == "H.R. 206"
    d = client.get("/api/v1/bills/hr206-119/draft", params={"check": 0}).json()
    assert d["warnings"] and d["source"]["congress"] == 119
