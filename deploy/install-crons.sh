#!/usr/bin/env bash
# The box's schedule for this site, written whole to /etc/cron.d/reintro:
#
#   sudo bash deploy/install-crons.sh
#
# One job: the daily update (deploy/update.sh). Image pruning is already on
# the statutes site's weekly cron, which prunes for the whole box.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/ec2-user/reintroductions-us}"
DATA_ROOT="${DATA_ROOT:-/var/lib/reintro}"
CRON_FILE=/etc/cron.d/reintro

if [ "$(id -u)" -ne 0 ]; then
    echo "run this as root — it writes $CRON_FILE" >&2
    exit 1
fi

cat > "$CRON_FILE" <<CRON
# Managed by deploy/install-crons.sh in reintroductions-us — edit there, not here.
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin

# Daily 08:17 UTC: new bill text and status from govinfo, then the load.
17 8 * * * root cd ${REPO_DIR} && sudo -u ec2-user bash deploy/update.sh >/dev/null 2>&1
CRON

chmod 0644 "$CRON_FILE"
mkdir -p "${DATA_ROOT}/logs"
chown ec2-user: "${DATA_ROOT}/logs" 2>/dev/null || true

echo "wrote $CRON_FILE:"
sed 's/^/    /' "$CRON_FILE"
