#!/usr/bin/env bash
# Builds and starts reintro.linkedlegislation.org on the box, as ec2-user from
# the checkout (docs/deploy.md):
#
#   bash deploy/deploy-on-box.sh [git-ref]     default origin/main
#
# Checks out the ref, validates deploy/Caddyfile with the proxy's image, builds
# the image on the box and runs `up -d --build --wait`, then fetches /healthz
# through the edge. flock on ${DATA_ROOT}/deploy.lock; everything logs to
# ${DATA_ROOT}/logs/deploy.log. It never touches the edge or the other sites.
set -euo pipefail
cd "$(dirname "$0")/.."

REF="${1:-origin/main}"
COMPOSE="docker compose -f docker-compose.prod.yml"

env_value() {  # env_value NAME [default]: NAME from .env, or the default
    local v
    v="$(grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | sed -e "s/^['\"]//" -e "s/['\"]$//")"
    echo "${v:-${2:-}}"
}

if [ "$(id -u)" -eq 0 ]; then
    echo "run this as ec2-user, not root: files it creates must stay writable by the cron jobs" >&2
    exit 1
fi
if [ ! -f .env ]; then
    echo ".env is missing (docs/deploy.md, step 3)" >&2
    exit 1
fi
DATA_ROOT="$(env_value DATA_ROOT /var/lib/reintro)"
SITE_ADDRESS="$(env_value SITE_ADDRESS)"
if [ -z "$SITE_ADDRESS" ]; then
    echo "SITE_ADDRESS not set in .env" >&2
    exit 1
fi
HOST="$(echo "$SITE_ADDRESS" | sed -E 's#^[a-z]+://##; s#:[0-9]+$##')"

mkdir -p "$DATA_ROOT/logs" "$DATA_ROOT/caddy"
exec > >(tee -a "$DATA_ROOT/logs/deploy.log") 2>&1

exec 9>"$DATA_ROOT/deploy.lock"
if ! flock -n 9; then
    echo "deploy already running"
    exit 1
fi

echo "=== $(date -u +%FT%TZ) deploying $REF ==="
git fetch --quiet origin
git checkout --quiet --detach "$REF"
echo "at $(git rev-parse --short HEAD)"

echo "=== validating deploy/Caddyfile ==="
docker run --rm \
    -e SITE_ADDRESS="$SITE_ADDRESS" \
    -e GATE_HOST="$(env_value GATE_HOST "$HOST")" \
    -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
    caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

# compose builds through buildx; the box's Docker had no buildx plugin on
# 2026-10-30, since the other two sites pull prebuilt images.
if ! docker buildx version >/dev/null 2>&1; then
    echo "docker buildx is not installed; as root (docs/deploy.md, step 4):" >&2
    echo "  mkdir -p /usr/local/lib/docker/cli-plugins" >&2
    echo "  curl -sSL https://github.com/docker/buildx/releases/download/v0.38.1/buildx-v0.38.1.linux-arm64 -o /usr/local/lib/docker/cli-plugins/docker-buildx" >&2
    echo "  chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx" >&2
    exit 1
fi

echo "=== building and starting ==="
$COMPOSE up -d --build --wait --force-recreate app proxy

echo "=== checking through the edge ==="
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    --resolve "$HOST:443:127.0.0.1" "https://$HOST/healthz" || true)"
if [ "$code" = "200" ]; then
    echo "https://$HOST/healthz: 200"
else
    echo "https://$HOST/healthz: ${code:-no answer} (the edge block or DNS is not in place yet; docs/deploy.md, steps 5 and 6)"
fi

docker image prune -f >/dev/null
echo "=== $(date -u +%FT%TZ) done ==="
