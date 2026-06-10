# Project State

Last updated: 2026-06-09

## Summary

Peptide Power Assistant is a self-hosted Python/SQLite web app for dose logging, protocol management, daily check-ins, and calendar review. It replaced the earlier AWS/Amplify and calculator/inventory direction. The app is now deployed on Zimaboard 2 and is updated through a GHCR-published Docker image.

## Repository

```text
/Users/skrems/Projects/peptide-power-assistant
git@github.com:skrems/peptide-power-assistant.git
```

GitHub Actions publishes:

```text
ghcr.io/skrems/peptide-power-assistant:latest
```

The GHCR package is public so Zimaboard can pull without `docker login`.

## Local Development

Run locally:

```bash
cd /Users/skrems/Projects/peptide-power-assistant
python3 -m app.server
```

Local URL:

```text
http://127.0.0.1:8080
```

Run checks:

```bash
python3 -m py_compile app/server.py scripts/smoke_test.py
python3 scripts/smoke_test.py
git diff --check
```

## Zimaboard Deployment

Zimaboard SSH alias:

```text
admin@zimaboard
```

Zimaboard IP/browser URL:

```text
http://192.168.68.199:8080
```

Important paths:

```text
/DATA/AppData/peptide-power-assistant/source
/DATA/AppData/peptide-power-assistant/data/app.db
/DATA/AppData/peptide-power-assistant/docker-config
```

Compose file on Zimaboard:

```text
/DATA/AppData/peptide-power-assistant/source/docker-compose.zima.yml
```

The compose file uses:

```yaml
image: ghcr.io/skrems/peptide-power-assistant:latest
volumes:
  - /DATA/AppData/peptide-power-assistant/data:/data
environment:
  PEPTIDE_DB: /data/app.db
  PEPTIDE_TIMEZONE: America/Los_Angeles
  TZ: America/Los_Angeles
```

ZimaOS needs a writable Docker config path because `/root/.docker` is read-only in this context:

```bash
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml pull

sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml up -d
```

Verify:

```bash
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker ps | grep peptide

sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker logs peptide-power-assistant --tail 50
```

## Update Flow

1. Make and test changes on the MacBook.
2. Commit and push to `main`.
3. GitHub Actions publishes `ghcr.io/skrems/peptide-power-assistant:latest`.
4. If `docker-compose.zima.yml` changed, copy it:

```bash
rsync -av \
  /Users/skrems/Projects/peptide-power-assistant/docker-compose.zima.yml \
  admin@zimaboard:/DATA/AppData/peptide-power-assistant/source/docker-compose.zima.yml
```

5. Pull and restart on Zimaboard:

```bash
ssh admin@zimaboard
cd /DATA/AppData/peptide-power-assistant/source
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml pull
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml up -d
```

## Data And Safety

Persistent data is SQLite at:

```text
/DATA/AppData/peptide-power-assistant/data/app.db
```

The container image does not include local DB files, user records, logs, or passwords. `.dockerignore` excludes local data and database files from Docker builds.

Backup before larger updates:

```bash
ssh admin@zimaboard 'cp /DATA/AppData/peptide-power-assistant/data/app.db /DATA/AppData/peptide-power-assistant/data/app-backup-$(date +%Y%m%d-%H%M%S).db'
```

## Current Features

- Login with local users and admin/member roles.
- Admin peptide catalog with colors.
- Admin protocol CRUD and publishing.
- Protocol steps support daily, every N days, selected weekdays, and rest.
- Today view for due protocol tasks.
- Manual and protocol dose logging.
- Dose log filtering by peptide.
- Dose log edit/delete.
- Calendar month view with peptide colors.
- Calendar day add/edit/delete.
- Daily check-ins.
- PWA manifest for iPhone home screen install.

## Known Notes

- The app timezone is explicit: `America/Los_Angeles`.
- Zimaboard host time was previously off from Pacific; app-level timezone now protects calendar/today calculations.
- Browsers do not use the SSH alias `zimaboard`; use `192.168.68.199` unless local DNS is configured.
