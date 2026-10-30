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
