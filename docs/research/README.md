---
title: How bills change when they are reintroduced
---

# How bills change when they are reintroduced

A comparison of the House and Senate bills introduced from January 3 to January 31,
2025 (the first month of the 119th Congress) with their versions in the 118th
Congress. The [guideline](/guideline) is written from these figures.

## Data

- Bill status: GovInfo `BILLSTATUS` bulk data for `hr` and `s` in the 118th and
  119th Congresses, converted to `data.json` by
  [unitedstates/congress](https://github.com/unitedstates/congress) (`usc-run bills`).
- Bill text: GovInfo `BILLS` bulk data (bill DTD XML), every version, laid out
  as `text-versions/{version}/document.xml` the way `usc-run govinfo` stores it.
- Downloaded 2026-10-30.

## Method

1. **Matching.** The bulk data records no relationship between a bill and its
   reintroduction ([Related bills](#related-bills) below). Each 119th bill is
   matched to 118th bills by title ([`reintro/matching.py`](https://github.com/aih/reintroductions-us/blob/main/reintro/matching.py)):
   the normalised official title (0.6, or 0.3 when more than three bills share it),
   each short title with its trailing year removed (0.5, or 0.25), and the same
   sponsor (+0.3), capped at 1.0. The best candidate at 0.5 or above is the match;
   among equal scores, the latest introduced. Normalising lower-cases the title,
   turns punctuation into spaces and collapses whitespace; "Medal of Honor Act of
   2023" and "Medal of Honor Act of 2025" are both `medal of honor act`.
2. **Text.** The introduced texts of both bills are reduced to plain text and
   compared word by word ([Text similarity](#text-similarity) below).
3. **Header.** Sponsor, cosponsors, committees and titles are compared from the
   bill DTD's `<form>`.
4. **Features.** For pairs whose texts are at least 50% similar, years, fiscal
   years, taxable years, "not later than" dates, citations (`<external-xref>`),
   section counts and committee names are counted over the whole texts.

### Text similarity

One score per pair of bills, computed over the whole body of each
([`compare_texts`](https://github.com/aih/reintroductions-us/blob/main/research/compare_reintroductions.py) in
`research/compare_reintroductions.py`). Sections are not compared one by one.

- **Text.** The body is the `<legis-body>` (or `<resolution-body>`) of the bill DTD
  XML, reduced to plain text by [`body_text`](https://github.com/aih/reintroductions-us/blob/main/reintro/billdtd.py): one line per
  enumerated element, with its number and heading ("SEC. 2. Definitions."), and
  the table of contents left out. The `<form>` (titles, sponsors, committees) is
  not part of it; the header comparison covers those.
- **Words.** Each text is split on whitespace. Punctuation stays attached and case
  is kept, so `2023.` and `2026.` are two different words, as are `Act` and `act`.
- **Algorithm.** Python's
  [`difflib.SequenceMatcher`](https://docs.python.org/3/library/difflib.html#sequencematcher-objects)
  over the two word lists, with `autojunk=False` so that common words are not
  discarded. It is the Ratcliff/Obershelp algorithm
  ([gestalt pattern matching](https://en.wikipedia.org/wiki/Gestalt_pattern_matching)):
  find the longest run of words the two texts share, then do the same on the parts
  before and after it, recursively. The score is `ratio()` = 2 × M / T, where M is
  the number of words in shared runs and T the number of words in both texts.
- **Versions.** The new bill's introduced text is compared with the old bill's
  introduced text (`vs_old_introduced` in `comparisons.json`) and, when the old
  bill had a later version (reported, engrossed), with that one as well
  (`vs_old_latest`).
- **Differences.** Each run that differs is classified by pattern: `year` (only a
  four-digit year changed), `fiscal-year`, `citation` (U.S.C., Public Law, Stat.,
  "section N"), `date` (a month name), `dollar-amount`, `wording`, `inserted` or
  `deleted`. `comparisons.json` keeps the counts and up to twelve examples per pair.

Examples:

- [H.R. 899](https://www.congress.gov/bill/119th-congress/house-bill/899) (119th)
  against [H.R. 899](https://www.congress.gov/bill/118th-congress/house-bill/899)
  (118th): 17 words each. One word differs: "shall terminate on December 31,
  2023." became "… 2026.", classified `year`. Score 2 × 16 / 34 = 0.9412. The two were
  matched on the official title and the sponsor (0.9).
- [H.R. 28](https://www.congress.gov/bill/119th-congress/house-bill/28) (119th)
  against [H.R. 734](https://www.congress.gov/bill/118th-congress/house-bill/734)
  (118th): 188 words, then 418. 183 of the old words are kept in shared runs and
  235 words are added. Score 2 × 183 / 606 = 0.604: a bill that keeps all of its
  old text and doubles in length scores about 0.6.
- [H.R. 769](https://www.congress.gov/bill/119th-congress/house-bill/769) (119th)
  against [S. 5569](https://www.congress.gov/bill/118th-congress/senate-bill/5569)
  (118th): matched on a short title alone (0.5), score 0.0292. Two different bills
  that share a name.

### Related bills

"Identical" and "related" bills come from Congress.gov's Related Bills tab, as
published in GovInfo's
[BILLSTATUS bulk data](https://www.govinfo.gov/bulkdata/BILLSTATUS). Each
`<relatedBills>` item names the other bill and one or more
`<relationshipDetails>`, each a `<type>` and an `<identifiedBy>` (House, Senate or
CRS). Congress.gov's [About Related Bills](https://www.congress.gov/help/related-bills)
page and [glossary](https://www.congress.gov/help/legislative-glossary) describe
the types:

- **Identical bill:** "a bill that is word-for-word identical to another bill".
  CRS records it between introduced texts only.
- **Related bill:** "a companion measure, an identical bill, a
  procedurally-related measure, or one with text similarities". A companion
  measure is "identical or substantially similar" and introduced in the other
  chamber; text similarities means "substantially similar in both text and
  meaning".
- **Procedurally-related:** a measure "that affects consideration of other
  legislation", such as the rule for considering a bill.
- These relationships "refer only to same-Congress measures". They are assigned
  by people at CRS, the House or the Senate reading the texts; Congress.gov does
  not publish a similarity score or a threshold.

In August 2026 Congress.gov added three types:
[Contained in public law and Reintroduction](https://content.govdelivery.com/accounts/USLOC/bulletins/423a378),
and [Cross-Congress related bill](https://content.govdelivery.com/accounts/USLOC/bulletins/426e7a4).
A Reintroduction links bills in consecutive Congresses with "the same official
title, same sponsor, and introduced text versions that are identical or nearly
identical". A Cross-Congress related bill is a measure "substantially similar,
in both text and meaning", introduced no more than two Congresses apart. In the
BILLSTATUS files checked on 2026-10-31:

- [H.R. 8878](https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hr/BILLSTATUS-119hr8878.xml)
  (119th, updated 2026-09-19) lists H.R. 6644 with two details, `Related bill`
  and `Contained in public law`, both by CRS. H.R. 6644 became Public Law 119-101.
- [H.R. 1318](https://www.govinfo.gov/bulkdata/BILLSTATUS/118/hr/BILLSTATUS-118hr1318.xml)
  (118th), Congress.gov's own Reintroduction example, lists only S. 886 (Identical
  bill, CRS). Its file was last updated 2026-01-15, before the new types.

[unitedstates/congress](https://github.com/unitedstates/congress/blob/main/congress/tasks/bill_info.py)
converts each item to `related_bills` in `data.json` with `reason` (the first
detail's type, lower-cased, without "bill": `identical`, `related`) and
`identified_by`. It keeps the first detail only, so H.R. 8878's
`Contained in public law` is not in its `data.json`.

The site marks a bill "A related bill became law" when another bill of the same
Congress became law and either is listed in its `related_bills` with reason
`identical` or `related`, or matches it by title at 0.6 or more with the scores
above ([`_mark_related_enacted`](https://github.com/aih/reintroductions-us/blob/main/reintro/db.py)). The bill page names the law
and the reason, for example "related bill (CRS)" or "same title: official title,
same sponsor". In the 119th Congress, H.R. 8878 is marked through H.R. 6644.

## Results

| | |
|---|---|
| Bills introduced in the window | 1,223 |
| Matched to a 118th bill with text | 1,088 |
| Not matched | 125 |
| Identical body text | 316 |
| Median similarity | 0.9956 |
| Similarity ≥ 0.95 | 840 |
| Similarity < 0.80 | 136 |
| Pairs used for features (similarity ≥ 0.5) | 1,046 |

Header ([summary.json](summary.json)):

| | % of matched pairs |
|---|---|
| Same sponsor | 85.9 |
| Same official title | 89.8 |
| Same short titles | 57.7 |
| Short titles differ only in the year | 31.4 |
| Same committees | 94.4 |
| Old cosponsors among the new original cosponsors | 44.6 |

43 pairs moved from one chamber to the other. 175 old bills had a version later
than the introduced one; for 85 of them the new text is closer to that later
version.

Features ([features-summary.json](features-summary.json)), counts of bills:

| | Bills |
|---|---|
| Cite the US Code or a Public Law | 769 |
| Amend existing law ("is amended") | 632 |
| Change at least one year | 451 |
| Strike and insert | 395 |
| Use "date of enactment" | 281 |
| Name a fiscal year / changed one | 120 / 80 |
| Changed at least one citation | 91 |
| Changed the number of sections | 77 |
| Set taxable years / changed them | 55 / 48 |
| Set a fixed "not later than" date / changed it | 22 / 15 |

Year changes by distance (new − old): +2: 652, +1: 394, +3: 19, +4: 7, −1: 4,
others 1 each.

Across the whole 119th Congress so far, 39 introduced texts name the House
Committee on Oversight and Accountability, renamed the Committee on Oversight and
Government Reform at the start of that Congress.

For scope: of the 16,213 House and Senate bills of the 118th Congress, 269 were
enacted. 8,526 of the other 15,944 have a title match among the 119th Congress's
bills as of October 2026, 1,132 of them introduced in January 2025.

## Limitations

- Title matching misses a bill renamed between Congresses and can pair two bills
  that share a short title but not their text: 42 matched pairs are less than 50%
  similar, and several of the lowest are different bills.
- The text comparison sees words, not structure: a moved section counts as a
  deletion and an insertion, and a renumbered section changes its number's word.
- A score is for the whole bill. A long bill with one rewritten section scores
  higher than a short bill with one changed sentence.
- [S. 252](https://www.congress.gov/bill/119th-congress/senate-bill/252) against
  [S. 791](https://www.congress.gov/bill/118th-congress/senate-bill/791) scores 0.0.
  GPO's XML for S. 791 as introduced has an empty `<legis-body>` before the one
  that holds the text, and the extraction read the empty one. The extraction now
  takes the first body with content; the figures above are from before that fix.
- The year, date and committee counts are pattern matches over plain text. A
  fiscal year in the name of another law ("for Fiscal Year 2021") is counted as a
  fiscal year.
- Only the first month of one Congress is measured.

## Reproduce

```
usc-run govinfo --bulkdata=BILLSTATUS --congress=118,119 --type=hr,s
usc-run bills --congress=118,119
usc-run govinfo --bulkdata=BILLS --congress=118,119 --type=hr,s   # or reintro ingest
uv run python research/compare_reintroductions.py DATA_DIR --congress 119 \
    --from 2025-01-03 --to 2025-01-31 --out docs/research
uv run python research/compare_reintroductions.py features DATA_DIR --out docs/research
```

`comparisons.json` holds every matched pair with its scores and up to twelve
differences; `features.json` is not committed (2.5 MB) and is regenerated by the
second command.
