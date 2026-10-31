---
title: About
---

# About

Reintroductions is a proof of concept by [Ari Hershowitz](https://aih.github.io).
It prepares a bill of the most recent Congress that did not become law as a draft
for the next Congress, in USLM 2.0 XML and as a PDF marked "Draft — not for
introduction" on every page. Drafts prepared here are not for introduction.

## What the site does

- Loads every bill and resolution of the 118th and 119th Congresses and marks the
  ones that became law and the ones a related bill of the same Congress made law.
- Searches them by words, title, number, policy area, sponsor, cosponsor, type and
  date of introduction.
- Builds each draft with the new Congress and session, the year moved in the
  bill's own short titles, and a warning at each place that needs review: cited US
  Code sections amended since the bill was written, words it strikes that are no
  longer in the law, Public Laws amended since, fiscal years and dates, renamed
  committees and findings.
- Downloads up to 50 checked bills at once as XML, PDF and a CSV of the warnings.

## Data

- Bill status and text: GovInfo bulk data (BILLSTATUS and BILLS), fetched with
  [unitedstates/congress](https://github.com/unitedstates/congress).
- US Code citations: [uscode.linkedlegislation.org](https://uscode.linkedlegislation.org).
- Public Law citations: [statutes.linkedlegislation.org](https://statutes.linkedlegislation.org).

The [guideline](/guideline) lists what usually changes between Congresses, the
[research report](/research) the comparison it is drawn from, and the [plan](/plan)
the data, the drafting and the research still owed.

## Source

The code is at [github.com/aih/reintroductions-us](https://github.com/aih/reintroductions-us).
The JSON API is described at [/api/docs](/api/docs).
