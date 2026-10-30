from reintro.matching import BillKey, TitleIndex, norm_title, short_title_key


def test_short_title_key_drops_the_year():
    assert short_title_key("WEATHER Act of 2023") == short_title_key("WEATHER Act of 2025") == "weather act"
    assert norm_title("Children’s “Act”") == "children s act"


def test_best_prefers_title_and_sponsor():
    old = [
        BillKey("hr1-118", "To do a particular thing for veterans and families.", ("Do Things Act of 2023",), "A1", "2023-01-09"),
        BillKey("hr2-118", "To do another thing.", ("Other Act",), "B2", "2023-02-01"),
    ]
    idx = TitleIndex(old)
    new = BillKey("hr9-119", "To do a particular thing for veterans and families.", ("Do Things Act of 2025",), "A1", "2025-01-03")
    m = idx.best(new)
    assert m.candidate.bill_id == "hr1-118"
    assert m.score == 1.0
    assert idx.best(BillKey("hr10-119", "Unrelated.", ("Nope Act",), "Z", None)) is None
