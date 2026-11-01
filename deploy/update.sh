#!/usr/bin/env bash
# The daily update, as ec2-user from the checkout (deploy/install-crons.sh):
#
#   bash deploy/update.sh [congress]
#
# `reintro update` for the current Congress (usc-run downloads only what
# changed, then the load), then `reintro link` against the Congress before and
# `reintro progress` for it (committee reports and passage, 1.5 s), in
# the `jobs` container while the site keeps serving. With REINTRO_PREGEN_BUCKET
# in .env, `reintro pregen` then queues the PDFs of new and changed drafts for
# the Lambda renderer (deploy/aws-setup.sh). The Congress is
# REINTRO_CONGRESS from .env, else the one in session today. flock on
# ${DATA_ROOT}/update.lock; logs to ${DATA_ROOT}/logs/update.log.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f docker-compose.prod.yml --profile jobs"
DATA_ROOT="$(grep -E '^DATA_ROOT=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
DATA_ROOT="${DATA_ROOT:-/var/lib/reintro}"
CONFIGURED="$(grep -E '^REINTRO_CONGRESS=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"

# A Congress begins on 3 January of an odd year.
year="$(date -u +%Y)"
congress=$(( (year - 1789) / 2 + 1 ))
if [ $(( year % 2 )) -eq 1 ] && [ "$(date -u +%m%d)" \< "0103" ]; then
    congress=$(( congress - 1 ))
fi
CONGRESS="${1:-${CONFIGURED:-$congress}}"

mkdir -p "$DATA_ROOT/logs"
exec >> "$DATA_ROOT/logs/update.log" 2>&1

exec 9>"$DATA_ROOT/update.lock"
if ! flock -n 9; then
    echo "$(date -u +%FT%TZ) update already running"
    exit 1
fi

echo "=== $(date -u +%FT%TZ) update, Congress $CONGRESS ==="
start=$(date +%s)
$COMPOSE run --rm --no-deps jobs reintro update --congress "$CONGRESS"
$COMPOSE run --rm --no-deps jobs reintro link --older "$(( CONGRESS - 1 ))" --newer "$CONGRESS"
$COMPOSE run --rm --no-deps jobs reintro progress --congress "$(( CONGRESS - 1 ))"
if grep -qE '^REINTRO_PREGEN_BUCKET=.+' .env 2>/dev/null; then
    $COMPOSE run --rm --no-deps jobs reintro pregen --congress "$CONGRESS"
fi
echo "=== $(date -u +%FT%TZ) done in $(( $(date +%s) - start )) s ==="
