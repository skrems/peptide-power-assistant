# Peptide Power Assistant

Self-hosted protocol and dose tracker for a Zimaboard or similar local server.

This is a clean MVP rewrite. It does not use AWS, Amplify, Cognito, AppSync, or the old calculator/inventory code.

## MVP Features

- Local username/password login.
- Admin and member roles.
- Admin protocol create, edit, publish, retire, and delete.
- Editable protocol day ranges and dose steps.
- Published protocol activation for users.
- Today view showing protocol day, dose due, rest days, and completion state.
- Dose log with protocol and manual entries.
- Daily check-in notes.
- PWA manifest so the app can be added to an iPhone home screen.
- SQLite database stored at `/data/app.db`.

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

## Run On Zimaboard

```bash
docker compose up -d --build
```

Then open:

```text
http://<zimaboard-ip>:8080
```

Change these in `docker-compose.yml` before real use:

- `PEPTIDE_ADMIN_EMAIL`
- `PEPTIDE_ADMIN_PASSWORD`
- `PEPTIDE_SECRET`

## Data And Backups

The SQLite database is stored in:

```text
./data/app.db
```

Stop the container before copying the database for a simple backup:

```bash
docker compose stop
cp data/app.db data/app-backup.db
docker compose up -d
```

## Medical Safety

This app is an arithmetic and logging helper only. Confirm peptide identity, dose, route, reconstitution, and schedule with a clinician or pharmacist.

