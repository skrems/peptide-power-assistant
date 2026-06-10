# Peptide Power Assistant

Self-hosted protocol and dose tracker for a Zimaboard or similar local server.

This is a clean MVP rewrite. It does not use AWS, Amplify, Cognito, AppSync, or the old calculator/inventory code.

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

Current deployment model: copy source from the MacBook to the Zimaboard, build the container directly on the Zimaboard, and keep SQLite data outside the container.

Confirmed Zimaboard address:

```text
192.168.68.199
```

The SSH config on the MacBook has an alias, so SSH/SCP/rsync can use:

```text
admin@zimaboard
```

Browsers do not read SSH aliases. Use the IP in Safari/Chrome:

```text
http://192.168.68.199:8080
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

### First-Time Source Copy

From the MacBook:

```bash
rsync -av --delete \
  --exclude data \
  --exclude .git \
  --exclude __pycache__ \
  --exclude '*.pyc' \
  /Users/skrems/Projects/peptide-power-assistant/ \
  admin@zimaboard:/DATA/AppData/peptide-power-assistant/source/
```

The database was copied separately to:

```text
/DATA/AppData/peptide-power-assistant/data/app.db
```

If it ever needs to be copied again from the MacBook:

```bash
scp /Users/skrems/Projects/peptide-power-assistant/data/app.db admin@zimaboard:/tmp/app.db
ssh admin@zimaboard 'sudo mkdir -p /DATA/AppData/peptide-power-assistant/data && sudo mv /tmp/app.db /DATA/AppData/peptide-power-assistant/data/app.db && sudo chown -R admin:samba /DATA/AppData/peptide-power-assistant'
```

### Compose File On Zimaboard

On the Zimaboard, the deployment uses this file:

```text
/DATA/AppData/peptide-power-assistant/source/docker-compose.zima.yml
```

Contents:

```yaml
services:
  peptide-power-assistant:
    build: .
    container_name: peptide-power-assistant
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      PEPTIDE_HOST: "0.0.0.0"
      PEPTIDE_PORT: "8080"
      PEPTIDE_DB: "/data/app.db"
      PEPTIDE_SECRET: "replace-this-with-a-long-random-secret"
    volumes:
      - /DATA/AppData/peptide-power-assistant/data:/data
```

ZimaOS has a read-only `/root` for this Docker CLI path, so commands use a writable Docker config directory:

```bash
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml up -d --build
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
http://192.168.68.199:8080
```

### Update Flow

After making app changes on the MacBook:

1. Test locally:

```bash
cd /Users/skrems/Projects/peptide-power-assistant
python3 -m py_compile app/server.py scripts/smoke_test.py
python3 scripts/smoke_test.py
git diff --check
```

2. Commit and push as usual.

3. Copy source to the Zimaboard:

```bash
rsync -av --delete \
  --exclude data \
  --exclude .git \
  --exclude __pycache__ \
  --exclude '*.pyc' \
  /Users/skrems/Projects/peptide-power-assistant/ \
  admin@zimaboard:/DATA/AppData/peptide-power-assistant/source/
```

4. Rebuild and restart on the Zimaboard:

```bash
ssh admin@zimaboard
cd /DATA/AppData/peptide-power-assistant/source
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker compose -f docker-compose.zima.yml up -d --build
```

5. Verify:

```bash
sudo env DOCKER_CONFIG=/DATA/AppData/peptide-power-assistant/docker-config \
  docker logs peptide-power-assistant --tail 50
```

The container should report:

```text
Peptide Power Assistant running at http://0.0.0.0:8080
```

### Backups

Before larger updates, back up the Zimaboard database:

```bash
ssh admin@zimaboard 'cp /DATA/AppData/peptide-power-assistant/data/app.db /DATA/AppData/peptide-power-assistant/data/app-backup-$(date +%Y%m%d-%H%M%S).db'
```

Container rebuilds should not erase app data because `/data` is mounted from:

```text
/DATA/AppData/peptide-power-assistant/data
```

## Data And Backups

Local MacBook SQLite database:

```text
/Users/skrems/Projects/peptide-power-assistant/data/app.db
```

Zimaboard SQLite database:

```text
/DATA/AppData/peptide-power-assistant/data/app.db
```

## Medical Safety

This app is an arithmetic and logging helper only. Confirm peptide identity, dose, route, reconstitution, and schedule with a clinician or pharmacist.
