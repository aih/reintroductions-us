# Reintroductions

A site that prepares a bill of the most recent Congress for reintroduction in the
next one. It downloads every bill with
[unitedstates/congress](https://github.com/unitedstates/congress), marks the ones
that became law, and for any other bill produces a draft in USLM 2.0 XML and a PDF
marked "Draft — not for introduction" on every page. The draft carries the new
Congress and session, moves the year in the bill's own short titles, and marks in
XML comments each place that needs review: cited US Code sections amended since the
bill was written (from [uscode.linkedlegislation.org](https://uscode.linkedlegislation.org)),
words it strikes that are no longer in the law, Public Laws amended since (from
[statutes.linkedlegislation.org](https://statutes.linkedlegislation.org)), fiscal
years and dates the bill sets, renamed committees and findings.

- [docs/guideline.md](docs/guideline.md): what changes between Congresses, shown on the site at `/guideline`.
- [docs/research/README.md](docs/research/README.md): the comparison of 1,223 bills of January 2025 with their 118th versions.
- [docs/deploy.md](docs/deploy.md): reintro.linkedlegislation.org on the shared EC2 box, its sizing, and the serverless pre-generation plan.
- [docs/plan.md](docs/plan.md): the data, the pipeline, the warnings, the site workflow, research still owed, and API tokens for the two linkedlegislation sites.

## Run it

```
uv sync --extra ingest                 # unitedstates/congress's usc-run comes with the extra
uv run reintro update --congress 119   # usc-run govinfo (BILLSTATUS, BILLS) + usc-run bills, then load
uv run reintro update --congress 118   # optional: the Congress before, for "already reintroduced" links
uv run reintro serve                   # http://127.0.0.1:8020
```

`usc-run` keeps a last-modified date per file and downloads only what changed, so
`reintro update` can run daily. One bill from the command line:

```
uv run reintro draft hr206-119 --out hr206.xml --pdf hr206.pdf
```

| Variable | Default | |
|---|---|---|
| `REINTRO_USC_ROOT` | `data/usc` | `usc-run`'s working directory (`data/` and `cache/` inside it) |
| `REINTRO_DB` | `data/reintro.db` | SQLite database with the FTS5 search index |
| `REINTRO_CACHE` | `data/cache` | Drafts and API answers |
| `REINTRO_CHECKS` | `on` | `off` builds drafts without calling the two linkedlegislation sites |
| `REINTRO_PDF_MAX_SOURCE_BYTES` | `1000000` | Longest source text whose PDF is rendered on request; also the PDF budget of one zip |
| `REINTRO_PDF_CONCURRENCY` | `1` | PDF renders at once |
| `REINTRO_PREGEN_BUCKET`, `REINTRO_PREGEN_QUEUE_URL` | — | The S3 bucket and SQS queue of the Lambda PDF renderer (`reintro pregen`, [docs/deploy.md](docs/deploy.md)) |
| `USCODE_API_TOKEN`, `STATUTES_API_TOKEN` | — | Sent as `Authorization: Bearer`; generating and storing them is [docs/api-tokens.md](docs/api-tokens.md) |

The variables can be kept in `.env` (copied from `.env.example`, gitignored) and passed with
`uv run --env-file .env reintro …`.

## Layout

| Module | |
|---|---|
| `reintro/congress_data.py` | Reads `usc-run` output: `data.json` and text versions |
| `reintro/billdtd.py` | Reads GPO bill DTD XML |
| `reintro/uslm.py` | Bill DTD → USLM 2.0; checked against GPO's conversions in `tests/fixtures/gpo` |
| `reintro/reintroduce.py` | The draft: Congress, session, short titles, warnings |
| `reintro/citations.py`, `reintro/linkedleg.py` | Citation checks and the API clients |
| `reintro/db.py` | SQLite schema, load, search, enacted and related-law marking |
| `reintro/render.py`, `reintro/pdf.py` | HTML and the watermarked PDF (WeasyPrint) |
| `reintro/pregen.py` | PDFs rendered ahead of time on Lambda: the planner, the handler, the sync |
| `reintro/web/` | The FastAPI site and its JSON API (`/api/v1/search`, `/api/v1/members`, `/api/v1/bills/{id}`, `/api/v1/bills/{id}/draft`) |
| `research/compare_reintroductions.py` | The research comparison |
| `reintro/schema/` | USLM 2.1.0 schema from [usgpo/uslm](https://github.com/usgpo/uslm) |

## Tests

```
uv run pytest
```

The citation tests use answers recorded from uscode.linkedlegislation.org
(`tests/fixtures/linkedleg.json`) and make no network calls.
