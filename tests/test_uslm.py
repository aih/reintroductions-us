import difflib
import re

import pytest
from lxml import etree

from conftest import FIX, bill_text
from reintro import uslm

SCHEMA = etree.XMLSchema(etree.parse(str(FIX.parents[1] / "reintro" / "schema" / "uslm-2.1.0.xsd")))
NS = {"u": uslm.USLM_NS}
PAIRS = [("BILLS-116hr3945ih.xml", "H3945_IH.XML"), ("BILLS-116hr1000ih.xml", "H1000_IH.XML"),
         ("BILLS-116s3874is.xml", "S3874_IS.XML"), ("BILLS-116hres1000ih.xml", "HR1000_IH.XML")]


def _text(tree):
    t = " ".join(tree.find(".//u:main", NS).itertext()).replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).split()


@pytest.mark.parametrize("src,gpo", PAIRS)
def test_conversion_matches_gpo(src, gpo):
    """GPO's own USLM conversion of the same file is the reference."""
    conv = uslm.convert(FIX / "gpo" / src, version=src.split(".")[0][-2:])
    ours = etree.fromstring(uslm.to_bytes(conv.tree))
    assert SCHEMA.validate(ours), SCHEMA.error_log
    assert conv.unknown == {}
    theirs = etree.parse(str(FIX / "gpo" / gpo))
    if "hres" not in src:
        ids_ours = {e.get("identifier") for e in ours.iter() if e.get("identifier")}
        ids_gpo = {e.get("identifier") for e in theirs.iter() if e.get("identifier")}
        assert ids_ours == ids_gpo
    ratio = difflib.SequenceMatcher(None, _text(etree.ElementTree(ours)), _text(theirs), autojunk=False).ratio()
    assert ratio > 0.96


def test_refs_carry_the_cited_subdivision():
    assert uslm.xref_href("usc", "usc/42/5302", "42 U.S.C. 5302(17)") == "/us/usc/t42/s5302/17"
    assert uslm.xref_href("usc", "usc/29/3101", "29 U.S.C. 3101 et seq.") == "/us/usc/t29/s3101/etseq"
    assert uslm.xref_href("public-law", "pl/117/58", "Public Law 117–58") == "/us/pl/117/58"
    assert uslm.xref_href("usc-chapter", "usc-chapter/5/87", "chapter 87") == "/us/usc/t5/ch87"


def test_amending_actions_and_quoted_text():
    conv = uslm.convert(bill_text(118, "hr", 1514, "ih"), version="ih")
    root = conv.tree.getroot()
    acts = [(a.get("type"), a.text) for a in root.iter(f"{{{uslm.USLM_NS}}}amendingAction")]
    assert ("amend", "is amended") in acts and ("delete", "striking") in acts
    qt = [("".join(q.itertext())) for q in root.iter(f"{{{uslm.USLM_NS}}}quotedText")]
    assert "$1,388.68" in qt
    assert SCHEMA.validate(root), SCHEMA.error_log
