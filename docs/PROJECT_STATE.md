# Project State

Last updated: 2026-08-15

## Summary

Peptide Power Assistant is a self-hosted Python/SQLite application for household peptide protocols, dose logging, calendar review, daily check-ins, administration, and audit history. It runs on ZimaOS and publishes versioned containers through GitHub Actions.

## Repository and Production

- Local path: `/Users/skrems/Projects/peptide-power-assistant`
- GitHub: `git@github.com:skrems/peptide-power-assistant.git`
- Branch: `main`
- Current release: `v1.18`
- Image: `ghcr.io/skrems/peptide-power-assistant:v1.18`
- ZimaOS app: `peptide-power-assistant`
- Production port: `8080`
- Application timezone: `America/Los_Angeles`

## Current Features

- Local email/password users with admin and member roles.
- Admin peptide catalog with short codes and colors.
- Protocol creation, versioned steps, publishing, retirement, enrollment, and Today view.
- Manual and protocol dose logging with mg/mcg input normalization.
- Admin dose entry and calendar review for another user.
- Dose audit events distinguish the acting user from the dose owner.
- Calendar month/day views with add, edit, delete, AM/PM previous-day copy, and injection-site selection.
- Daily check-ins and admin-only SQLite backup export.
- PWA support and public `/library` redirect to the separate Protocol Library on port `8090`.
- App version is shown in Settings.

## Database and Related Apps

Production data:

```text
/DATA/AppData/peptide-power-assistant/data/app.db
```

Peptide Inventory mounts this same file and reads:

```text
users
peptides
dose_logs
```

Changes to those tables must remain compatible with both applications. The Peptide Protocol Library is intentionally separate and does not share private data or authentication.

## Local Development

```bash
python3 -m app.server
python3 scripts/smoke_test.py
```

Default local URL is `http://127.0.0.1:8080`.

## ZimaOS Paths

```text
/DATA/AppData/peptide-power-assistant/source
/DATA/AppData/peptide-power-assistant/data/app.db
/DATA/AppData/peptide-power-assistant/backups
/DATA/AppData/peptide-power-assistant/docker-config
```

The compose file sets the stable project/container name, persistent volume, Pacific timezone, and CasaOS dashboard metadata. ZimaOS updates use explicit GHCR tags and `casaos-cli app-management apply` or the documented Docker Compose fallback.

## Important Behavior

- Calendar manual entries default to the current local time on the selected date.
- Previous-day AM copies use entries before noon and place copies at 8:00 AM; PM copies use noon or later and place copies at 8:00 PM.
- Exact target-date duplicates are skipped.
- Standard users remain limited to their own records.
- Historical and current dose audit events must retain actor, owner, request, result, and error context.

## Current Direction

Continue improving protocol and calendar workflows without weakening authorization or auditability. Treat database compatibility with Peptide Inventory as a release requirement.
