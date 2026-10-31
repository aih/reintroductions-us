from conftest import bill_text

from reintro import billdtd


def test_read_bill_header():
    d = billdtd.read_bill(bill_text(118, "hr", 1514, "ih"))
    assert (d.congress, d.session, d.bill_type, d.number) == (118, 1, "hr", 1514)
    assert d.action_date == "2023-03-09"
    assert d.sponsor[0] == "N000026"
    assert d.short_titles == ["Medal of Honor Act"]
    assert d.official_title.startswith("To amend title 38, United States Code")


def test_senate_ids_are_lis_ids():
    d = billdtd.read_bill(bill_text(118, "s", 3401, "is"))
    assert d.sponsor[0].startswith("S") and len(d.sponsor[0]) == 4


def test_body_text_leaves_out_the_table_of_contents():
    t = billdtd.body_text(bill_text(119, "hr", 206, "ih"))
    assert "Landlord Accountability Act of 2025" in t
    assert "Sec. 1." not in t


def test_an_empty_legis_body_before_the_real_one_is_skipped():
    # GPO's S. 791 (118th Congress, introduced) carries `<legis-body/>` and then the text.
    from pathlib import Path

    from reintro import uslm

    src = Path(bill_text(119, "hr", 206, "ih")).read_bytes()
    doubled = src.replace(b"<legis-body", b'<legis-body changed="not-changed"></legis-body><legis-body', 1)
    assert doubled.count(b"<legis-body") == 2
    assert billdtd.body_text(doubled) == billdtd.body_text(src) != ""
    assert billdtd.read_bill(doubled).short_titles == billdtd.read_bill(src).short_titles
    n = lambda b: uslm.to_bytes(uslm.convert(b, version="ih").tree).count(b"<section")
    assert n(doubled) == n(src) > 0
