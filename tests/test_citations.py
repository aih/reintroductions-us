from conftest import bill_text
from reintro import citations, reintroduce, uslm


def test_medal_of_honor_pension(clients):
    """H.R. 1514 (118th) strikes "$1,388.68" from 38 U.S.C. 1562; Pub. L. 119-43
    rewrote that paragraph, so the words are gone and the section has changed."""
    us, st = clients
    conv = uslm.convert(bill_text(118, "hr", 1514, "ih"), version="ih")
    d = reintroduce.reintroduce(conv.tree, to_congress=119, source={"bill_type": "hr", "date": "2023-03-09"})
    rep = citations.check(d, us, st, since="2023-03-09")
    kinds = {w.kind: w for w in d.warnings}
    assert rep["usc_sections"] == 1
    assert "Pub. L. 119-43" in kinds["citation-amended"].message
    assert "$1,388.68" in kinds["struck-text-not-found"].message
    xml = uslm.to_bytes(d.tree).decode()
    assert "REINTRO-WARNING struck-text-not-found" in xml


def test_no_warning_before_the_change(clients):
    us, st = clients
    conv = uslm.convert(bill_text(118, "hr", 1514, "ih"), version="ih")
    d = reintroduce.reintroduce(conv.tree, to_congress=119, source={"bill_type": "hr"})
    citations.check(d, us, st, since="2026-10-01")
    assert "citation-amended" not in {w.kind for w in d.warnings}


def test_written_citations_are_found():
    from lxml import etree
    from reintro.uslm import U
    c = etree.Element(U("content"))
    c.text = "Section 1562(a)(1) of title 38, United States Code, and 42 U.S.C. 1395w–4 are cited."
    root = etree.Element(U("main"))
    root.append(c)
    found = {(x.title, x.section) for x in citations.collect_usc(root)}
    assert found == {("38", "1562"), ("42", "1395w-4")}
