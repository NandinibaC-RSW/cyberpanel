#!/usr/bin/env bash
# Deploys a commit of this repo to the CyberPanel staging tree on this server.
# Runs ON the server (self-hosted GitHub Actions runner, as user "deploy").
# Usage: deploy-staging.sh <commit-sha>
#
# Design notes:
# - git reset --hard only rewrites TRACKED files; the staging runtime
#   (.env, venv/, public/) is untracked + gitignored and survives every deploy.
# - On health-check failure the tree is reset back to the previous commit
#   and the service restarted, then the run fails loudly.

set -euo pipefail

COMMIT="${1:?usage: deploy-staging.sh <commit-sha>}"
STAGING_DIR="${STAGING_DIR:-/var/www/html/cyberpanel}"
VENV="$STAGING_DIR/venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
SERVICE="cyberpanel-staging.service"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8091/}"
HEALTH_RETRIES=30
HEALTH_INTERVAL=2

say() { echo "[deploy-staging] $*"; }
fail() {
    echo "[deploy-staging] ERROR: $*" >&2
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
        echo "❌ $*" >> "$GITHUB_STEP_SUMMARY"
    fi
    exit 1
}
summary() {
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
        echo "$*" >> "$GITHUB_STEP_SUMMARY"
    fi
}

# --- Preflight: server must have been set up first (docs/DEPLOY-SERVER-SETUP.md) ---
[ -d "$STAGING_DIR/.git" ] || fail "staging tree $STAGING_DIR is not a git clone — run the server setup first"
[ -d "$VENV" ]             || fail "staging venv $VENV missing — run the server setup first"
[ -f "$STAGING_DIR/.env" ] || fail "staging $STAGING_DIR/.env missing — run the server setup first"
[ -d "$STAGING_DIR/public" ] || fail "staging $STAGING_DIR/public/ missing — run the server setup first"
systemctl cat "$SERVICE" >/dev/null 2>&1 || fail "systemd unit $SERVICE not installed — run the server setup first"

cd "$STAGING_DIR"

PREV_COMMIT="$(git rev-parse HEAD)"
say "staging is at $PREV_COMMIT, deploying $COMMIT"

# --- 1. Sync tracked files to the target commit ---
git fetch origin main
git reset --hard "$COMMIT"
say "checked out $COMMIT ($(git log -1 --format='%h %s'))"

# --- 2. Dependencies (requirements.txt is the source of truth once present) ---
if [ -f requirements.txt ]; then
    "$PIP" install -q -r requirements.txt
fi
if [ -f requirements-staging.txt ]; then
    "$PIP" install -q -r requirements-staging.txt
fi

# --- 3. Pre-flight: fail BEFORE touching the running service if the code is broken ---
"$PY" manage.py check

# --- 4. Migrate staging DB ---
"$PY" manage.py migrate --noinput

# --- 5. Restart and health-check ---
sudo -n systemctl restart "$SERVICE"

HEALTHY=0
for i in $(seq 1 "$HEALTH_RETRIES"); do
    if curl -sf -o /dev/null "$HEALTH_URL"; then
        HEALTHY=1
        break
    fi
    sleep "$HEALTH_INTERVAL"
done

if [ "$HEALTHY" != 1 ]; then
    say "health check FAILED after $((HEALTH_RETRIES * HEALTH_INTERVAL))s — rolling back to $PREV_COMMIT"
    git reset --hard "$PREV_COMMIT"
    sudo -n systemctl restart "$SERVICE"
    sleep 5
    curl -sf -o /dev/null "$HEALTH_URL" || say "WARNING: rollback also failed health check — investigate manually"
    fail "deployed code failed health check; rolled back to $PREV_COMMIT"
fi

say "SUCCESS: $COMMIT deployed and healthy (previous: $PREV_COMMIT)"
{
    echo "## Staging deploy ✅"
    echo "- Commit: \`${COMMIT}\` — $(git log -1 --format='%s')"
    echo "- Previous: \`${PREV_COMMIT}\`"
    echo "- Files changed: $(git diff --name-only "$PREV_COMMIT" "$COMMIT" | wc -l)"
    echo "- Health: $HEALTH_URL OK"
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
