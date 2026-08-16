# Peptide Power Assistant Agent Guide

## Start Here

- Read `README.md` and `docs/PROJECT_STATE.md` before changing code.
- Inspect `git status --short --branch` and preserve unrelated work.
- Use `app/server.py` and `docker-compose.zima.yml` to resolve stale documentation.

## Architecture

- This is a dependency-light Python web app with SQLite persistence.
- Most application behavior, routes, HTML, migrations, and validation live in `app/server.py`; static assets live in `static/`.
- Peptide Inventory is a separate app that shares this project's production SQLite file and reads `users`, `peptides`, and `dose_logs`.
- The public Peptide Protocol Library is separate and reached through `/library`.

## Verification

Run after code changes:

```bash
python3 -m py_compile app/server.py scripts/smoke_test.py
python3 scripts/smoke_test.py
git diff --check
```

Use a disposable test database. Do not test against production data.

## Data Safety

- Production SQLite is `/DATA/AppData/peptide-power-assistant/data/app.db` and is shared with Peptide Inventory.
- Before live migrations or direct edits, create a timestamped SQLite `.backup` in `/DATA/AppData/peptide-power-assistant/backups`.
- Preserve users, password hashes, dose logs, audit records, enrollments, and cross-app schema compatibility.
- Do not commit databases, backups, credentials, or exported health records.

## Application Rules

- Standard users may access only their own data; admins may act for another user only where the UI and audit trail explicitly support it.
- Keep actor identity separate from dose-owner identity in audit events.
- Calendar and protocol date calculations use `America/Los_Angeles`.
- Protocol and dose inputs accept mg or mcg, while stored values follow the existing normalized schema.
- Do not remove medical/entertainment disclaimers.

## Releases and ZimaOS

- Keep `APP_VERSION`, `docker-compose.zima.yml`, documentation, and the Git/GHCR tag synchronized.
- Use explicit public GHCR version tags, never `latest`, for ZimaOS updates.
- Production port is `8080`; persistent data remains outside the container.
- Do not push, tag, or deploy unless the user requested publication or a production update.

## Documentation Upkeep

- Update `docs/PROJECT_STATE.md` after material feature, schema, release, or deployment changes.
- Record durable architectural choices in `docs/DECISIONS.md`.
