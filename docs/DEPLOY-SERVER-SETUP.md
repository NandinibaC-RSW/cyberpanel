# Staging Deploy — Server Setup Runbook

One-time setup that prepares the server for the `Deploy Staging` GitHub Actions
pipeline (`.github/workflows/deploy.yml`). After this runbook is done, every
push to `main` on GitHub auto-deploys to the staging tree on this server.

| Item | Value |
|---|---|
| Repo (git URL) | `https://github.com/NandinibaC-RSW/cyberpanel.git` |
| Staging tree | `/var/www/html/cyberpanel` (owned by `rainstream`) |
| Staging venv | `/var/www/html/cyberpanel/venv` (untracked, gitignored) |
| Staging config | `/var/www/html/cyberpanel/.env` (untracked, gitignored, `chmod 600`) |
| Staging DB | MariaDB database `cyberpanel_staging` (isolated copy, never production) |
| Staging service | `cyberpanel-staging.service` (gunicorn, port 8091, user `rainstream`) |
| Runner | ALREADY REGISTERED — `cyberpanel-server`, runs as `rainstream` via `actions.runner.NandinibaC-RSW-cyberpanel.cyberpanel-server.service`; manage with `sudo ./svc.sh status\|stop\|start` in `/home/rainstream/actions-runner` |
| Production panel | `/usr/local/CyberCP` — **NEVER touched by this pipeline** |

> **User note:** the pipeline runs as the existing `rainstream` user (which has
> `NOPASSWD: ALL` sudo). No dedicated `deploy` user is needed.

**Status:** the GitHub runner is registered and running. Sections 1–7 below
(staging runtime) are still outstanding — until they are done, pipeline runs
fail at the deploy script's preflight checks with a message naming the missing
piece (by design; nothing on the server is touched).

All commands run on the server as `rainstream`.

---

## 1. Packages

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
# git, curl, rsync are already present per audit
```

## 2. Repoint the staging clone to the fork

> The existing clone at `/var/www/html/cyberpanel` tracks upstream
> `usmannasir/cyberpanel` (v3.0.5-dev + 4 local template tweaks). Those tweaks
> are already part of the fork's `main`, so repointing loses nothing.

```bash
cd /var/www/html/cyberpanel
sudo git remote set-url origin https://github.com/NandinibaC-RSW/cyberpanel.git
sudo git fetch origin
sudo git checkout -B main origin/main
sudo git reset --hard origin/main
sudo chown -R rainstream:rainstream /var/www/html/cyberpanel
```

## 3. Runtime webroot `public/`

`public/` (phpMyAdmin, SnappyMail, served assets) is untracked runtime data.
Ensure it exists in the staging tree:

```bash
cd /var/www/html/cyberpanel
if [ ! -d public ]; then
  sudo cp -a /usr/local/CyberCP/public ./public
  sudo chown -R rainstream:rainstream ./public
fi
```

## 4. Staging venv (python3.12) seeded from the production env

```bash
cd /var/www/html/cyberpanel
python3.12 -m venv venv
sudo /usr/local/CyberCP/bin/pip freeze --local > /tmp/prod-freeze.txt
venv/bin/pip install -r /tmp/prod-freeze.txt -r requirements-staging.txt
rm /tmp/prod-freeze.txt
```

## 5. Staging `.env` (fresh secrets — NEVER reuse production's)

```bash
cd /var/www/html/cyberpanel
DBPASS=$(openssl rand -hex 24)
SECRET=$(openssl rand -base64 48 | tr -d '\n' | tr '/+_' 'AaBb')
cat > .env <<EOF
DEBUG=true
ALLOWED_HOSTS=*
SECRET_KEY=$SECRET
DB_NAME=cyberpanel_staging
DB_USER=cyberpanel_staging
DB_PASSWORD=$DBPASS
DB_HOST=localhost
ROOT_DB_NAME=mysql
ROOT_DB_USER=cyberpanel_staging
ROOT_DB_PASSWORD=$DBPASS
SERVE_STATIC=true
EOF
chmod 600 .env
```

`SERVE_STATIC=true` turns on the env-gated WhiteNoise middleware in
`CyberCP/settings.py` (staging only; production behavior is unchanged).
`ROOT_DB_*` deliberately points at the restricted staging user — privileged
site-management operations in staging fail with DB access errors **by design**
(see §8).

## 6. Staging database (isolated copy of production data)

```bash
sudo mysql <<SQL
CREATE DATABASE IF NOT EXISTS cyberpanel_staging;
CREATE USER IF NOT EXISTS 'cyberpanel_staging'@'localhost' IDENTIFIED BY '$DBPASS';
GRANT ALL PRIVILEGES ON cyberpanel_staging.* TO 'cyberpanel_staging'@'localhost';
FLUSH PRIVILEGES;
SQL

# One-time copy of production data (viewing purposes only):
sudo mysqldump --single-transaction --no-tablespaces cyberpanel | sudo mysql cyberpanel_staging
```

(`$DBPASS` must still be set in the shell from §5; otherwise re-read it from
`.env` first.)

To refresh staging data from production later (manual, on the server):

```bash
sudo mysql -e 'DROP DATABASE cyberpanel_staging; CREATE DATABASE cyberpanel_staging;'
sudo mysqldump --single-transaction --no-tablespaces cyberpanel | sudo mysql cyberpanel_staging
```

## 7. systemd service

```bash
sudo tee /etc/systemd/system/cyberpanel-staging.service >/dev/null <<'EOF'
[Unit]
Description=CyberPanel staging panel (gunicorn, port 8091)
After=network.target mariadb.service

[Service]
User=rainstream
Group=rainstream
WorkingDirectory=/var/www/html/cyberpanel
EnvironmentFile=/var/www/html/cyberpanel/.env
ExecStart=/var/www/html/cyberpanel/venv/bin/gunicorn CyberCP.wsgi:application --bind 0.0.0.0:8091 --workers 3 --timeout 120 --access-logfile - --error-logfile -
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
Staging runs as `rainstream`, whose only superpower beyond the tree is
restarting this service via the deploy script.

## 8. What staging can and cannot do (isolation model)

- Staging uses its own DB user restricted to `cyberpanel_staging.*`.
- Consequence: browsing/validating the v3 UI works against real-looking data;
  site-management actions (create/delete website, etc.) fail safely instead of
  touching production. This is intentional.

## 9. First deploy + how to verify

Once §1–§7 are done, trigger a deploy: GitHub → **Actions → Deploy Staging →
Run workflow** (or push any commit to `main`).

Expected: `sync → deps → manage.py check → migrate → restart → health check`.
On health failure the script auto-rolls back to the previous commit and marks
the run failed.

Quick verification on the server:

```bash
curl -sI http://127.0.0.1:8091/
git -C /var/www/html/cyberpanel log -1 --oneline   # matches the deployed SHA
```

## 10. Security follow-ups (important, separate from this setup)

- **Make the repo private** (Settings → Danger Zone) while it still contains
  the old secrets in history.
- **Rotate the exposed production secrets**: `/usr/local/CyberCP/secret_key`
  and `/usr/local/CyberCP/terminal_jwt_secret` were committed to the public
  repo in its first commit. Generate new ones on the server, `chown`/`chmod`
  them as the originals were, and `sudo systemctl restart lscpd` (admin
  sessions log out — expected). Then purge the old commit from repo history
  (force-push a clean history) as a follow-up.
- Repo Settings → Actions → General: require approval for **all** outside
  contributors (defense in depth on top of the workflow's PR guard — the
  workflow itself never triggers on `pull_request`).
- Keep `secret_key`, `terminal_jwt_secret`, `.env`, `venv/`, `public/` out of
  git forever (now enforced by `.gitignore`).
