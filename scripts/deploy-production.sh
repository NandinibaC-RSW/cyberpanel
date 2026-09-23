#!/usr/bin/env bash
# Deploys a commit of this repo to the LIVE production panel (/usr/local/CyberCP).
# Runs ON the server (self-hosted GitHub Actions runner, as user "rainstream";
# all filesystem/DB operations go through sudo because the production tree is
# root-owned).
# Usage: deploy-production.sh <commit-sha>
#
# Safety model:
# 1. Before touching anything, the current dirty state of the production tree
#    (1,482+ files of live work) is committed to a local backup branch
#    backup/pre-deploy-<timestamp> — full recovery stays possible.
# 2. A gzip'd dump of the cyberpanel DB is written to /root first.
# 3. If the health check fails after deploy, the tree is reset back to the
#    backup branch and the service restarted before the run fails.
# The last 3 backup branches are kept; older ones are pruned.

set -euo pipefail

COMMIT="${1:?usage: deploy-production.sh <commit-sha>}"
PROD_DIR="${PROD_DIR:-/usr/local/CyberCP}"
SERVICE="lscpd"
HEALTH_URL="${HEALTH_URL:-https://127.0.0.1:8090/}"
HEALTH_RETRIES=30
HEALTH_INTERVAL=2
KEEP_BACKUPS=3
REPO_URL="https://github.com/NandinibaC-RSW/cyberpanel.git"   # public: fetch needs no credentials

say() { echo "[deploy-production] $*"; }
fail() {
    echo "[deploy-production] ERROR: $*" >&2
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
        echo "❌ $*" >> "$GITHUB_STEP_SUMMARY"
    fi
    exit 1
}

# --- Preflight ---
[ -d "$PROD_DIR/.git" ] || fail "$PROD_DIR is not a git checkout"
systemctl cat "$SERVICE" >/dev/null 2>&1 || fail "systemd unit $SERVICE not found"
command -v mysqldump >/dev/null 2>&1 || fail "mysqldump not found"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_BRANCH="backup/pre-deploy-$STAMP"

# --- 1. Make sure runtime dirs are never committed to the snapshot ---
sudo curl -sf https://raw.githubusercontent.com/NandinibaC-RSW/cyberpanel/main/.gitignore \
     -o "$PROD_DIR/.gitignore" || say "WARNING: could not refresh .gitignore; continuing with existing one"

# --- 2. Snapshot the live tree state (dirty work + untracked source) ---
if [ -n "$(sudo git -C "$PROD_DIR" status --porcelain)" ]; then
    sudo git -C "$PROD_DIR" add -A
    sudo git -C "$PROD_DIR" -c user.name="deploy-bot" -c user.email="deploy-bot@rainflowweb.local" \
        commit -q -m "pre-deploy snapshot $STAMP (auto: before deploying $COMMIT)"
    say "dirty production state saved as commit $(sudo git -C "$PROD_DIR" rev-parse --short HEAD)"
else
    say "production tree clean; no snapshot commit needed"
fi
sudo git -C "$PROD_DIR" branch -f "$BACKUP_BRANCH" HEAD
PREV_COMMIT="$(sudo git -C "$PROD_DIR" rev-parse HEAD)"
say "backup branch: $BACKUP_BRANCH @ $PREV_COMMIT"

# --- 3. DB dump ---
DB_DUMP="/root/cyberpanel-db-pre-deploy-$STAMP.sql.gz"
sudo mysqldump --single-transaction --no-tablespaces cyberpanel | sudo gzip > "$DB_DUMP"
sudo chmod 600 "$DB_DUMP"
say "DB dump written: $DB_DUMP ($(sudo du -h "$DB_DUMP" | cut -f1))"
# keep the last 3 DB dumps (glob must run as root — rainstream can't list /root)
sudo bash -c 'ls -1t /root/cyberpanel-db-pre-deploy-*.sql.gz 2>/dev/null | tail -n +4 | xargs -r rm -f' || true

# --- 4. Fetch target commit from the fork and reset the tree ---
sudo git -C "$PROD_DIR" fetch "$REPO_URL" main
if ! sudo git -C "$PROD_DIR" cat-file -e "$COMMIT" 2>/dev/null; then
    sudo git -C "$PROD_DIR" branch -D "$BACKUP_BRANCH" 2>/dev/null || true
    fail "commit $COMMIT not found after fetch — aborting before any changes"
fi
sudo git -C "$PROD_DIR" reset --hard "$COMMIT"
say "production tree checked out $COMMIT ($(sudo git -C "$PROD_DIR" log -1 --format='%h %s'))"

# --- 5. DB migrations (run from the production tree, not the job workspace) ---
if ! sudo bash -c "cd '$PROD_DIR' && ./bin/python3 manage.py migrate --noinput"; then
    say "migrate FAILED — rolling back tree before failing"
    sudo git -C "$PROD_DIR" reset --hard "$PREV_COMMIT"
    sudo systemctl restart "$SERVICE"
    fail "manage.py migrate failed on production DB"
fi

# --- 6. Restart panel and health check ---
sudo systemctl restart "$SERVICE"

HEALTHY=0
for i in $(seq 1 "$HEALTH_RETRIES"); do
    if curl -skf -o /dev/null "$HEALTH_URL"; then
        HEALTHY=1
        break
    fi
    sleep "$HEALTH_INTERVAL"
done

if [ "$HEALTHY" != 1 ]; then
    say "health check FAILED — rolling back to $PREV_COMMIT ($BACKUP_BRANCH)"
    sudo git -C "$PROD_DIR" reset --hard "$PREV_COMMIT"
    sudo systemctl restart "$SERVICE"
    sleep 5
    curl -skf -o /dev/null "$HEALTH_URL" && say "rollback healthy" || say "WARNING: rollback also failed health check — investigate manually (tree restored to $BACKUP_BRANCH)"
    fail "deployed code failed production health check; rolled back to $BACKUP_BRANCH"
fi

# --- 7. Prune old backup branches (keep newest N) ---
sudo git -C "$PROD_DIR" for-each-ref --format='%(refname:short) %(committerdate:unix)' refs/heads/backup/ \
  | sort -k2 -rn | tail -n +$((KEEP_BACKUPS + 1)) | cut -d' ' -f1 \
  | while read -r old; do sudo git -C "$PROD_DIR" branch -D "$old" >/dev/null; done

say "SUCCESS: $COMMIT deployed to production and healthy (backup: $BACKUP_BRANCH)"
{
    echo "## Production deploy ✅"
    echo "- Commit: \`${COMMIT}\` — $(sudo git -C "$PROD_DIR" log -1 --format='%s')"
    echo "- Backup branch: \`$BACKUP_BRANCH\` (tree state before deploy)"
    echo "- DB dump: \`$DB_DUMP\`"
    echo "- Health: $HEALTH_URL OK"
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
