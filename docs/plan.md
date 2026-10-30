---
title: Research and workflow plan
---

# Research and workflow plan

The site prepares a draft reintroduction of any bill of the most recent Congress
that did not become law. A congressional office searches for bills, selects the
ones to reintroduce, and downloads each as USLM 2.0 XML and as a PDF marked "Draft
— not for introduction". This plan covers the data, the drafting mechanics, the
site's workflow, and the research still owed. The first round of research is in
the [research report](/research); its conclusions are in the [guideline](/guideline).

## Data

| Source | What | How |
|---|---|---|
| GovInfo `BILLSTATUS` | Titles, sponsor, cosponsors, committees, subjects, policy area, actions, laws | `usc-run govinfo --bulkdata=BILLSTATUS`, then `usc-run bills` → `data.json` per bill ([unitedstates/congress](https://github.com/unitedstates/congress)) |
| GovInfo `BILLS` | Every text version, bill DTD XML | `usc-run govinfo --bulkdata=BILLS` → `text-versions/{version}/document.xml` |
| GovInfo `BILLS/uslm` | Enrolled bills in USLM 2.x | Reference only; introduced and reported versions exist only in the bill DTD |
| [uscode.linkedlegislation.org](https://uscode.linkedlegislation.org) | Each US Code section's history by release point, the classification tables, current text | `/api/v1/sections/{id}/versions`, `/api/v1/classifications/code/{title}/{section}`, `/api/v1/{id}` |
| [statutes.linkedlegislation.org](https://statutes.linkedlegislation.org) | Public Laws by number and section, whether amended since enactment | `/api/v1/us/pl/{congress}/{number}[/s{section}]` |

Both GovInfo collections are polled by `usc-run`, which keeps a `lastmod` per file
and downloads only what changed. `reintro update` runs both commands and reloads
the bills whose `data.json` or text changed. Run daily; the 119th Congress has
about 16,600 House and Senate bills and 1,500 resolutions.

## Pipeline for one bill

1. **Select the source text.** The most advanced version short of enrollment
   (`congress_data.VERSION_ORDER`). An enacted bill (`enacted_as` set) gets no
   draft.
2. **Convert to USLM 2.0.** `reintro/uslm.py` maps the bill DTD to USLM 2.0
   (`<bill>`, `<meta>`, `<preface>`, `<main>`, levels with `@identifier`
   `/us/bill/{congress}/{type}/{number}/s1/a`, `<quotedContent>`, `<ref href>`
   for every `<external-xref>`). It is checked against GPO's own conversions in
   [usgpo/uslm `bill-version-samples-september-2024`](https://github.com/usgpo/uslm/tree/main/bill-version-samples-september-2024),
   which pairs a bill DTD file with GPO's USLM output.
3. **Reintroduce.** `reintro/reintroduce.py` sets the new Congress and session,
   empties the number, date, sponsor, cosponsors and referral, moves the year in
   the bill's own short titles everywhere they occur, and inserts a warning comment
   before every element that needs a person (the kinds are below).
4. **Check citations.** `reintro/citations.py` looks up every cited US Code section
   and Public Law and adds warnings.
5. **Render.** HTML on the site (warnings shown in place), USLM XML for download,
   and a PDF with the watermark on every page.

The draft is generated on request and cached by (bill, source version, US Code
release point).

## Warnings

Each warning is an XML comment directly before the element it is about:

```xml
<!-- REINTRO-WARNING citation-amended: 42 U.S.C. 1395w–4 has changed since
     2023-03-14 (text, 3 versions); laws classified to it since: Pub. L. 119-37,
     Pub. L. 119-75. Review this provision against current law. -->
<paragraph identifier="/us/bill/120/hr/0/s2/a/1">…
```

| Kind | When |
|---|---|
| `citation-amended` | A cited US Code section's text changed after the source text's date |
| `citation-missing` | A cited section is repealed, transferred, omitted or not found |
| `struck-text-not-found` | The words a "striking" instruction quotes are not in the cited section as it reads now |
| `law-amended` | A cited Public Law section has been amended since enactment |
| `short-title-year` | A short title's year was moved (informational) |
| `date-set-by-bill` | A fixed date, taxable year or fiscal year in operative text |
| `fiscal-year-past` | A fiscal year that has begun by the start of the new Congress |
| `renamed-committee` | A committee name that the new Congress no longer uses |
| `findings` | A findings section; statistics and events may need updating |
| `chamber-header` | Fields left empty for the clerk: number, date, sponsor, referral |

A warning is advice. The text is not changed except for the Congress, session and
short-title year.

## Site workflow

1. **Search.** By text (full-text index over the latest version), number
   ("H.R. 1514", "hr1514"), subject or policy area, sponsor and cosponsor. Filters:
   chamber, Congress, status, "has a draft".
2. **Review a bill.** Status, sponsor, cosponsors, versions, whether it became law,
   matching bills already introduced in the current Congress, the draft with its
   warnings in place.
3. **Collect.** Add bills to a selection kept in the browser.
4. **Download.** Per bill or for the whole selection as a zip: USLM 2.0 XML, PDF,
   and a warnings report (CSV).

### Later stages

| Stage | What it adds | Depends on |
|---|---|---|
| Edit | Editing the draft in the browser, accepting or dismissing each warning, re-running the checks | A USLM-aware editor; drafts stored per office |
| Amended-law check | Executing each amendatory instruction against the current text of the provision it amends, and showing the result | Section text from uscode.linkedlegislation.org; compilations (COMPS) from statutes.linkedlegislation.org for laws not in the Code |
| Accounts | Saved selections and drafts per office | Authentication |
| Enacted-elsewhere | Detecting a bill whose provisions became law inside another law | Research item R3 |

## Research still owed

| # | Question | Method | Output |
|---|---|---|---|
| R1 | Do the January 2025 figures hold for other Congresses and the rest of a Congress? | Run `research/compare_reintroductions.py` on 117→118 and on the whole 119th | A second column in the research report |
| R2 | How accurate is title matching? | Hand-label 200 pairs, measure precision and recall; add a text-similarity match (MinHash over word shingles) for renamed bills | A matching score with a measured threshold |
| R3 | Which unenacted bills were enacted inside another law? | Shingle each bill section and each enacted law of the same and next Congress (PLAW USLM); report overlaps above a threshold | An "enacted in Pub. L. X, sec. Y" mark |
| R4 | What does a chamber change require? | Compare the 43 pairs that moved chamber; Senate files carry LIS member ids, House files bioguide ids | Rules for converting a House draft to a Senate one |
| R5 | Which dates does a bill set, and which only mention? | Classify every date and fiscal year in 1,046 pairs by role (effective date, sunset, authorization period, report deadline, name of a law, history) | The `date-set-by-bill` rule, measured |
| R6 | How often does an amendatory instruction fail against current law? | Parse "striking/inserting/redesignating" instructions in the 118th bills, execute them against the current Code section, count failures | The `struck-text-not-found` rate; the amended-law check's design |
| R7 | What do offices and Legislative Counsel need? | Interviews with five offices and with House and Senate Legislative Counsel: who drafts a reintroduction, in what tool (XMetaL), what file they accept | Format and workflow requirements |
| R8 | Is the output valid USLM? | Validate every generated draft against `uslm-2.1.0.xsd` (the schema GPO's bill samples cite) | A validity rate per bill type |
| R9 | Which committees, agencies and officers were renamed? | Diff `committees-current.yaml` ([unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators)) between Congresses; executive-branch renames from the Federal Register | The `renamed-committee` list, maintained |

## API access to the linkedlegislation sites

The checks call uscode.linkedlegislation.org once or twice per cited section; the
119th Congress's introduced bills carry 41,265 US Code citations and 3,714 Public
Law citations. The relevant limits on that site are per caller address: 10 per
second for the classification routes, one call every 5 seconds (burst 5) for the
windowed history (`/versions?from=`), and one budget of 6 per second for every
outside caller together on routes that pin a release point. A bill-by-bill check
fits inside those; checking every bill of a Congress in advance does not.

Plan: an API token, sent as `Authorization: Bearer <token>`, that

- keys the caller's rate-limit buckets on the token instead of the address;
- multiplies each budget by the token's tier;
- exempts the caller from the shared outside-world budget;
- is stored as a SHA-256 hash in the server's settings, named, and revocable by
  removing it.

The same on statutes.linkedlegislation.org, whose law, `cite` and `cited-by` routes
are limited per caller. Pull requests:
[aih/uscode-redesign#100](https://github.com/aih/uscode-redesign/pull/100)
(`USC_API_TOKENS`, ADR-0096) and
[aih/statutes-at-large#28](https://github.com/aih/statutes-at-large/pull/28)
(`STATUTES_API_TOKENS`, ADR-0038). Each site's `scripts/api_token.py NAME --scale N`
makes a token and prints the settings entry; this site reads the tokens from
`USCODE_API_TOKEN` and `STATUTES_API_TOKEN`. Both pull requests are merged; generating, storing and
using the tokens is [docs/api-tokens.md](api-tokens.md).

Until a token is issued, this site uses the unwindowed history
(`/sections/{id}/versions`, not limited by the windowed budget) and computes
"changed since" itself, and caches each answer by the release point it was read at.
