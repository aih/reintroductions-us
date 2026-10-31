#!/usr/bin/env bash
# Whether .github/workflows/deploy.yml deploys <sha>:
#
#   bash deploy/deploy-gate.sh <sha> [healthz-url]
#
# Prints `deploy=true` or `deploy=false` on stdout, for $GITHUB_OUTPUT, and
# the reason on stderr. `false` only when the commit the site reports on
# /healthz is <sha> or an ancestor of it, and every path changed between the
# two is one the running site does not read: a *.md file at the top level,
# .github/, tests/, lambda/, deploy/aws-setup.sh or deploy/lambda-deploy.sh.
# docs/ is not on that list: the site serves docs/*.md as pages. Anything else
# is `true`: /healthz not answering, `commit` unknown, a commit this checkout
# does not hold, a deployed commit that is not an ancestor. Needs the full
# history (`fetch-depth: 0`).
set -euo pipefail
cd "$(dirname "$0")/.."

NEW="${1:?usage: deploy-gate.sh <sha> [healthz-url]}"
URL="${2:-https://reintro.linkedlegislation.org/healthz}"

decide() {
    echo "deploy-gate: $2" >&2
    echo "deploy=$1"
    exit 0
}

DEPLOYED="$(curl -sf --max-time 10 --retry 2 "$URL" | jq -r '.commit // empty' 2>/dev/null || true)"
if [ -z "$DEPLOYED" ] || [ "$DEPLOYED" = "unknown" ]; then
    decide true "$URL did not name a commit"
fi
if ! git cat-file -e "${DEPLOYED}^{commit}" 2>/dev/null; then
    decide true "the deployed commit $DEPLOYED is not in this checkout"
fi
if ! git merge-base --is-ancestor "$DEPLOYED" "$NEW"; then
    decide true "the deployed commit $DEPLOYED is not an ancestor of $NEW"
fi

CHANGED="$(git diff --name-only "$DEPLOYED" "$NEW")"
if [ -z "$CHANGED" ]; then
    decide false "$NEW changes no file since the deployed commit $DEPLOYED"
fi
SERVED="$(echo "$CHANGED" | awk '!/^[^\/]+\.md$/ && !/^\.github\// && !/^tests\// && !/^lambda\// \
    && $0 != "deploy/aws-setup.sh" && $0 != "deploy/lambda-deploy.sh"')"
if [ -n "$SERVED" ]; then
    decide true "$(echo "$SERVED" | wc -l | tr -d ' ') changed path(s) the site reads since $DEPLOYED, first $(echo "$SERVED" | head -1)"
fi
decide false "nothing the site reads changed since the deployed commit $DEPLOYED: $(echo "$CHANGED" | wc -l | tr -d ' ') path(s)"
