# Project State

Last updated: 2026-06-20

## Summary

Peptide Power Assistant is a self-hosted Python/SQLite web app for dose logging, protocol management, daily check-ins, and calendar review. It replaced the earlier AWS/Amplify and calculator/inventory direction. The app is now deployed on Zimaboard 2 and is updated through a GHCR-published Docker image.

## Repository

```text
<local-repo-path>
git@github.com:skrems/peptide-power-assistant.git
```

The current Zimaboard deployment is pinned to:

```text
ghcr.io/skrems/peptide-power-assistant:v1.10
```

GitHub Actions also publishes `latest` and `sha-...` tags. ZimaOS custom apps should use explicit `vX.Y` tags. Testing showed the dashboard does not reliably detect a changed digest under the same custom tag.

The GHCR package is public so Zimaboard can pull without `docker login`.

## Related Apps

Peptide Protocol Library is a separate public/read-only reference app:

```text
<local-projects-path>/peptide-protocol-library
ghcr.io/skrems/peptide-protocol-library:v1.0
```

It runs on port `8090` and intentionally does not share Peptide Power's login, dose logs, users, inventory, or private SQLite database.

Peptide Power exposes `/library` as a public redirect. By default it keeps the same hostname and changes the port to `8090`, so both LAN and remote-hostname access work:

```text
http://<host>:8080/library -> http://<host>:8090/
```

Override with:

```text
PEPTIDE_PROTOCOL_LIBRARY_URL
PEPTIDE_PROTOCOL_LIBRARY_PORT
```

## Local Development

Run locally:

```bash
cd <local-repo-path>
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
<zimaboard-user>@<zimaboard-host>
```

Zimaboard IP/browser URL:

```text
http://<zimaboard-ip>:8080
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
name: peptide-power-assistant
image: ghcr.io/skrems/peptide-power-assistant:v1.10
volumes:
  - /DATA/AppData/peptide-power-assistant/data:/data
environment:
  PEPTIDE_DB: /data/app.db
  PEPTIDE_TIMEZONE: America/Los_Angeles
  PEPTIDE_PROTOCOL_LIBRARY_PORT: "8090"
  TZ: America/Los_Angeles
x-casaos:
  title:
    en_us: Peptide Power Assistant
  icon: https://raw.githubusercontent.com/skrems/peptide-power-assistant/main/static/icon.svg
  port_map: "8080"
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

## ZimaOS Dashboard Tile

The dashboard previously showed an incomplete app tile named `source` because the app was first deployed by running Docker Compose from a directory named `source`. Docker created a `source` compose project and ZimaOS recorded that app id.

The compose file now sets the project name and ZimaOS metadata explicitly:

```yaml
name: peptide-power-assistant
x-casaos:
  main: peptide-power-assistant
  title:
    en_us: Peptide Power Assistant
  icon: https://raw.githubusercontent.com/skrems/peptide-power-assistant/main/static/icon.svg
```

One-time cleanup/recreate command:

```bash
ssh <zimaboard-user>@<zimaboard-host>
cd /DATA/AppData/peptide-power-assistant/source
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -p source -f docker-compose.zima.yml down
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml pull
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml up -d
```

If ZimaOS still keeps a stale `source` launcher after refresh, remove that tile in the UI and re-import `docker-compose.zima.yml` via **App Store > Custom Install > Import > Docker Compose** so the `x-casaos` metadata is used.

## Update Flow

1. Make and test changes on the MacBook.
2. Bump the image tag in `docker-compose.zima.yml`.
3. Commit and push to `main`.
4. Create and push the matching git tag:

```bash
git tag <version>
git push origin <version>
```

GitHub Actions publishes the matching image tag:

```text
ghcr.io/skrems/peptide-power-assistant:<version>
```

5. If `docker-compose.zima.yml` changed, copy it:

```bash
rsync -av \
  <local-repo-path>/docker-compose.zima.yml \
  <zimaboard-user>@<zimaboard-host>:/DATA/AppData/peptide-power-assistant/source/docker-compose.zima.yml
```

6. Pull and restart on Zimaboard:

```bash
ssh <zimaboard-user>@<zimaboard-host>
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

Admins can download a consistent SQLite snapshot from:

```text
Admin > Export backup file
```

For automated Zimaboard backups, use SQLite's online backup command instead of copying the live database file:

```bash
ssh <zimaboard-user>@<zimaboard-host> 'mkdir -p /DATA/AppData/peptide-power-assistant/backups && sqlite3 /DATA/AppData/peptide-power-assistant/data/app.db ".backup /DATA/AppData/peptide-power-assistant/backups/app-$(date +%Y%m%d-%H%M%S).db"'
```

## Current Features

- Login with local users and admin/member roles.
- Admin peptide catalog with colors.
- Admin protocol CRUD and publishing.
- Protocol steps support daily, every N days, selected weekdays, and rest.
- Protocol and dose input accepts values in mg or mcg; mcg is stored as mg internally.
- Seeded published protocols include GHK-Cu, Selank SK10, and Tesamorelin TSM10 user-provided cycles.
- Today view for due protocol tasks.
- Manual and protocol dose logging.
- Dose log filtering by peptide.
- Dose log edit/delete.
- Calendar month view with peptide colors and individual dose amounts.
- Calendar day add/edit/delete.
- Daily check-ins.
- Admin-only SQLite backup export.
- Public `/library` redirect to the separate read-only Peptide Protocol Library.
- PWA manifest for iPhone home screen install.

## Known Notes

- The app timezone is explicit: `America/Los_Angeles`.
- Zimaboard host time was previously off from Pacific; app-level timezone now protects calendar/today calculations.
- Browsers do not use SSH aliases; use `<zimaboard-ip>` unless local DNS is configured.
