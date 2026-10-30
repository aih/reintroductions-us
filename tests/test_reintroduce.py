import datetime as dt
import re

from lxml import etree

from conftest import FIX, bill_text
from reintro import reintroduce, uslm

SCHEMA = etree.XMLSchema(etree.parse(str(FIX.parents[1] / "reintro" / "schema" / "uslm-2.1.0.xsd")))
U = uslm.U


def _draft(congress, t, n, v, to):
    conv = uslm.convert(bill_text(congress, t, n, v), version=v)
    src = {"bill_id": f"{t}{n}-{congress}", "bill_type": t, "congress": congress, "number": n, "version": v,
           "date": "2025-01-03", "citation": f"{t} {n}"}
    return reintroduce.reintroduce(conv.tree, to_congress=to, source=src)


def test_header_and_identifiers():
    d = _draft(119, "hr", 206, "ih", 120)
    root = d.tree.getroot()
    pre = root.find(U("preface"))
    assert pre.find(U("congress")).get("value") == "120"
    assert pre.find(U("congress")).text == "120th CONGRESS"
    assert pre.find(U("session")).text == "1st Session"
    assert pre.find(U("docNumber")).text == reintroduce.DRAFT_NUMBER
    assert root.find(U("meta")).find(U("congress")).text == "120"
    ids = [e.get("identifier") for e in root.iter() if isinstance(e.tag, str) and e.get("identifier")]
    assert ids and all(i.startswith("/us/bill/120/hr/draft/") for i in ids)
    assert SCHEMA.validate(root), SCHEMA.error_log


def test_short_title_year_moves_everywhere():
    d = _draft(119, "hr", 206, "ih", 120)
    xml = re.sub(r"<!--.*?-->", "", uslm.to_bytes(d.tree).decode(), flags=re.S)
    assert "Landlord Accountability Act of 2025" not in xml
    assert xml.count("Landlord Accountability Act of 2027") >= 2
    assert any("Landlord Accountability Act of 2027" in c for c in d.changes)


def test_dates_are_flagged_not_changed():
    d = _draft(119, "hr", 206, "ih", 120)
    kinds = [w.kind for w in d.warnings]
    assert "fiscal-year-past" in kinds          # "fiscal years 2026 through 2035" by January 2027
    assert "date-set-by-bill" in kinds          # taxable years
    xml = uslm.to_bytes(d.tree).decode()
    assert "fiscal years 2026 through 2035" in xml
    assert set(k for k, _ in reintroduce.warnings_in(d.tree)) == set(kinds)


def test_warning_comes_before_the_level_not_inside_it():
    d = _draft(119, "hr", 206, "ih", 120)
    for c in d.tree.getroot().iter(etree.Comment):
        if (c.text or "").strip().startswith(reintroduce.WARNING_PREFIX):
            nxt = c.getnext()
            while isinstance(nxt, etree._Comment):
                nxt = nxt.getnext()
            assert nxt is None or nxt.tag != U("content") or nxt.getparent().tag not in reintroduce.LEVEL_TAGS


def test_comment_text_is_well_formed():
    w = reintroduce.Warning("x", "a -- b")
    assert "--" not in w.comment_text()


def test_congress_dates():
    assert reintroduce.congress_start(120) == dt.date(2027, 1, 3)
    assert reintroduce.fiscal_year(dt.date(2027, 1, 3)) == 2027
    assert reintroduce.ordinal(121) == "121st" and reintroduce.ordinal(112) == "112th"
