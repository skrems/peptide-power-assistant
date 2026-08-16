# Architectural Decisions

## Local Self-Hosted Python and SQLite

The application is intentionally dependency-light and self-hosted on ZimaOS. Persistent state lives in a host-mounted SQLite database rather than the container image.

## Shared Core Tables With Peptide Inventory

Peptide Inventory reads users, peptides, and dose logs from the same production database. Those shared tables are a cross-application contract.

## Explicit Actor and Owner Auditing

When admins record or change another user's dose, the audit trail stores both the acting administrator and the record owner. Authorization and audit semantics must remain separate.

## Application-Level Timezone

Calendar and protocol behavior uses `America/Los_Angeles` regardless of host timezone to prevent date-boundary errors.

## Versioned ZimaOS Releases

Production is pinned to public `linux/amd64` GHCR `vX.Y` tags. Mutable `latest` tags are not used for production updates.
