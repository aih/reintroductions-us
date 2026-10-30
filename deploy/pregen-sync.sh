#!/usr/bin/env bash
# Copies the PDFs the Lambda renderer has written to S3 into
# ${DATA_ROOT}/cache/pdf (deploy/install-crons.sh, every 30 minutes). Does
# nothing without REINTRO_PREGEN_BUCKET in .env. Logs to ${DATA_ROOT}/logs/pregen-sync.log.
set -euo pipefail
cd "$(dirname "$0")/.."

grep -qE '^REINTRO_PREGEN_BUCKET=.+' .env 2>/dev/null || exit 0
DATA_ROOT="$(grep -E '^DATA_ROOT=' .env | tail -1 | cut -d= -f2-)"
DATA_ROOT="${DATA_ROOT:-/var/lib/reintro}"
mkdir -p "$DATA_ROOT/logs"
exec >> "$DATA_ROOT/logs/pregen-sync.log" 2>&1

exec 9>"$DATA_ROOT/pregen-sync.lock"
flock -n 9 || exit 0

echo "$(date -u +%FT%TZ) $(docker compose -f docker-compose.prod.yml --profile jobs run --rm --no-deps -T jobs reintro pregen-sync)"
