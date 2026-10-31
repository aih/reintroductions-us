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


def test_search_filters_sorts_and_snippets(database):
    con = db.connect(database)
    ids = lambda res: [r["bill_id"] for r in res.rows]
    assert ids(db.search(con, congress=119, bill_type="s")) == ["s231-119"]
    assert ids(db.search(con, congress=119, introduced_from="2025-01-10")) == ["hr695-119", "s231-119"]
    assert ids(db.search(con, congress=119, introduced_to="2025-01-03")) == ["hr206-119"]
    assert ids(db.search(con, congress=119, bill_type="hr", sort="number")) == ["hr8-119", "hr206-119", "hr695-119"]
    assert ids(db.search(con, congress=119, bill_type="hr", sort="number-desc")) == ["hr695-119", "hr206-119", "hr8-119"]
    assert ids(db.search(con, congress=118, sort="introduced-desc")) == ["s3401-118", "hr1514-118"]
    assert ids(db.search(con, congress=119, sort="action-desc"))[0] == "hr695-119"
    snip = db.search(con, q="landlord").rows[0]["snippet"]
    assert db.MARK_START + "Landlord" + db.MARK_END in snip
    assert db.search(con, congress=119).rows[0]["snippet"] is None
    assert db.query_terms('landlord "housing vouchers" and tenant') == ["landlord", "housing vouchers", "tenant"]


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
    for p in ("/guideline", "/research", "/plan", "/selection", "/about"):
        assert client.get(p).status_code == 200
    home = client.get("/").text
    assert 'id="related" hidden' in home and "Congressional Research" in home
    assert "Text Analysis Program" in home and 'href="https://github.com/unitedstates/BillMap"' in home
    assert 'class="info" data-toggle="related" aria-expanded="false" aria-controls="related"' in home
    assert '<span class="sitename">Reintroductions</span>' in home and 'rel="icon" href="/static/favicon.svg"' in home
    r = client.get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(b"\x89PNG")
    for f in ("favicon.svg", "favicon-32.png", "apple-touch-icon.png", "site.js"):
        assert client.get(f"/static/{f}").status_code == 200
    assert 'href="https://aih.github.io"' in home and "proof of concept" in home
    assert "Ari Hershowitz" in client.get("/about").text


def test_version_in_footer_and_healthz(client):
    from reintro.version import GIT_COMMIT, SITE_VERSION
    h = client.get("/healthz").json()
    assert h["version"] == SITE_VERSION and h["commit"] == GIT_COMMIT
    home = client.get("/").text
    assert f"version {SITE_VERSION}, commit" in home and 'href="https://github.com/aih/reintroductions-us"' in home


def test_footer_links_a_known_commit(client, monkeypatch):
    from reintro.web import app as appmod
    sha = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setitem(appmod.templates.env.globals, "git_commit", sha)
    home = client.get("/").text
    assert f'href="https://github.com/aih/reintroductions-us/commit/{sha}"><code>0123456</code></a>' in home


def test_search_page_marks_matches_and_offers_checkboxes(client):
    r = client.get("/search", params={"q": "landlord"}).text
    assert "<mark>Landlord</mark>" in r
    assert 'class="sel-check"' in r and 'data-sel="hr206-119"' in r and "data-sel-all" in r
    assert "data-busy" in r and "/static/search.js" in r
    assert "Add to selection" not in r
    r = client.get("/search", params={"congress": 119, "status": "enacted"}).text
    assert 'data-sel="hr695-119"' not in r
    r = client.get("/search", params={"congress": 119, "type": "hr", "sort": "number-desc"}).text
    assert r.index("H.R. 695") < r.index("H.R. 206")
    assert 'value="relevance"' not in r
    r = client.get("/search", params={"congress": 119, "from": "2025-01-10", "sort": "bogus"})
    assert r.status_code == 200 and "H.R. 206" not in r.text
    assert 'class="sel-check"' in client.get("/bills/hr206-119").text


def test_highlight_escapes():
    from reintro.web.app import highlight, snippet_html
    assert str(highlight("<b>Tenants</b> & landlords", ["tenant", "landlord"])) == \
        "&lt;b&gt;<mark>Tenants</mark>&lt;/b&gt; &amp; <mark>landlords</mark>"
    assert str(snippet_html("a <x> \x02b\x03")) == "a &lt;x&gt; <mark>b</mark>"
    assert str(snippet_html("no match here")) == ""


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


def test_a_long_bill_gets_no_pdf_on_request(client, monkeypatch):
    import shutil

    from reintro.web import app as appmod

    shutil.rmtree(appmod.CACHE_DIR / "pdf", ignore_errors=True)
    monkeypatch.setattr(appmod, "PDF_MAX_SOURCE", 10)
    assert "Download PDF" not in client.get("/bills/hr206-119").text
    r = client.get("/bills/hr206-119/draft.pdf")
    assert r.status_code == 413 and "XML" in r.json()["detail"]
    r = client.get("/download", params={"ids": "hr206-119", "pdf": 1})
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "reintroduction-draft-hr206-119.pdf" not in z.namelist()
    assert "PDF not included" in z.read("skipped.txt").decode()


def test_pdfs_are_kept_and_a_zip_has_a_pdf_budget(client, monkeypatch):
    import io
    import shutil
    import zipfile

    from reintro.web import app as appmod

    shutil.rmtree(appmod.CACHE_DIR / "pdf", ignore_errors=True)
    monkeypatch.setattr(appmod, "PDF_MIN_COST", appmod.PDF_MAX_SOURCE + 1)
    r = client.get("/download", params={"ids": "hr206-119", "pdf": 1})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "reintroduction-draft-hr206-119.pdf" not in z.namelist()
    assert "1 MB of bill text" in z.read("skipped.txt").decode()

    first = client.get("/bills/hr206-119/draft.pdf").content
    assert [p.name for p in (appmod.CACHE_DIR / "pdf").iterdir()][0].startswith("hr206-119-")
    assert client.get("/bills/hr206-119/draft.pdf").content == first
    r = client.get("/download", params={"ids": "hr206-119", "pdf": 1})
    assert zipfile.ZipFile(io.BytesIO(r.content)).read("reintroduction-draft-hr206-119.pdf") == first


def test_a_pregenerated_pdf_is_served_past_the_limit(client, monkeypatch):
    from reintro.web import app as appmod

    first = client.get("/bills/hr206-119/draft.pdf").content  # kept in the cache
    monkeypatch.setattr(appmod, "PDF_MAX_SOURCE", 10)
    assert "Download PDF" in client.get("/bills/hr206-119").text
    assert client.get("/bills/hr206-119/draft.pdf").content == first
