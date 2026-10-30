"""Compare bills introduced early in a Congress with their versions in the Congress
before, and classify what changed.

    uv run python research/compare_reintroductions.py DATA_DIR --congress 119 \
        --from 2025-01-03 --to 2025-01-31 --out docs/research

DATA_DIR is unitedstates/congress output holding both Congresses' bill status
(data.json) and text versions (document.xml). Writes `comparisons.json` (one row
per matched pair) and `summary.json` (the counts the guideline quotes).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reintro import billdtd  # noqa: E402
from reintro.congress_data import iter_bills  # noqa: E402
from reintro.matching import TitleIndex  # noqa: E402

YEAR = re.compile(r"\b(19|20)\d\d\b")
FY = re.compile(r"fiscal years?\b", re.I)
DOLLARS = re.compile(r"\$[\d,\.]+")
USC = re.compile(r"U\.S\.C\.|United States Code", re.I)
PUBLIC_LAW = re.compile(r"Public Law \d+[–-]\d+|Stat\. \d+", re.I)
SECTION_NUM = re.compile(r"\bsection \d+[a-z\-–]*", re.I)
DATE_WORDS = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b")


def tokens(s: str) -> list[str]:
    return re.findall(r"\S+", s)


def classify_change(old: str, new: str) -> set[str]:
    """Kinds of a replaced run of words."""
    kinds = set()
    both = old + " " + new
    if YEAR.search(old) and YEAR.search(new) and YEAR.sub("Y", old) == YEAR.sub("Y", new):
        kinds.add("year")
    if DOLLARS.search(both):
        kinds.add("dollar-amount")
    if FY.search(both) or (YEAR.search(both) and re.search(r"through|for fiscal", both)):
        kinds.add("fiscal-year")
    if USC.search(both) or PUBLIC_LAW.search(both) or SECTION_NUM.search(both):
        kinds.add("citation")
    if DATE_WORDS.search(both):
        kinds.add("date")
    if not kinds:
        kinds.add("wording")
    return kinds


def compare_texts(old: str, new: str) -> dict:
    a, b = tokens(old), tokens(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    kinds = Counter()
    examples = []
    changed_words = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        o, n = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        changed_words += max(i2 - i1, j2 - j1)
        if op == "replace":
            ks = classify_change(o, n)
        elif op == "insert":
            ks = {"inserted"} | ({"year"} if YEAR.search(n) and len(b[j1:j2]) <= 3 else set())
        else:
            ks = {"deleted"} | ({"year"} if YEAR.search(o) and len(a[i1:i2]) <= 3 else set())
        for k in ks:
            kinds[k] += 1
        if len(examples) < 12:
            ctx_before = " ".join(a[max(0, i1 - 6):i1])
            examples.append({"op": op, "kinds": sorted(ks), "before": ctx_before, "old": o[:300], "new": n[:300]})
    return {
        "similarity": round(sm.ratio(), 4),
        "old_words": len(a), "new_words": len(b), "changed_words": changed_words,
        "change_kinds": dict(kinds), "examples": examples,
    }


def header_changes(old: billdtd.BillDoc, new: billdtd.BillDoc) -> dict:
    o_cos = {c[0] for c in old.cosponsors}
    n_cos = {c[0] for c in new.cosponsors}
    o_cmt = {c[0] for c in old.committees}
    n_cmt = {c[0] for c in new.committees}
    o_st = set(old.short_titles)
    n_st = set(new.short_titles)
    return {
        "same_sponsor": bool(old.sponsor and new.sponsor and old.sponsor[0] == new.sponsor[0]),
        "cosponsors_old": len(o_cos), "cosponsors_new": len(n_cos), "cosponsors_kept": len(o_cos & n_cos),
        "committees_same": o_cmt == n_cmt,
        "committees_old": sorted(o_cmt), "committees_new": sorted(n_cmt),
        "official_title_same": old.official_title == new.official_title,
        "short_titles_same": o_st == n_st,
        "short_title_year_only": o_st != n_st and {YEAR.sub("Y", s) for s in o_st} == {YEAR.sub("Y", s) for s in n_st},
        "short_titles_old": sorted(o_st), "short_titles_new": sorted(n_st),
        "chamber_changed": (old.bill_type or "")[:1] != (new.bill_type or "")[:1],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--congress", type=int, default=119)
    ap.add_argument("--from", dest="date_from", default="2025-01-03")
    ap.add_argument("--to", dest="date_to", default="2025-01-31")
    ap.add_argument("--types", default="hr,s")
    ap.add_argument("--out", type=Path, default=Path("docs/research"))
    args = ap.parse_args(argv)
    types = tuple(args.types.split(","))

    prior = list(iter_bills(args.data_dir, args.congress - 1, types))
    index = TitleIndex(b.key() for b in prior if b.data)
    by_id = {b.bill_id: b for b in prior}

    new_bills = [b for b in iter_bills(args.data_dir, args.congress, types)
                 if b.data and args.date_from <= (b.introduced_at or "") <= args.date_to]

    rows = []
    unmatched = 0
    for nb in sorted(new_bills, key=lambda b: (b.bill_type, b.number)):
        m = index.best(nb.key())
        if not m:
            unmatched += 1
            continue
        ob = by_id[m.candidate.bill_id]
        nt = nb.introduced_text()
        if not nt:
            continue
        o_intro = ob.introduced_text()
        o_latest = ob.latest_text()
        if not o_intro:
            continue
        new_doc = billdtd.read_bill(nt[1])
        new_text = billdtd.body_text(nt[1])
        cmp_intro = compare_texts(billdtd.body_text(o_intro[1]), new_text)
        row = {
            "new": nb.bill_id, "old": ob.bill_id, "score": m.score, "reasons": m.reasons,
            "new_introduced": nb.introduced_at, "old_introduced": ob.introduced_at,
            "old_status": ob.data.get("status"), "old_enacted": ob.enacted,
            "old_latest_version": o_latest[0] if o_latest else None,
            "policy_area": nb.data.get("subjects_top_term"),
            "header": header_changes(billdtd.read_bill(o_intro[1]), new_doc),
            "vs_old_introduced": cmp_intro,
        }
        if o_latest and o_latest[0] != o_intro[0]:
            row["vs_old_latest"] = compare_texts(billdtd.body_text(o_latest[1]), new_text)
        rows.append(row)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "comparisons.json").write_text(json.dumps(rows, indent=1))
    summary = summarize(rows, len(new_bills), unmatched, args)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


def summarize(rows, n_new, unmatched, args) -> dict:
    n = len(rows)
    sims = sorted(r["vs_old_introduced"]["similarity"] for r in rows)
    kinds = Counter()
    bills_with = Counter()
    for r in rows:
        ck = r["vs_old_introduced"]["change_kinds"]
        kinds.update(ck)
        bills_with.update(ck.keys())
    h = [r["header"] for r in rows]
    closer_to_latest = sum(1 for r in rows if "vs_old_latest" in r
                           and r["vs_old_latest"]["similarity"] > r["vs_old_introduced"]["similarity"])
    has_later = sum(1 for r in rows if "vs_old_latest" in r)

    def pct(x):
        return round(100 * x / n, 1) if n else None

    return {
        "congress": args.congress, "window": [args.date_from, args.date_to], "types": args.types,
        "introduced_in_window": n_new, "matched_with_text": n, "unmatched": unmatched,
        "identical_body": sum(1 for s in sims if s == 1.0),
        "similarity_median": sims[n // 2] if n else None,
        "similarity_ge_0_95": sum(1 for s in sims if s >= 0.95),
        "similarity_lt_0_80": sum(1 for s in sims if s < 0.80),
        "bills_with_change_kind": dict(bills_with.most_common()),
        "bills_with_change_kind_pct": {k: pct(v) for k, v in bills_with.most_common()},
        "same_sponsor_pct": pct(sum(x["same_sponsor"] for x in h)),
        "chamber_changed": sum(x["chamber_changed"] for x in h),
        "official_title_same_pct": pct(sum(x["official_title_same"] for x in h)),
        "short_titles_same_pct": pct(sum(x["short_titles_same"] for x in h)),
        "short_title_year_only_pct": pct(sum(x["short_title_year_only"] for x in h)),
        "committees_same_pct": pct(sum(x["committees_same"] for x in h)),
        "cosponsors_kept_pct_of_old": round(100 * sum(x["cosponsors_kept"] for x in h) / max(1, sum(x["cosponsors_old"] for x in h)), 1),
        "old_had_later_version": has_later,
        "closer_to_old_latest_than_introduced": closer_to_latest,
        "old_enacted": sum(1 for r in rows if r["old_enacted"]),
    }



# ---- second pass: features counted over the whole texts, not the diff samples ----

TAXABLE = re.compile(r"taxable years? beginning after (\w+ \d+, )?((19|20)\d\d)", re.I)
FY_RANGE = re.compile(r"fiscal years? ((19|20)\d\d)(?: through ((19|20)\d\d))?", re.I)
NOT_LATER = re.compile(r"not later than (\w+ \d+, ((19|20)\d\d))", re.I)
COMMITTEE_119_RENAMES = {
    # House committees renamed at the start of the 119th Congress (H. Res. 5).
    "Committee on Oversight and Accountability": "Committee on Oversight and Government Reform",
}


def year_shifts(old: str, new: str) -> Counter:
    a, b = tokens(old), tokens(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = Counter()
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace" or (i2 - i1) != (j2 - j1):
            continue
        for x, y in zip(a[i1:i2], b[j1:j2]):
            mx, my = YEAR.search(x), YEAR.search(y)
            if mx and my and YEAR.sub("Y", x) == YEAR.sub("Y", y):
                out[int(my.group(0)) - int(mx.group(0))] += 1
    return out


def features(old_path, new_path) -> dict:
    old, new = billdtd.body_text(old_path), billdtd.body_text(new_path)
    od, nd = billdtd.read_bill(old_path), billdtd.read_bill(new_path)
    f = {}
    f["year_shifts"] = dict(year_shifts(old, new))
    f["taxable_year_old"] = [m.group(2) for m in TAXABLE.finditer(old)]
    f["taxable_year_new"] = [m.group(2) for m in TAXABLE.finditer(new)]
    f["fy_old"] = [m.group(0) for m in FY_RANGE.finditer(old)]
    f["fy_new"] = [m.group(0) for m in FY_RANGE.finditer(new)]
    f["deadline_old"] = [m.group(1) for m in NOT_LATER.finditer(old)]
    f["deadline_new"] = [m.group(1) for m in NOT_LATER.finditer(new)]
    f["renamed_committee_old"] = {k: old.count(k) for k in COMMITTEE_119_RENAMES if k in old}
    f["renamed_committee_new"] = {k: new.count(k) for k in COMMITTEE_119_RENAMES if k in new}
    f["new_name_new"] = {v: new.count(v) for v in COMMITTEE_119_RENAMES.values() if v in new}
    oc = {(c.legal_doc, c.parsable_cite) for c in od.citations}
    nc = {(c.legal_doc, c.parsable_cite) for c in nd.citations}
    f["cites_old"], f["cites_new"], f["cites_kept"] = len(oc), len(nc), len(oc & nc)
    f["cite_kinds"] = dict(Counter(c.legal_doc for c in nd.citations))
    f["date_of_enactment_old"] = old.count("date of enactment")
    f["date_of_enactment_new"] = new.count("date of enactment")
    f["sections_old"] = len(billdtd.parse(old_path).xpath("//legis-body/section|//legis-body/*/section|//legis-body/*/*/section"))
    f["sections_new"] = len(billdtd.parse(new_path).xpath("//legis-body/section|//legis-body/*/section|//legis-body/*/*/section"))
    f["amending"] = len(re.findall(r"is amended", new))
    f["strike_insert"] = len(re.findall(r"by striking", new))
    return f


def features_main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--congress", type=int, default=119)
    ap.add_argument("--out", type=Path, default=Path("docs/research"))
    ap.add_argument("--min-similarity", type=float, default=0.5)
    args = ap.parse_args(argv)
    rows = json.loads((args.out / "comparisons.json").read_text())
    from reintro.congress_data import bill_dir, parse_bill_id

    def intro(bill_id):
        t, n, c = parse_bill_id(bill_id)
        d = bill_dir(args.data_dir, c, t, n) / "text-versions"
        for v in ("ih", "is"):
            if (d / v / "document.xml").exists():
                return d / v / "document.xml"

    agg = Counter()
    shifts = Counter()
    kept = []
    n = 0
    for r in rows:
        if r["vs_old_introduced"]["similarity"] < args.min_similarity:
            continue
        n += 1
        f = features(intro(r["old"]), intro(r["new"]))
        r["features"] = f
        kept.append(r)
        shifts.update({int(k): v for k, v in f["year_shifts"].items()})
        if f["year_shifts"]:
            agg["any_year_shift"] += 1
        if f["taxable_year_old"]:
            agg["has_taxable_year"] += 1
            if f["taxable_year_old"] != f["taxable_year_new"]:
                agg["taxable_year_changed"] += 1
        if f["fy_old"]:
            agg["has_fiscal_year"] += 1
            if f["fy_old"] != f["fy_new"]:
                agg["fiscal_year_changed"] += 1
        if f["deadline_old"]:
            agg["has_fixed_deadline"] += 1
            if f["deadline_old"] != f["deadline_new"]:
                agg["fixed_deadline_changed"] += 1
        if f["renamed_committee_old"]:
            agg["names_renamed_committee"] += 1
            if not f["renamed_committee_new"]:
                agg["renamed_committee_updated"] += 1
        if f["cites_old"]:
            agg["has_citations"] += 1
            if f["cites_old"] != f["cites_kept"] or f["cites_new"] != f["cites_kept"]:
                agg["citations_changed"] += 1
        if f["sections_old"] != f["sections_new"]:
            agg["section_count_changed"] += 1
        if f["amending"]:
            agg["amends_law"] += 1
        if f["strike_insert"]:
            agg["strike_and_insert"] += 1
        if f["date_of_enactment_new"]:
            agg["uses_date_of_enactment"] += 1
    (args.out / "features.json").write_text(json.dumps(kept, indent=1))
    out = {"pairs": n, "min_similarity": args.min_similarity, "counts": dict(agg.most_common()),
           "year_shift_histogram": dict(sorted(shifts.items()))}
    (args.out / "features-summary.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "features":
        features_main(sys.argv[2:])
    else:
        main()
