# Staging Deploy — Server Setup Runbook

One-time setup that prepares the server for the `Deploy Staging` GitHub Actions
pipeline (`.github/workflows/deploy.yml`). After this runbook is done, every
push to `main` on GitHub auto-deploys to the staging tree on this server.

| Item | Value |
|---|---|
| Repo (git URL) | `https://github.com/NandinibaC-RSW/cyberpanel.git` (public, read-only fetch — no deploy key needed) |
| Staging tree | `/var/www/html/cyberpanel` |
| Staging venv | `/var/www/html/cyberpanel/venv` (untracked, gitignored) |
| Staging config | `/var/www/html/cyberpanel/.env` (untracked, gitignored, `chmod 600`) |
| Staging DB | MariaDB database `cyberpanel_staging` (isolated copy, never production) |
| Staging service | `cyberpanel-staging.service` (gunicorn, port 8091) |
| Pipeline user | `deploy` |
| Production panel | `/usr/local/CyberCP` — **NEVER touched by this pipeline** |

All commands run on the server as a sudo-capable user unless stated otherwise.

---

## 1. Packages

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
# git, curl, rsync, docker are already present per audit
```

## 2. Pipeline user + restricted sudo

```bash
sudo adduser --disabled-password --gecos "CyberPanel deploy" deploy
# The ONLY privilege this user gets: restarting the staging service.
echo 'deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart cyberpanel-staging.service' \
  | sudo tee /etc/sudoers.d/deploy-staging
sudo chmod 440 /etc/sudoers.d/deploy-staging
sudo visudo -c   # must report "parsed OK"
```

## 3. Repurpose the staging clone to track the fork

> The existing clone at `/var/www/html/cyberpanel` tracks upstream
> `usmannasir/cyberpanel` (v3.0.5-dev + 4 local template tweaks). Those tweaks
> are already part of the fork's `main`, so repointing loses nothing.
> If anything else on the server references this directory, check first.

```bash
cd /var/www/html/cyberpanel
sudo git remote set-url origin https://github.com/NandinibaC-RSW/cyberpanel.git
sudo git remote add upstream https://github.com/usmannasir/cyberpanel.git 2>/dev/null || true
sudo git fetch origin
sudo git checkout -B main origin/main
sudo git reset --hard origin/main
sudo git branch -D v3.0.5-dev 2>/dev/null || true
sudo chown -R deploy:deploy /var/www/html/cyberpanel
```

## 4. Runtime webroot `public/`

`public/` (phpMyAdmin, SnappyMail, served assets) is untracked runtime data.
Ensure it exists in the staging tree:

```bash
cd /var/www/html/cyberpanel
if [ ! -d public ]; then
  sudo cp -a /usr/local/CyberCP/public ./public   # copy from the live install
  sudo chown -R deploy:deploy ./public
fi
```

## 5. Staging venv (python3.12) seeded from the production env

```bash
sudo -u deploy python3.12 -m venv /var/www/html/cyberpanel/venv
sudo /usr/local/CyberCP/bin/pip freeze --local > /tmp/prod-freeze.txt
sudo chown deploy:deploy /tmp/prod-freeze.txt
sudo -u deploy /var/www/html/cyberpanel/venv/bin/pip install \
  -r /tmp/prod-freeze.txt \
  -r /var/www/html/cyberpanel/requirements-staging.txt
rm /tmp/prod-freeze.txt
```

## 6. Staging `.env` (fresh secrets — NEVER reuse production's)

```bash
cd /var/www/html/cyberpanel
sudo -u deploy bash -c 'cat > .env <<EOF
DEBUG=true
ALLOWED_HOSTS=*
SECRET_KEY=$(openssl rand -base64 48 | tr -d "\n" | tr "/+_" "AaBb")
DB_NAME=cyberpanel_staging
DB_USER=cyberpanel_staging
DB_PASSWORD=$(openssl rand -hex 24)
DB_HOST=localhost
ROOT_DB_NAME=mysql
ROOT_DB_USER=cyberpanel_staging
ROOT_DB_PASSWORD=\$(grep DB_PASSWORD .env | cut -d= -f2)
SERVE_STATIC=true
EOF'
sudo -u deploy sed -i '/^ROOT_DB_PASSWORD/d' .env
# fix ROOT_DB_PASSWORD to match DB_PASSWORD:
sudo -u deploy bash -c 'echo "ROOT_DB_PASSWORD=$(grep ^DB_PASSWORD= .env | cut -d= -f2)" >> .env'
sudo -u deploy chmod 600 .env
```

`SERVE_STATIC=true` turns on the env-gated WhiteNoise middleware in
`CyberCP/settings.py` (staging only; production behavior is unchanged).
`ROOT_DB_*` deliberately points at the restricted staging user — privileged
site-management operations in staging fail with DB access errors **by design**
(see §9).

## 7. Staging database (isolated copy of production data)

```bash
# Read the staging DB password chosen above:
DBPASS=$(sudo -u deploy grep ^DB_PASSWORD= /var/www/html/cyberpanel/.env | cut -d= -f2)

sudo mysql <<SQL
CREATE DATABASE IF NOT EXISTS cyberpanel_staging;
CREATE USER IF NOT EXISTS 'cyberpanel_staging'@'localhost' IDENTIFIED BY '$DBPASS';
GRANT ALL PRIVILEGES ON cyberpanel_staging.* TO 'cyberpanel_staging'@'localhost';
FLUSH PRIVILEGES;
SQL

# One-time copy of production data (viewing purposes only):
sudo mysqldump --single-transaction --no-tablespaces cyberpanel | sudo mysql cyberpanel_staging
```

To refresh staging data from production later (manual, on the server):

```bash
sudo mysql -e 'DROP DATABASE cyberpanel_staging; CREATE DATABASE cyberpanel_staging;'
sudo mysqldump --single-transaction --no-tablespaces cyberpanel | sudo mysql cyberpanel_staging
```

## 8. systemd service

```bash
sudo tee /etc/systemd/system/cyberpanel-staging.service >/dev/null <<'EOF'
[Unit]
Description=CyberPanel staging panel (gunicorn, port 8091)
After=network.target mariadb.service

[Service]
User=deploy
Group=deploy
WorkingDirectory=/var/www/html/cyberpanel
EnvironmentFile=/var/www/html/cyberpanel/.env
ExecStart=/var/www/html/cyberpanel/venv/bin/gunicorn CyberCP.wsgi:application \
    --bind 0.0.0.0:8091 --workers 3 --timeout 120 \
    --access-logfile - --error-logfile -
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now cyberpanel-staging.service
sleep 3
curl -sI http://127.0.0.1:8091/ | head -3   # expect an HTTP response (200/302)
journalctl -u cyberpanel-staging.service -n 30 --no-pager   # check for errors
```

The panel is reachable at `http://192.168.1.138:8091` from the LAN
(NAT keeps it off the internet). CyberPanel admin login still applies.

## 9. What staging can and cannot do (isolation model)

- Staging runs as user `deploy`, which has **no** write access to
  `/usr/local/CyberCP`, `/home/*`, LiteSpeed config, or Docker — and one
  single sudo grant (restart the staging service).
- Staging uses its own DB user restricted to `cyberpanel_staging.*`.
- Consequence: browsing/validating the v3 UI works against real-looking data;
  site-management actions (create/delete website, etc.) fail safely instead of
  touching production. This is intentional.

## 10. Register the self-hosted GitHub Actions runner

This is the only step that needs the GitHub UI (a human):

1. GitHub → `NandinibaC-RSW/cyberpanel` → **Settings → Actions → Runners →
   New self-hosted runner → Linux / x64**. Copy the registration token shown.
2. On the server, as the `deploy` user:

```bash
sudo -u deploy -H bash -c '
  mkdir -p ~/actions-runner && cd ~/actions-runner
  curl -o actions-runner-linux-x64-2.327.1.tar.gz -L \
    https://github.com/actions/runner/releases/download/v2.327.1/actions-runner-linux-x64-2.327.1.tar.gz
  echo "08e56be4a23c1e7bb98f4b155d7c5eeb6b8c3f3f6f8b5e5e0e6f9c8d7a6b5c4e  actions-runner-linux-x64-2.327.1.tar.gz" >/dev/null  # replace with the hash shown on the download page
  tar xzf actions-runner-linux-x64-*.tar.gz
  ./config.sh --url https://github.com/NandinibaC-RSW/cyberpanel \
              --token <TOKEN-FROM-GITHUB-UI> \
              --labels staging \
              --unattended
'
sudo ./svc.sh install deploy   # run from ~deploy/actions-runner as sudo-capable user:
sudo ./svc.sh start
```

> Use the exact download URL, SHA checksum, and token shown on the GitHub
> runner-registration page — the version above may be outdated by the time you
> run this. The `staging` label is required: the workflow targets
> `runs-on: [self-hosted, staging]`.

Verify: GitHub → Settings → Actions → Runners shows the runner **Idle**.

## 11. First deploy + how to verify

The push of this pipeline to `main` already created a queued run (no runner
existed yet). Once the runner starts it will pick it up — or trigger manually:

- GitHub → **Actions → Deploy Staging → Run workflow** (or push any commit to `main`).

Expected: `sync → deps → manage.py check → migrate → restart → health check`.
On health failure the script auto-rolls back to the previous commit and marks
the run failed.

Quick verification on the server:

```bash
curl -sI http://127.0.0.1:8091/
cd /var/www/html/cyberpanel && sudo -u deploy git log -1 --oneline   # matches the deployed SHA
```

## 12. Security follow-ups (important, separate from this setup)

- **Rotate the exposed production secrets**: `/usr/local/CyberCP/secret_key`
  and `/usr/local/CyberCP/terminal_jwt_secret` were committed to the public
  repo in its first commit. Generate new ones on the server, `chown`/`chmod`
  them as the originals were, and `sudo systemctl restart lscpd` (admin
  sessions log out — expected). Then purge the old commit from repo history
  (force-push a clean history) as a follow-up.
- Keep `secret_key`, `terminal_jwt_secret`, `.env`, `venv/`, `public/` out of
  git forever (now enforced by `.gitignore`).
