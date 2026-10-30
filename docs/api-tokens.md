# API tokens

The citation checks call uscode.linkedlegislation.org and statutes.linkedlegislation.org. Each
site can issue this site a token that raises its rate limits; without one the checks run within
the anonymous limits. Each site's operator makes the token; this site stores and sends it.

## Calls and budgets

| Site | Call | Budget without a token |
|---|---|---|
| US Code | `/sections/us/usc/t{T}/s{S}/versions`, one per cited section | none |
| US Code | `/us/usc/t{T}/s{S}`, one per section whose struck text is checked | none |
| Statutes | `/us/pl/{c}/{n}[/s{sec}]`, one per cited Public Law or section | 120 a burst, 1 a second |

The client spaces its own calls 0.2 seconds apart, so a draft citing many Public Laws can spend
the Statutes budget; on a `429` it waits the `Retry-After` and tries once more, then counts the
call in the draft's `report.errors`. A Statutes token at scale 20 makes that budget 2,400 a
burst and 20 a second. The US Code token raises the limits on the routes the later stages use:
the windowed history (`?from=&to=`, 5 a burst and one every 5 seconds without a token), the
classification tables (120 a burst, 10 a second) and lookups pinned to a release point.

## Generate

On each site's checkout (any machine with Python 3):

```bash
# in uscode-redesign
python3 scripts/api_token.py reintroductions --scale 20
# in statutes-at-large
python3 scripts/api_token.py reintroductions --scale 20
```

Each prints the token once and the settings entry for that site's box. The entry goes into the
site's `.env` as `USC_API_TOKENS` or `STATUTES_API_TOKENS`; each repository's
`docs/api-tokens.md` has the steps. The token is not recoverable; a lost one is replaced by
generating another.

## Store

The tokens are read from the environment:

| Variable | Token for |
|---|---|
| `USCODE_API_TOKEN` | uscode.linkedlegislation.org (`usc_…`) |
| `STATUTES_API_TOKEN` | statutes.linkedlegislation.org (`stat_…`) |

Keep them in a `.env` file in the checkout, which `.gitignore` excludes, with mode 600:

```bash
cp .env.example .env
chmod 600 .env
"${EDITOR:-vi}" .env
```

```bash
USCODE_API_TOKEN=usc_…
STATUTES_API_TOKEN=stat_…
```

and pass it to every command with `uv run --env-file .env`:

```bash
uv run --env-file .env reintro serve
uv run --env-file .env reintro draft hr1514-118 --out hr1514.xml
```

Under systemd, `EnvironmentFile=/path/to/checkout/.env` in the unit does the same. A token in
the shell's environment (`export USCODE_API_TOKEN=…`) works as well and takes precedence over
the file.

## Use

`reintro/linkedleg.py` sends each token as `Authorization: Bearer <token>` on every request to
its site. Nothing else changes: the same calls, the same cache.

| Answer | What the draft shows |
|---|---|
| `401` | The site does not recognise the token: mistyped, revoked, or the site's API was not recreated after its `.env` was edited. The check is skipped and counted in `report.errors`. Fix the variable and rebuild the draft; a `401` is not cached. |
| `403` | The site's proxy refused the User-Agent. The client's is `reintroductions/0.1 (+https://github.com/aih/reintroductions-us)`; one containing `bot`, `crawler` or `spider` is refused. |
| `429` | The budget is spent even at the token's scale; the client waits once, then counts an error. |

To check a token from the checkout:

```bash
set -a; . ./.env; set +a
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $STATUTES_API_TOKEN" https://statutes.linkedlegislation.org/api/v1/us/pl/119/43
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $USCODE_API_TOKEN" https://uscode.linkedlegislation.org/api/v1/classifications/code/38/1562
```

`200` from both means the tokens are installed on both sites.

## Rotate

The site's operator adds a new token beside the old one. Replace the variable in `.env`,
restart `reintro serve`, and tell the operator to remove the old one. Answers already in the
cache (`REINTRO_CACHE/http.db`) stay valid; they do not depend on the token.
