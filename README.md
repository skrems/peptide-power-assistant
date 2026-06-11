# Peptide Power Assistant

Self-hosted protocol and dose tracker for a Zimaboard or similar local server. This is for entertaiment purposes only and is not intended as any type of medical application. This a proof of concept build as education and entertainment. 

## MVP Features

- Local username/password login.
- Admin and member roles.
- Admin protocol create, edit, publish, retire, and delete.
- Admin peptide catalog for dropdowns in protocols and manual logging.
- Editable protocol day ranges and dose steps.
- Published protocol activation for users.
- Today view showing protocol day, dose due, rest days, and completion state.
- Dose log with protocol and manual entries, peptide filtering, edit, and delete.
- Calendar view with peptide colors and day-level add, edit, and delete.
- Daily check-in notes.
- Admin-only SQLite backup export.
- PWA manifest so the app can be added to an iPhone home screen.
- SQLite database stored at `/data/app.db`.

## Peptides

Admins add and remove peptide names from the **Admin** tab. The catalog feeds the peptide dropdowns used by:

- Manual dose logging.
- Protocol creation and editing.

Starter peptides are seeded automatically:

- GHK-Cu
- MOTS-c
- Retatrutide
- SS-31
- BPC-157
- TP-500

## Run Locally

```bash
python -m app.server
```

Open:

```text
http://127.0.0.1:8080
```

Default local admin:

```text
admin@example.local
change-me-now
```

Override with environment variables:

```bash
PEPTIDE_ADMIN_EMAIL=you@example.com PEPTIDE_ADMIN_PASSWORD='strong password' python -m app.server
```

## Test On This MacBook

Run the smoke test before deploying anywhere:

```bash
make smoke
```

The smoke test starts the app with a throwaway SQLite database, logs in, checks the seeded GHK-Cu protocol, activates it, logs a dose, and verifies the admin page.

For container testing on macOS, install one container runtime first:

```bash
brew install --cask orbstack
```

or:

```bash
brew install --cask docker
```

Then build and run the same container you will move to the Zimaboard:

```bash
make docker-build
make docker-up
```

Open:

```text
http://127.0.0.1:8080
```

Stop it with:

```bash
make docker-down
```

## Zimaboard Deployment

Current deployment model: GitHub Actions publishes a Docker image to GHCR, Zimaboard pulls that image, and SQLite data stays outside the container.

Zimaboard address:

```text
<zimaboard-ip>
```

The SSH config on the MacBook has an alias, so SSH/SCP/rsync can use:

```text
<zimaboard-user>@<zimaboard-host>
```

Browsers do not read SSH aliases. Use the IP in Safari/Chrome:

```text
http://<zimaboard-ip>:8080
```

### Zimaboard Paths

```text
/DATA/AppData/peptide-power-assistant/source
/DATA/AppData/peptide-power-assistant/data/app.db
/DATA/AppData/peptide-power-assistant/docker-config
```

The app database is stored at:

```text
/DATA/AppData/peptide-power-assistant/data/app.db
```

### Image

The current Zimaboard deployment is pinned to:

```text
ghcr.io/skrems/peptide-power-assistant:v1.3
```

GitHub Actions also publishes `latest` and `sha-...` tags for traceability, but ZimaOS should use the explicit `vX.Y` tag so GUI updates can detect a real version change.

The GHCR package has been made public so Zimaboard can pull it without `docker login`.

### Database Copy

The database was copied separately to the Zimaboard data path. If it ever needs to be copied again from the MacBook:

```bash
scp <local-repo-path>/data/app.db <zimaboard-user>@<zimaboard-host>:/tmp/app.db
ssh <zimaboard-user>@<zimaboard-host> 'sudo mkdir -p /DATA/AppData/peptide-power-assistant/data && sudo mv /tmp/app.db /DATA/AppData/peptide-power-assistant/data/app.db && sudo chown -R <zimaboard-user>:<zimaboard-group> /DATA/AppData/peptide-power-assistant'
```

### Compose File On Zimaboard

The repo includes this Zimaboard-specific compose file:

```text
/DATA/AppData/peptide-power-assistant/source/docker-compose.zima.yml
```

Copy or update it from the MacBook with:

```bash
rsync -av \
  <local-repo-path>/docker-compose.zima.yml \
  <zimaboard-user>@<zimaboard-host>:/DATA/AppData/peptide-power-assistant/source/docker-compose.zima.yml
```

It pins the app to Pacific time so calendar days match local use even if the Zimaboard host timezone differs:

```yaml
name: peptide-power-assistant

services:
  peptide-power-assistant:
    image: ghcr.io/skrems/peptide-power-assistant:v1.3
    container_name: peptide-power-assistant
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      PEPTIDE_HOST: "0.0.0.0"
      PEPTIDE_PORT: "8080"
      PEPTIDE_DB: "/data/app.db"
      PEPTIDE_TIMEZONE: "America/Los_Angeles"
      TZ: "America/Los_Angeles"
      PEPTIDE_SECRET: "replace-this-with-a-long-random-secret"
    volumes:
      - /DATA/AppData/peptide-power-assistant/data:/data

x-casaos:
  architectures:
    - amd64
  main: peptide-power-assistant
  title:
    en_us: Peptide Power Assistant
  icon: https://raw.githubusercontent.com/skrems/peptide-power-assistant/main/static/icon.svg
  port_map: "8080"
  scheme: http
  index: /
```

ZimaOS has a read-only `/root` for this Docker CLI path, so CLI commands use a writable Docker config directory:

```bash
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml up -d
```

Check container status:

```bash
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker ps
```

Check logs:

```bash
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker logs peptide-power-assistant --tail 50
```

Then open:

```text
http://<zimaboard-ip>:8080
```

### ZimaOS Dashboard Tile

If the ZimaOS dashboard shows the app as `source` with a generic/incomplete icon, it is stale dashboard metadata from the original CLI deploy folder name.

The compose file now includes:

- `name: peptide-power-assistant`
- `x-casaos` metadata for title, icon, Web UI port, and main service

To recreate the container with the corrected compose project name:

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

This keeps the SQLite database because it lives in the persistent host path:

```text
/DATA/AppData/peptide-power-assistant/data/app.db
```

If the dashboard still keeps the stale `source` tile after a refresh, remove the old tile from the ZimaOS UI and import `docker-compose.zima.yml` through **App Store > Custom Install > Import > Docker Compose**. The compose metadata will populate the tile as **Peptide Power Assistant**.

### Update Flow

After making app changes on the MacBook:

1. Test locally:

```bash
cd <local-repo-path>
python3 -m py_compile app/server.py scripts/smoke_test.py
python3 scripts/smoke_test.py
git diff --check
```

2. Commit and push as usual.

3. For a ZimaOS GUI-friendly release, bump the image tag in `docker-compose.zima.yml`, for example from `v1.3` to `v1.4`, then commit and push.

4. Create and push a matching git tag:

```bash
git tag v1.4
git push origin v1.4
```

GitHub Actions publishes the matching image tag:

```text
ghcr.io/skrems/peptide-power-assistant:v1.4
```

5. Copy the latest compose file to Zimaboard only if it changed:

```bash
rsync -av \
  <local-repo-path>/docker-compose.zima.yml \
  <zimaboard-user>@<zimaboard-host>:/DATA/AppData/peptide-power-assistant/source/docker-compose.zima.yml
```

6. Pull and restart on the Zimaboard:

```bash
ssh <zimaboard-user>@<zimaboard-host>
cd /DATA/AppData/peptide-power-assistant/source
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml pull
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml up -d
```

6. Verify:

```bash
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker logs peptide-power-assistant --tail 50
```

The container should report:

```text
Peptide Power Assistant running at http://0.0.0.0:8080
```

### Backups

Admins can download a consistent SQLite snapshot from:

```text
Admin > Export backup file
```

For automated Zimaboard backups, use SQLite's online backup command instead of copying the live database file:

```bash
ssh <zimaboard-user>@<zimaboard-host> 'mkdir -p /DATA/AppData/peptide-power-assistant/backups && sqlite3 /DATA/AppData/peptide-power-assistant/data/app.db ".backup /DATA/AppData/peptide-power-assistant/backups/app-$(date +%Y%m%d-%H%M%S).db"'
```

Container rebuilds should not erase app data because `/data` is mounted from:

```text
/DATA/AppData/peptide-power-assistant/data
```

## Data And Backups

Local MacBook SQLite database:

```text
<local-repo-path>/data/app.db
```

Zimaboard SQLite database:

```text
/DATA/AppData/peptide-power-assistant/data/app.db
```

The Admin export button uses SQLite's backup API and downloads a timestamped `.db` file.

## Medical Safety

This app is an arithmetic and logging helper only. Confirm peptide identity, dose, route, reconstitution, and schedule with a clinician or pharmacist.
