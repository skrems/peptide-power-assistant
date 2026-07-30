from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import urllib.parse
import calendar as calendar_lib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


APP_NAME = "Peptide Power Assistant"
APP_VERSION = "v1.18"
ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
DB_PATH = Path(os.environ.get("PEPTIDE_DB", ROOT / "data" / "app.db"))
HOST = os.environ.get("PEPTIDE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PEPTIDE_PORT", "8080"))
SECRET = os.environ.get("PEPTIDE_SECRET", secrets.token_hex(32))
ADMIN_EMAIL = os.environ.get("PEPTIDE_ADMIN_EMAIL", "admin@example.local").strip().lower()
ADMIN_PASSWORD = os.environ.get("PEPTIDE_ADMIN_PASSWORD", "change-me-now")
APP_TIMEZONE_NAME = os.environ.get("PEPTIDE_TIMEZONE", os.environ.get("TZ", "America/Los_Angeles"))
PROTOCOL_LIBRARY_URL = os.environ.get("PEPTIDE_PROTOCOL_LIBRARY_URL", "").strip()
PROTOCOL_LIBRARY_PORT = os.environ.get("PEPTIDE_PROTOCOL_LIBRARY_PORT", "8090").strip()

SESSIONS: dict[str, int] = {}


def app_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(APP_TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Los_Angeles")


@dataclass
class RequestContext:
    user: sqlite3.Row | None
    flash: str | None = None
    error: str | None = None


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return datetime.now(app_timezone()).replace(tzinfo=None, microsecond=0).isoformat()


def now_datetime_local() -> str:
    return datetime.now(app_timezone()).replace(tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes")


def datetime_local_for_date(on_date: date) -> str:
    current_time = datetime.now(app_timezone()).strftime("%H:%M")
    return f"{on_date.isoformat()}T{current_time}"


def submitted_datetime_or_now(value: str | None) -> str:
    if not value:
        return now_iso()
    value = value.strip()
    if not value:
        return now_iso()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Enter a valid dose date and time.") from exc
    return parsed.replace(microsecond=0).isoformat()


def today_iso() -> str:
    return datetime.now(app_timezone()).date().isoformat()


def today_date() -> date:
    return datetime.now(app_timezone()).date()


def h(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


WEEKDAYS = [
    ("mon", "Mon"),
    ("tue", "Tue"),
    ("wed", "Wed"),
    ("thu", "Thu"),
    ("fri", "Fri"),
    ("sat", "Sat"),
    ("sun", "Sun"),
]

DEFAULT_PEPTIDE_COLORS = {
    "ghk-cu": "#7e3bb5",
    "selank": "#315f94",
    "tesamorelin": "#2f6fba",
    "mots-c": "#e86f00",
    "retatrutide": "#8b0000",
    "ss-31": "#111111",
    "bpc-157": "#0b5d2a",
    "tp-500": "#72b856",
    "tb-500": "#72b856",
}


PEPTIDE_SHORT_CODES = {
    "bpc-157": "BPC",
    "dsip": "DSIP",
    "ghk-cu": "GHK",
    "glow70": "G70",
    "ipamorelin": "IPA",
    "mots-c": "MOTS",
    "retatrutide": "RETA",
    "selank": "SEL",
    "semax": "SMX",
    "ss-31": "SS31",
    "tb-500": "TB500",
    "tesamorelin": "TESA",
    "tirzepatide": "TIRZ",
    "terzepitide": "TIRZ",
    "tp-500": "TP500",
}


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"{iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        iterations_s, salt, expected = stored.split("$", 2)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations_s))
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              display_name TEXT NOT NULL,
              role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS protocols (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              peptide_name TEXT NOT NULL,
              description TEXT,
              status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'retired')),
              created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              published_at TEXT
            );

            CREATE TABLE IF NOT EXISTS peptides (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              notes TEXT,
              color TEXT NOT NULL DEFAULT '#60706a',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS protocol_steps (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              protocol_id INTEGER NOT NULL REFERENCES protocols(id) ON DELETE CASCADE,
              sort_order INTEGER NOT NULL,
              start_day INTEGER NOT NULL,
              end_day INTEGER NOT NULL,
              dose_amount REAL NOT NULL,
              dose_unit TEXT NOT NULL DEFAULT 'mg',
              cadence_type TEXT NOT NULL CHECK (cadence_type IN ('daily', 'every_n_days', 'weekdays', 'rest')),
              interval_days INTEGER NOT NULL DEFAULT 1,
              weekdays TEXT NOT NULL DEFAULT '',
              instructions TEXT
            );

            CREATE TABLE IF NOT EXISTS enrollments (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              protocol_id INTEGER NOT NULL REFERENCES protocols(id) ON DELETE CASCADE,
              start_date TEXT NOT NULL,
              status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'completed', 'stopped')),
              reminder_time TEXT,
              notes TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dose_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              enrollment_id INTEGER REFERENCES enrollments(id) ON DELETE SET NULL,
              protocol_id INTEGER REFERENCES protocols(id) ON DELETE SET NULL,
              protocol_step_id INTEGER REFERENCES protocol_steps(id) ON DELETE SET NULL,
              protocol_day INTEGER,
              source TEXT NOT NULL CHECK (source IN ('protocol', 'manual')),
              peptide_name TEXT NOT NULL,
              scheduled_dose_amount REAL,
              actual_dose_amount REAL NOT NULL,
              dose_unit TEXT NOT NULL DEFAULT 'mg',
              status TEXT NOT NULL CHECK (status IN ('completed', 'skipped')),
              site TEXT,
              notes TEXT,
              logged_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dose_audit_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_type TEXT NOT NULL CHECK (event_type IN ('attempt', 'success', 'error')),
              action TEXT NOT NULL,
              actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
              user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
              path TEXT NOT NULL,
              log_id INTEGER,
              peptide_name TEXT,
              actual_dose_amount REAL,
              dose_unit TEXT NOT NULL DEFAULT 'mg',
              site TEXT,
              logged_at TEXT,
              return_to TEXT,
              client_ip TEXT,
              user_agent TEXT,
              error TEXT,
              payload_json TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_checkins (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              entry_date TEXT NOT NULL,
              appetite TEXT,
              energy TEXT,
              mental_acuity TEXT,
              mood TEXT,
              sleep_quality TEXT,
              side_effects TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              UNIQUE(user_id, entry_date)
            );
            """
        )
        migrate_protocol_steps(conn)
        migrate_peptides(conn)
        migrate_dose_audit_events(conn)
        seed_admin(conn)
        seed_peptides(conn)
        seed_ghk_protocol(conn)
        seed_selank_protocol(conn)
        seed_tesamorelin_protocol(conn)


def migrate_protocol_steps(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'protocol_steps'",
    ).fetchone()
    if not table:
        return
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(protocol_steps)").fetchall()}
    table_sql = table["sql"] or ""
    needs_rebuild = (
        "interval_days" not in columns
        or "weekdays" not in columns
        or "every_n_days" not in table_sql
        or "weekdays" not in table_sql
    )
    if not needs_rebuild:
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE protocol_steps RENAME TO protocol_steps_old")
    conn.execute(
        """
        CREATE TABLE protocol_steps (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          protocol_id INTEGER NOT NULL REFERENCES protocols(id) ON DELETE CASCADE,
          sort_order INTEGER NOT NULL,
          start_day INTEGER NOT NULL,
          end_day INTEGER NOT NULL,
          dose_amount REAL NOT NULL,
          dose_unit TEXT NOT NULL DEFAULT 'mg',
          cadence_type TEXT NOT NULL CHECK (cadence_type IN ('daily', 'every_n_days', 'weekdays', 'rest')),
          interval_days INTEGER NOT NULL DEFAULT 1,
          weekdays TEXT NOT NULL DEFAULT '',
          instructions TEXT
        )
        """
    )
    old_columns = {row["name"] for row in conn.execute("PRAGMA table_info(protocol_steps_old)").fetchall()}
    interval_expr = "interval_days" if "interval_days" in old_columns else "1"
    weekdays_expr = "weekdays" if "weekdays" in old_columns else "''"
    conn.execute(
        f"""
        INSERT INTO protocol_steps
          (id, protocol_id, sort_order, start_day, end_day, dose_amount, dose_unit,
           cadence_type, interval_days, weekdays, instructions)
        SELECT
          id,
          protocol_id,
          sort_order,
          start_day,
          end_day,
          dose_amount,
          dose_unit,
          CASE
            WHEN cadence_type IN ('daily', 'every_n_days', 'weekdays', 'rest') THEN cadence_type
            ELSE 'daily'
          END,
          COALESCE({interval_expr}, 1),
          COALESCE({weekdays_expr}, ''),
          instructions
        FROM protocol_steps_old
        """
    )
    conn.execute("DROP TABLE protocol_steps_old")
    conn.execute("PRAGMA foreign_keys = ON")


def migrate_peptides(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(peptides)").fetchall()}
    if "color" not in columns:
        conn.execute("ALTER TABLE peptides ADD COLUMN color TEXT NOT NULL DEFAULT '#60706a'")
    for peptide_key, color in DEFAULT_PEPTIDE_COLORS.items():
        conn.execute(
            "UPDATE peptides SET color = ? WHERE lower(name) = ? AND (color IS NULL OR color = '' OR color = '#60706a')",
            (color, peptide_key),
        )


def migrate_dose_audit_events(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(dose_audit_events)").fetchall()}
    if "actor_user_id" not in columns:
        conn.execute("ALTER TABLE dose_audit_events ADD COLUMN actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")


def seed_admin(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
    if row:
        return
    conn.execute(
        """
        INSERT INTO users (email, password_hash, display_name, role, active, created_at)
        VALUES (?, ?, ?, 'admin', 1, ?)
        """,
        (ADMIN_EMAIL, password_hash(ADMIN_PASSWORD), "Admin", now_iso()),
    )


def seed_peptides(conn: sqlite3.Connection) -> None:
    defaults = [
        ("GHK-Cu", "Copper peptide protocols.", DEFAULT_PEPTIDE_COLORS["ghk-cu"]),
        ("Selank", "SK10 cycle protocol candidate.", DEFAULT_PEPTIDE_COLORS["selank"]),
        ("Tesamorelin", "TSM10 weekday cycle protocol candidate.", DEFAULT_PEPTIDE_COLORS["tesamorelin"]),
        ("MOTS-c", "Weekday cadence protocol candidate.", DEFAULT_PEPTIDE_COLORS["mots-c"]),
        ("Retatrutide", "Weekly or every-six-days protocol candidate.", DEFAULT_PEPTIDE_COLORS["retatrutide"]),
        ("SS-31", "Daily protocol candidate.", DEFAULT_PEPTIDE_COLORS["ss-31"]),
        ("BPC-157", "Recovery protocol candidate.", DEFAULT_PEPTIDE_COLORS["bpc-157"]),
        ("TP-500", "Recovery protocol candidate.", DEFAULT_PEPTIDE_COLORS["tp-500"]),
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO peptides (name, notes, color, created_at)
        VALUES (?, ?, ?, ?)
        """,
        [(name, notes, color, now_iso()) for name, notes, color in defaults],
    )


def seed_ghk_protocol(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT id FROM protocols WHERE name = ?", ("GHK-Cu 60-day ramp",)).fetchone()
    if exists:
        return
    admin = conn.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    created_by = admin["id"] if admin else None
    cursor = conn.execute(
        """
        INSERT INTO protocols
          (name, peptide_name, description, status, created_by, created_at, updated_at, published_at)
        VALUES (?, ?, ?, 'published', ?, ?, ?, ?)
        """,
        (
            "GHK-Cu 60-day ramp",
            "GHK-Cu",
            "Days 1-15 1 mg/day, days 16-30 2 mg/day, days 31-60 rest.",
            created_by,
            now_iso(),
            now_iso(),
            now_iso(),
        ),
    )
    protocol_id = cursor.lastrowid
    steps = [
        (1, 1, 15, 1.0, "daily", "Phase 1"),
        (2, 16, 30, 2.0, "daily", "Phase 2"),
        (3, 31, 60, 0.0, "rest", "Rest period"),
    ]
    conn.executemany(
        """
        INSERT INTO protocol_steps
          (protocol_id, sort_order, start_day, end_day, dose_amount, dose_unit, cadence_type, instructions)
        VALUES (?, ?, ?, ?, ?, 'mg', ?, ?)
        """,
        [(protocol_id, *step) for step in steps],
    )


def seed_selank_protocol(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT id FROM protocols WHERE name = ?", ("Selank SK10 12-week cycle",)).fetchone()
    if exists:
        return
    admin = conn.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    created_by = admin["id"] if admin else None
    cursor = conn.execute(
        """
        INSERT INTO protocols
          (name, peptide_name, description, status, created_by, created_at, updated_at, published_at)
        VALUES (?, ?, ?, 'published', ?, ?, ?, ?)
        """,
        (
            "Selank SK10 12-week cycle",
            "Selank",
            "User-provided 12-week cycle: weeks 1-4 on, weeks 5-6 off, weeks 7-10 on, weeks 11-12 off. Dose steps store mcg values as mg.",
            created_by,
            now_iso(),
            now_iso(),
            now_iso(),
        ),
    )
    protocol_id = cursor.lastrowid
    steps = [
        (1, 1, 3, 0.25, "daily", "5 units (250 mcg) once daily in the morning; assess tolerance."),
        (2, 4, 14, 0.4, "daily", "6-8 units (300-400 mcg) once daily or split 2x/day; upper example dose stored."),
        (3, 15, 28, 0.5, "daily", "8-10 units (400-500 mcg) daily; optional higher range noted in source protocol."),
        (4, 29, 42, 0.0, "rest", "Off/reset period. No Selank."),
        (5, 43, 70, 0.5, "daily", "Maintenance using optimized Phase 1 dose, example 8-10 units (400-500 mcg) daily."),
        (6, 71, 84, 0.0, "rest", "Off/reset period. No Selank."),
    ]
    conn.executemany(
        """
        INSERT INTO protocol_steps
          (protocol_id, sort_order, start_day, end_day, dose_amount, dose_unit, cadence_type, instructions)
        VALUES (?, ?, ?, ?, ?, 'mg', ?, ?)
        """,
        [(protocol_id, *step) for step in steps],
    )


def seed_tesamorelin_protocol(conn: sqlite3.Connection) -> None:
    exists = conn.execute("SELECT id FROM protocols WHERE name = ?", ("Tesamorelin TSM10 12-week cycle",)).fetchone()
    if exists:
        return
    admin = conn.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    created_by = admin["id"] if admin else None
    cursor = conn.execute(
        """
        INSERT INTO protocols
          (name, peptide_name, description, status, created_by, created_at, updated_at, published_at)
        VALUES (?, ?, ?, 'published', ?, ?, ?, ?)
        """,
        (
            "Tesamorelin TSM10 12-week cycle",
            "Tesamorelin",
            "User-provided 12-week cycle using 5 on / 2 off cadence with Saturday and Sunday off. Evening timing notes are stored in each step.",
            created_by,
            now_iso(),
            now_iso(),
            now_iso(),
        ),
    )
    protocol_id = cursor.lastrowid
    weekdays = "mon,tue,wed,thu,fri"
    steps = [
        (1, 1, 7, 1.0, "weekdays", weekdays, "Week 1: 20 units (1 mg) in the evening; assess tolerance."),
        (2, 8, 28, 1.5, "weekdays", weekdays, "Weeks 2-4: 30-40 units (1.5 mg) in the evening if tolerated."),
        (3, 29, 56, 2.0, "weekdays", weekdays, "Weeks 5-8 maintenance: 40 units (2 mg) or optimized dose in the evening."),
        (4, 57, 84, 2.0, "weekdays", weekdays, "Weeks 9-12: continue maintenance or taper if desired; evening timing."),
    ]
    conn.executemany(
        """
        INSERT INTO protocol_steps
          (protocol_id, sort_order, start_day, end_day, dose_amount, dose_unit,
           cadence_type, weekdays, instructions)
        VALUES (?, ?, ?, ?, ?, 'mg', ?, ?, ?)
        """,
        [(protocol_id, *step) for step in steps],
    )


def query(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def one(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, args).fetchone()


def parse_cookies(raw: str | None) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if not raw:
        return cookies
    for part in raw.split(";"):
        if "=" in part:
            key, value = part.strip().split("=", 1)
            cookies[key] = value
    return cookies


def nav_item(path: str, label: str, active: str, icon: str) -> str:
    current = "active" if active == path else ""
    return f'<a class="{current}" href="{path}">{icon}<span>{label}</span></a>'


def nav_link(path: str, label: str, icon_svg: str) -> str:
    return f'<a href="{path}">{icon_svg}<span>{label}</span></a>'


def icon(name: str) -> str:
    icons = {
        "today": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 5h16v15H4z"/><path d="M8 3v4M16 3v4M4 10h16"/></svg>',
        "protocols": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M7 4h10l3 3v13H7z"/><path d="M17 4v4h4M10 12h7M10 16h7"/></svg>',
        "library": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 4h11l3 3v13H5z"/><path d="M16 4v4h4M8 12h8M8 16h6"/></svg>',
        "log": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 4h14v16H5z"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>',
        "calendar": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 5h16v15H4z"/><path d="M8 3v4M16 3v4M4 10h16"/><path d="M8 14h.01M12 14h.01M16 14h.01M8 17h.01M12 17h.01"/></svg>',
        "admin": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3l7 4v5c0 5-3 8-7 9-4-1-7-4-7-9V7z"/><path d="M9 12l2 2 4-5"/></svg>',
        "settings": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z"/><path d="M4 12h2M18 12h2M12 4v2M12 18v2"/></svg>',
    }
    return icons[name]


def layout(ctx: RequestContext, active: str, title: str, body: str) -> bytes:
    user = ctx.user
    admin_nav = nav_item("/admin", "Admin", active, icon("admin")) if user and user["role"] == "admin" else ""
    flash = f'<div class="flash">{h(ctx.flash)}</div>' if ctx.flash else ""
    error = f'<div class="flash error">{h(ctx.error)}</div>' if ctx.error else ""
    user_chip = ""
    if user:
        user_chip = f"""
        <div class="user-chip">
          <strong>{h(user['display_name'])}</strong>
          <span>{h(user['role'])}</span>
          <form method="post" action="/logout"><button class="text" type="submit">Log out</button></form>
        </div>
        """
    html = f"""<!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
      <meta name="theme-color" content="#0f7c72">
      <title>{h(title)} · {APP_NAME}</title>
      <link rel="stylesheet" href="/static/styles.css">
      <link rel="manifest" href="/static/manifest.webmanifest">
      <link rel="apple-touch-icon" href="/static/icon.svg">
    </head>
    <body>
      <main class="app-shell">
        <header class="topbar">
          <div>
            <p class="eyebrow">Peptide Power Assistant</p>
            <h1>{h(title)}</h1>
          </div>
          {user_chip}
        </header>
        <div class="notice">Arithmetic and tracking helper only. Confirm peptide identity, dose, route, reconstitution, and schedule with a clinician or pharmacist.</div>
        {flash}
        {error}
        {body}
      </main>
      <nav class="tabs">
        {nav_item("/", "Today", active, icon("today"))}
        {nav_item("/protocols", "Protocols", active, icon("protocols"))}
        {nav_link("/library", "Library", icon("library"))}
        {nav_item("/log", "Log", active, icon("log"))}
        {nav_item("/calendar", "Calendar", active, icon("calendar"))}
        {admin_nav}
        {nav_item("/settings", "Settings", active, icon("settings"))}
      </nav>
      <script>
        if ("serviceWorker" in navigator) {{
          navigator.serviceWorker.register("/service-worker.js").catch(() => undefined);
        }}
        document.addEventListener("click", (event) => {{
          const button = event.target.closest(".site-button");
          if (!button) return;
          const form = button.closest("form");
          const input = form && form.querySelector('input[name="site"]');
          if (!input) return;
          input.value = button.dataset.site || "";
          form.querySelectorAll(".site-button").forEach((candidate) => {{
            const selected = candidate === button;
            candidate.classList.toggle("selected", selected);
            candidate.setAttribute("aria-pressed", selected ? "true" : "false");
          }});
        }});
        document.addEventListener("click", (event) => {{
          const button = event.target.closest(".time-button");
          if (!button) return;
          const form = button.closest("form");
          const input = form && form.querySelector('input[name="logged_at"]');
          if (!input) return;
          const datePart = (input.value || new Date().toISOString().slice(0, 16)).slice(0, 10);
          input.value = `${{datePart}}T${{button.dataset.time || "08:00"}}`;
          form.querySelectorAll(".time-button").forEach((candidate) => {{
            candidate.classList.toggle("selected", candidate === button);
          }});
        }});
      </script>
    </body>
    </html>"""
    return html.encode("utf-8")


def login_page(error: str | None = None) -> bytes:
    error_html = f'<div class="flash error">{h(error)}</div>' if error else ""
    html = f"""<!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
      <meta name="theme-color" content="#0f7c72">
      <title>Log in · {APP_NAME}</title>
      <link rel="stylesheet" href="/static/styles.css">
      <link rel="manifest" href="/static/manifest.webmanifest">
    </head>
    <body class="auth">
      <section class="auth-card">
        <div class="panel">
          <p class="eyebrow">Local protocol notebook</p>
          <h1>{APP_NAME}</h1>
          <p class="meta">Self-hosted dose tracking and protocol management.</p>
          <div class="notice">Confirm peptide identity, dose, route, reconstitution, and schedule with a clinician or pharmacist.</div>
          {error_html}
          <form method="post" action="/login" class="stack">
            <label>Email <input name="email" type="email" autocomplete="username" required></label>
            <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
            <button type="submit">Log in</button>
          </form>
          <p class="meta"><a href="/library">Open public protocol library</a></p>
        </div>
      </section>
    </body>
    </html>"""
    return html.encode("utf-8")


def protocol_day(start_date: str, on_date: str | None = None) -> int:
    start = date.fromisoformat(start_date)
    current = date.fromisoformat(on_date or today_iso())
    return (current - start).days + 1


def format_dose(amount: Any) -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "0"
    return f"{value:g}"


def parse_dose_mg(value: str, label: str = "Dose") -> float:
    raw = (value or "").strip().lower()
    raw = raw.replace(",", "").replace("\u00b5", "u").replace("\u03bc", "u")
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([a-z]*)", raw)
    if not match:
        raise ValueError(f"{label} must be a number, optionally followed by mg or mcg.")
    amount = float(match.group(1))
    unit = match.group(2) or "mg"
    if unit in {"mg", "milligram", "milligrams"}:
        return amount
    if unit in {"mcg", "ug", "microgram", "micrograms"}:
        return amount / 1000
    raise ValueError(f"{label} unit must be mg or mcg.")


def peptide_key(name: str | None) -> str:
    return (name or "").strip().lower()


def normalize_color(value: str | None, fallback: str = "#60706a") -> str:
    raw = (value or "").strip()
    if len(raw) == 7 and raw.startswith("#") and all(char in "0123456789abcdefABCDEF" for char in raw[1:]):
        return raw.lower()
    return fallback


def default_color_for_peptide(name: str | None) -> str:
    return DEFAULT_PEPTIDE_COLORS.get(peptide_key(name), "#60706a")


def peptide_colors(conn: sqlite3.Connection) -> dict[str, str]:
    colors = {key: color for key, color in DEFAULT_PEPTIDE_COLORS.items()}
    rows = query(conn, "SELECT name, color FROM peptides")
    for row in rows:
        colors[peptide_key(row["name"])] = normalize_color(row["color"], default_color_for_peptide(row["name"]))
    return colors


def color_for_peptide(name: str | None, colors: dict[str, str]) -> str:
    key = peptide_key(name)
    return colors.get(key, default_color_for_peptide(name))


def peptide_chip(name: str, color: str) -> str:
    return f'<span class="peptide-chip" style="--peptide-color: {h(normalize_color(color))}">{h(name)}</span>'


def peptide_short_code(name: str | None) -> str:
    raw = (name or "").strip()
    if not raw:
        return "UNK"
    key = peptide_key(raw)
    normalized_key = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
    compact_key = re.sub(r"[^a-z0-9]", "", key)
    for candidate in (key, normalized_key, compact_key):
        if candidate in PEPTIDE_SHORT_CODES:
            return PEPTIDE_SHORT_CODES[candidate]
    compact = re.sub(r"[^a-zA-Z0-9]", "", raw).upper()
    if any(char.isdigit() for char in compact):
        return compact[:5] or "UNK"
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", raw) if part]
    if len(parts) > 1:
        return "".join(part[0] for part in parts).upper()[:5]
    return compact[:5] or "UNK"


def add_months(month_start: date, months: int) -> date:
    month_index = month_start.month - 1 + months
    year = month_start.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def month_from_params(params: dict[str, list[str]]) -> date:
    raw = params.get("month", [""])[0]
    if raw:
        try:
            parsed = datetime.strptime(raw, "%Y-%m").date()
            return date(parsed.year, parsed.month, 1)
        except ValueError:
            pass
    today = today_date()
    return date(today.year, today.month, 1)


def date_from_param(value: str | None, fallback: date) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return fallback


def int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_weekdays(raw: str | None) -> str:
    if not raw:
        return ""
    aliases = {code: code for code, _label in WEEKDAYS}
    aliases.update(
        {
            "monday": "mon",
            "tuesday": "tue",
            "wednesday": "wed",
            "thursday": "thu",
            "friday": "fri",
            "saturday": "sat",
            "sunday": "sun",
        }
    )
    selected: list[str] = []
    for part in raw.replace(";", ",").split(","):
        key = part.strip().lower()
        if not key:
            continue
        key = aliases.get(key, aliases.get(key[:3], ""))
        if key and key not in selected:
            selected.append(key)
    allowed = [code for code, _label in WEEKDAYS]
    return ",".join(code for code in allowed if code in selected)


def weekday_label_list(raw: str | None) -> str:
    selected = set(normalize_weekdays(raw).split(",")) if raw else set()
    labels = [label for code, label in WEEKDAYS if code in selected]
    return ", ".join(labels)


def cadence_label(step: sqlite3.Row) -> str:
    cadence = step["cadence_type"]
    if cadence == "rest" or float(step["dose_amount"]) <= 0:
        return "Rest"
    if cadence == "every_n_days":
        interval = max(1, int_or_default(step["interval_days"], 1))
        return f"Every {interval} day{'s' if interval != 1 else ''}"
    if cadence == "weekdays":
        labels = weekday_label_list(step["weekdays"])
        return labels or "Selected weekdays"
    return "Daily"


def cadence_select(selected: str = "daily") -> str:
    options = [
        ("daily", "Daily"),
        ("every_n_days", "Every N days"),
        ("weekdays", "Selected weekdays"),
        ("rest", "Rest"),
    ]
    return "\n".join(
        f'<option value="{value}" {"selected" if value == selected else ""}>{label}</option>'
        for value, label in options
    )


def weekday_checkboxes(selected_raw: str | None = "") -> str:
    selected = set(normalize_weekdays(selected_raw).split(",")) if selected_raw else set()
    return "".join(
        f"""
        <label class="checkbox-chip">
          <input type="checkbox" name="weekdays" value="{code}" {"checked" if code in selected else ""}>
          {label}
        </label>
        """
        for code, label in WEEKDAYS
    )


def step_is_due_on(step: sqlite3.Row, protocol_day_value: int, iso_date: str) -> bool:
    cadence = step["cadence_type"]
    if cadence == "rest" or float(step["dose_amount"]) <= 0:
        return True
    if cadence == "daily":
        return True
    if cadence == "every_n_days":
        interval = max(1, int_or_default(step["interval_days"], 1))
        return (protocol_day_value - int(step["start_day"])) % interval == 0
    if cadence == "weekdays":
        selected = set(normalize_weekdays(step["weekdays"]).split(","))
        weekday_code = WEEKDAYS[date.fromisoformat(iso_date).weekday()][0]
        return weekday_code in selected
    return True


def step_cadence_fields(selected: str = "daily", interval_days: Any = 1, weekdays: str | None = "") -> str:
    return f"""
    <div class="step-cadence-grid">
      <label>Cadence
        <select name="cadence_type">
          {cadence_select(selected)}
        </select>
      </label>
      <label>Every N days <input name="interval_days" inputmode="numeric" value="{max(1, int_or_default(interval_days, 1))}"></label>
      <label class="weekday-field">Weekdays
        <div class="checkbox-grid">
          {weekday_checkboxes(weekdays)}
        </div>
      </label>
    </div>
    """


def peptide_name_from_form(data: dict[str, str]) -> str:
    custom = data.get("peptide_name_other", "").strip()
    selected = data.get("peptide_name", "").strip()
    return custom or selected


def peptide_select(conn: sqlite3.Connection, selected: str = "", include_other: bool = True) -> str:
    peptides = query(conn, "SELECT * FROM peptides ORDER BY name")
    names = [row["name"] for row in peptides]
    options = []
    if selected and selected not in names:
        options.append(f'<option value="{h(selected)}" selected>{h(selected)}</option>')
    for peptide in peptides:
        is_selected = "selected" if peptide["name"] == selected else ""
        options.append(f'<option value="{h(peptide["name"])}" {is_selected}>{h(peptide["name"])}</option>')
    if include_other:
        options.append('<option value="">Other / type below</option>')
    return "\n".join(options)


def injection_site_picker(selected_site: str = "") -> str:
    sites = [
        "Left Deltoid",
        "Right Deltoid",
        "Left Abdomen",
        "Right Abdomen",
        "Abdomen Far Left",
        "Abdomen Far Right",
        "Left Thigh",
        "Right Thigh",
        "Left Left Thigh",
        "Right Right Thigh",
    ]
    buttons = "".join(
        f'<button class="site-button {"selected" if site == selected_site else ""}" type="button" data-site="{h(site)}" aria-pressed="{"true" if site == selected_site else "false"}">{h(site)}</button>'
        for site in sites
    )
    return f"""
    <div class="site-picker">
      <div class="site-figure">
        <img src="/static/body-map.svg" alt="Body diagram with common injection regions">
        <span>Quick site</span>
      </div>
      <div class="site-options" aria-label="Injection site choices">
        {buttons}
      </div>
    </div>
    """


def logged_at_picker(value: str | None = None) -> str:
    field_value = (value or now_datetime_local()).replace("T", " ")[:16].replace(" ", "T")
    return f"""
    <div class="datetime-picker">
      <label>Dose date and time
        <input name="logged_at" type="datetime-local" value="{h(field_value)}" required>
      </label>
      <div class="time-shortcuts" aria-label="Quick time choices">
        <button class="time-button" type="button" data-time="08:00">Morning</button>
        <button class="time-button" type="button" data-time="20:00">Night</button>
      </div>
    </div>
    """


def with_flash(path: str, message: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}flash={urllib.parse.quote(message)}"


def safe_return_to(value: str | None, fallback: str = "/log") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme or parsed.netloc or not raw.startswith("/") or raw.startswith("//"):
        return fallback
    return raw


def dose_audit_payload(data: dict[str, str]) -> str:
    allowed = {
        "actual_dose_amount",
        "dose_unit",
        "enrollment_id",
        "log_id",
        "logged_at",
        "peptide_name",
        "protocol_day",
        "return_to",
        "site",
        "step_id",
        "target_user_id",
        "target_date",
        "copy_period",
    }
    payload = {key: data.get(key, "") for key in sorted(allowed) if key in data}
    return json.dumps(payload, sort_keys=True)


def dose_action_for_path(path: str, data: dict[str, str]) -> str:
    if path == "/log/protocol":
        return "protocol_create"
    if path == "/logs/delete":
        return "delete"
    if path == "/logs/copy-previous-day":
        return "copy_previous_day"
    if path == "/log/manual":
        return "manual_create"
    if path == "/logs/save" and data.get("log_id"):
        return "manual_update"
    if path == "/logs/save":
        return "manual_create"
    return "dose_action"


def current_path(path: str, params: dict[str, list[str]], exclude: set[str] | None = None) -> str:
    excluded = {"flash", "error"} | (exclude or set())
    clean_params = {
        key: values[-1]
        for key, values in params.items()
        if values and values[-1] and key not in excluded
    }
    query = urllib.parse.urlencode(clean_params)
    return f"{path}?{query}" if query else path


def host_with_port(host_header: str, port: str) -> str:
    host = (host_header or "").strip()
    if not host:
        return f"127.0.0.1:{port}"
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return f"{host[:end + 1]}:{port}"
    if ":" in host:
        return f"{host.rsplit(':', 1)[0]}:{port}"
    return f"{host}:{port}"


def log_form(
    conn: sqlite3.Connection,
    row: sqlite3.Row | None = None,
    *,
    actor_user: sqlite3.Row | None = None,
    target_user_id: int | None = None,
    return_to: str = "/log",
    default_logged_at: str | None = None,
    button_label: str = "Save dose",
) -> str:
    log_id = row["id"] if row else ""
    peptide_name = row["peptide_name"] if row else ""
    dose_amount = format_dose(row["actual_dose_amount"]) if row else ""
    site = row["site"] if row else ""
    notes = row["notes"] if row else ""
    logged_at = row["logged_at"] if row else default_logged_at
    owner_id = int(row["user_id"]) if row else int(target_user_id or (actor_user["id"] if actor_user else 0))
    owner_field = ""
    if actor_user and actor_user["role"] == "admin":
        if row:
            owner = one(conn, "SELECT display_name, email FROM users WHERE id = ?", (owner_id,))
            owner_label = owner["display_name"] if owner else "Unknown user"
            owner_field = f'<label>Log for <input value="{h(owner_label)}" disabled><input type="hidden" name="target_user_id" value="{owner_id}"></label>'
        else:
            options = []
            for user in query(conn, "SELECT id, display_name, email FROM users WHERE active = 1 ORDER BY display_name, email"):
                label = f"{user['display_name']} ({user['email']})"
                selected = " selected" if int(user["id"]) == owner_id else ""
                options.append(f'<option value="{user["id"]}"{selected}>{h(label)}</option>')
            owner_field = f'<label>Log for <select name="target_user_id">{"".join(options)}</select></label>'
    return f"""
    <form method="post" action="/logs/save" class="stack">
      <input type="hidden" name="log_id" value="{log_id}">
      <input type="hidden" name="return_to" value="{h(return_to)}">
      {logged_at_picker(logged_at)}
      {owner_field}
      <div class="grid three">
        <label>Peptide
          <select name="peptide_name">
            {peptide_select(conn, peptide_name)}
          </select>
        </label>
        <label>Dose <input name="actual_dose_amount" inputmode="decimal" value="{h(dose_amount)}" placeholder="1 mg or 400 mcg" required></label>
        <label>Site <input name="site" value="{h(site)}" placeholder="optional"></label>
      </div>
      {injection_site_picker(site)}
      <label>Other peptide name <input name="peptide_name_other" placeholder="Only needed if not in the dropdown"></label>
      <label>Notes <input name="notes" value="{h(notes)}" placeholder="optional"></label>
      <div class="button-row"><button type="submit">{h(button_label)}</button></div>
    </form>
    """


def get_due_tasks(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    current_date = today_iso()
    enrollments = query(
        conn,
        """
        SELECT e.*, p.name, p.peptide_name
        FROM enrollments e
        JOIN protocols p ON p.id = e.protocol_id
        WHERE e.user_id = ? AND e.status = 'active'
        ORDER BY e.start_date, p.name
        """,
        (user_id,),
    )
    for enrollment in enrollments:
        day = protocol_day(enrollment["start_date"], current_date)
        step = one(
            conn,
            """
            SELECT * FROM protocol_steps
            WHERE protocol_id = ? AND start_day <= ? AND end_day >= ?
            ORDER BY sort_order
            LIMIT 1
            """,
            (enrollment["protocol_id"], day, day),
        )
        if step and not step_is_due_on(step, day, current_date):
            continue
        log = one(
            conn,
            """
            SELECT * FROM dose_logs
            WHERE user_id = ? AND enrollment_id = ? AND protocol_day = ?
            ORDER BY logged_at DESC
            LIMIT 1
            """,
            (user_id, enrollment["id"], day),
        )
        tasks.append({"enrollment": enrollment, "day": day, "step": step, "log": log})
    return tasks


def render_today(ctx: RequestContext, conn: sqlite3.Connection) -> bytes:
    assert ctx.user
    colors = peptide_colors(conn)
    tasks = get_due_tasks(conn, ctx.user["id"])
    cards: list[str] = []
    for task in tasks:
        enrollment = task["enrollment"]
        step = task["step"]
        log = task["log"]
        day = task["day"]
        if not step:
            cards.append(
                f"""
                <article class="item">
                  <div class="item-title"><h3>{h(enrollment['name'])}</h3><span class="badge">day {day}</span></div>
                  <p class="meta">No protocol step is defined for today.</p>
                </article>
                """
            )
            continue
        is_rest = step["cadence_type"] == "rest" or float(step["dose_amount"]) <= 0
        badge = "rest" if is_rest else ("warn" if not log else "")
        badge_text = "rest day" if is_rest else ("done" if log else "due")
        action = ""
        if not is_rest and not log:
            action = f"""
            <form method="post" action="/log/protocol" class="grid two">
              <input type="hidden" name="enrollment_id" value="{enrollment['id']}">
              <input type="hidden" name="step_id" value="{step['id']}">
              <input type="hidden" name="protocol_day" value="{day}">
              <label>Actual dose <input name="actual_dose_amount" inputmode="decimal" value="{format_dose(step['dose_amount'])}" placeholder="1 mg or 400 mcg" required></label>
              <label>Site <input name="site" placeholder="optional"></label>
              <label class="grid-span">Notes <input name="notes" placeholder="optional"></label>
              {injection_site_picker()}
              <div class="button-row"><button type="submit">Log completed</button></div>
            </form>
            """
        elif log:
            action = f'<p class="meta">Logged {h(log["logged_at"])} · {format_dose(log["actual_dose_amount"])} {h(log["dose_unit"])}</p>'
        cards.append(
            f"""
            <article class="item">
              <div class="item-title">
                <div>
                  <h3>{h(enrollment['name'])}</h3>
                  <p class="meta">{h(enrollment['peptide_name'])} · protocol day {day}</p>
                </div>
                <span class="badge {badge}">{badge_text}</span>
              </div>
              <p>{format_dose(step['dose_amount'])} {h(step['dose_unit'])} · {h(cadence_label(step))} · days {step['start_day']}-{step['end_day']}</p>
              <p class="meta">{h(step['instructions'])}</p>
              {action}
            </article>
            """
        )
    task_html = "".join(cards) if cards else '<div class="empty">No active protocol doses today. Activate a published protocol to begin tracking.</div>'
    recent = query(
        conn,
        "SELECT * FROM dose_logs WHERE user_id = ? ORDER BY logged_at DESC LIMIT 5",
        (ctx.user["id"],),
    )
    recent_html = "".join(
        f"""
        <article class="item">
          <div class="item-title"><h3>{peptide_chip(row['peptide_name'], color_for_peptide(row['peptide_name'], colors))}</h3><span class="badge">{h(row['status'])}</span></div>
          <p class="meta">{h(row['logged_at'])} · {format_dose(row['actual_dose_amount'])} {h(row['dose_unit'])}</p>
          <p class="meta">{h(row['notes'])}</p>
        </article>
        """
        for row in recent
    ) or '<div class="empty">No dose logs yet.</div>'
    body = f"""
    <section class="panel">
      <div class="panel-head">
        <div><p class="eyebrow">At a glance</p><h2>Due today</h2></div>
        <span class="badge">{today_date().strftime('%b %-d, %Y') if sys.platform != 'win32' else today_date().strftime('%b %#d, %Y')}</span>
      </div>
      <div class="card-list">{task_html}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Daily check-in</h2></div>
      {daily_checkin_form(conn, ctx.user['id'])}
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Recent log</h2><a class="button secondary" href="/log">View all</a></div>
      <div class="card-list">{recent_html}</div>
    </section>
    """
    return layout(ctx, "/", "Today", body)


def daily_checkin_form(conn: sqlite3.Connection, user_id: int) -> str:
    row = one(conn, "SELECT * FROM daily_checkins WHERE user_id = ? AND entry_date = ?", (user_id, today_iso()))
    def selected(name: str, value: str) -> str:
        return "selected" if row and row[name] == value else ""

    notes = row["notes"] if row else ""
    side_effects = row["side_effects"] if row else ""
    return f"""
    <form method="post" action="/checkin" class="stack">
      <div class="grid three">
        <label>Appetite
          <select name="appetite">
            <option {selected('appetite', 'same')} value="same">same</option>
            <option {selected('appetite', 'down')} value="down">decreased</option>
            <option {selected('appetite', 'up')} value="up">increased</option>
          </select>
        </label>
        <label>Energy
          <select name="energy">
            <option {selected('energy', 'same')} value="same">same</option>
            <option {selected('energy', 'down')} value="down">down</option>
            <option {selected('energy', 'up')} value="up">up</option>
          </select>
        </label>
        <label>Mental acuity
          <select name="mental_acuity">
            <option {selected('mental_acuity', 'same')} value="same">same</option>
            <option {selected('mental_acuity', 'down')} value="down">decreased</option>
            <option {selected('mental_acuity', 'up')} value="up">increased</option>
          </select>
        </label>
        <label>Mood
          <select name="mood">
            <option {selected('mood', 'same')} value="same">same</option>
            <option {selected('mood', 'down')} value="down">down</option>
            <option {selected('mood', 'up')} value="up">up</option>
          </select>
        </label>
        <label>Sleep
          <select name="sleep_quality">
            <option {selected('sleep_quality', 'ok')} value="ok">ok</option>
            <option {selected('sleep_quality', 'poor')} value="poor">poor</option>
            <option {selected('sleep_quality', 'good')} value="good">good</option>
          </select>
        </label>
      </div>
      <label>Side effects <input name="side_effects" value="{h(side_effects)}" placeholder="optional"></label>
      <label>Notes <textarea name="notes" placeholder="daily notes">{h(notes)}</textarea></label>
      <div class="button-row"><button type="submit">Save check-in</button></div>
    </form>
    """


def render_protocols(ctx: RequestContext, conn: sqlite3.Connection, params: dict[str, list[str]]) -> bytes:
    assert ctx.user
    edit_id = int(params.get("edit", ["0"])[0] or 0)
    protocols = query(conn, "SELECT * FROM protocols ORDER BY status, name")
    steps = query(conn, "SELECT * FROM protocol_steps ORDER BY protocol_id, sort_order, start_day")
    step_map: dict[int, list[sqlite3.Row]] = {}
    for step in steps:
        step_map.setdefault(step["protocol_id"], []).append(step)

    editor = ""
    if ctx.user["role"] == "admin":
        editing = one(conn, "SELECT * FROM protocols WHERE id = ?", (edit_id,)) if edit_id else None
        editor = protocol_editor(conn, editing)

    cards = []
    for protocol in protocols:
        protocol_steps = step_map.get(protocol["id"], [])
        step_text = "<br>".join(
            f"Days {step['start_day']}-{step['end_day']}: {format_dose(step['dose_amount'])} {h(step['dose_unit'])} · {h(cadence_label(step))}"
            for step in protocol_steps
        )
        actions = []
        if protocol["status"] == "published":
            actions.append(
                f"""
                <form class="inline-form" method="post" action="/protocols/activate">
                  <input type="hidden" name="protocol_id" value="{protocol['id']}">
                  <button class="secondary" type="submit">Activate</button>
                </form>
                """
            )
        if ctx.user["role"] == "admin":
            actions.append(f'<a class="button secondary" href="/protocols?edit={protocol["id"]}">Edit</a>')
            if protocol["status"] == "draft":
                actions.append(
                    f"""
                    <form class="inline-form" method="post" action="/protocols/publish">
                      <input type="hidden" name="protocol_id" value="{protocol['id']}">
                      <button type="submit">Publish</button>
                    </form>
                    """
                )
            if protocol["status"] != "retired":
                actions.append(
                    f"""
                    <form class="inline-form" method="post" action="/protocols/retire">
                      <input type="hidden" name="protocol_id" value="{protocol['id']}">
                      <button class="danger" type="submit">Retire</button>
                    </form>
                    """
                )
            actions.append(
                f"""
                <form class="inline-form" method="post" action="/protocols/delete" onsubmit="return confirm('Delete this protocol? Logs connected to it may keep their historical text only.');">
                  <input type="hidden" name="protocol_id" value="{protocol['id']}">
                  <button class="danger" type="submit">Delete</button>
                </form>
                """
            )
        cards.append(
            f"""
            <article class="item">
              <div class="item-title">
                <div>
                  <h3>{h(protocol['name'])}</h3>
                  <p class="meta">{h(protocol['peptide_name'])} · {h(protocol['description'])}</p>
                </div>
                <span class="badge">{h(protocol['status'])}</span>
              </div>
              <p class="meta">{step_text}</p>
              <div class="button-row">{"".join(actions)}</div>
            </article>
            """
        )

    enrollments = query(
        conn,
        """
        SELECT e.*, p.name, p.peptide_name
        FROM enrollments e JOIN protocols p ON p.id = e.protocol_id
        WHERE e.user_id = ?
        ORDER BY e.created_at DESC
        """,
        (ctx.user["id"],),
    )
    enrollment_html = "".join(
        f"""
        <article class="item">
          <div class="item-title"><h3>{h(row['name'])}</h3><span class="badge">{h(row['status'])}</span></div>
          <p class="meta">{h(row['peptide_name'])} · start {h(row['start_date'])} · day {protocol_day(row['start_date'])}</p>
          <div class="button-row">
            <form class="inline-form" method="post" action="/enrollments/status">
              <input type="hidden" name="enrollment_id" value="{row['id']}">
              <input type="hidden" name="status" value="paused">
              <button class="secondary" type="submit">Pause</button>
            </form>
            <form class="inline-form" method="post" action="/enrollments/status">
              <input type="hidden" name="enrollment_id" value="{row['id']}">
              <input type="hidden" name="status" value="active">
              <button class="secondary" type="submit">Resume</button>
            </form>
            <form class="inline-form" method="post" action="/enrollments/status">
              <input type="hidden" name="enrollment_id" value="{row['id']}">
              <input type="hidden" name="status" value="stopped">
              <button class="danger" type="submit">Stop</button>
            </form>
          </div>
        </article>
        """
        for row in enrollments
    ) or '<div class="empty">No active protocols yet.</div>'

    body = f"""
    {editor}
    <section class="panel">
      <div class="panel-head"><h2>Published and draft protocols</h2></div>
      <div class="card-list">{"".join(cards) if cards else '<div class="empty">No protocols yet.</div>'}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>My protocols</h2></div>
      <div class="card-list">{enrollment_html}</div>
    </section>
    """
    return layout(ctx, "/protocols", "Protocols", body)


def protocol_editor(conn: sqlite3.Connection, protocol: sqlite3.Row | None) -> str:
    title = "Edit protocol" if protocol else "Add protocol"
    protocol_id = protocol["id"] if protocol else ""
    name = protocol["name"] if protocol else ""
    peptide = protocol["peptide_name"] if protocol else ""
    description = protocol["description"] if protocol else ""
    step_protocol_id = protocol["id"] if protocol else ""
    return f"""
    <section class="panel">
      <div class="panel-head"><h2>{title}</h2></div>
      <form method="post" action="/protocols/save" class="stack">
        <input type="hidden" name="protocol_id" value="{protocol_id}">
        <div class="grid two">
          <label>Name <input name="name" value="{h(name)}" placeholder="GHK-Cu 60-day ramp" required></label>
          <label>Peptide
            <select name="peptide_name">
              {peptide_select(conn, peptide)}
            </select>
          </label>
        </div>
        <label>Other peptide name <input name="peptide_name_other" placeholder="Only needed if not in the dropdown"></label>
        <label>Description <textarea name="description" placeholder="Plain-language schedule summary">{h(description)}</textarea></label>
        <div class="button-row">
          <button type="submit">Save protocol</button>
          <a class="button secondary" href="/protocols">Clear</a>
        </div>
      </form>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Add dose step</h2></div>
      <form method="post" action="/steps/save" class="stack">
        <input type="hidden" name="protocol_id" value="{step_protocol_id}">
        <div class="grid three">
          <label>Start day <input name="start_day" inputmode="numeric" value="1" required></label>
          <label>End day <input name="end_day" inputmode="numeric" value="15" required></label>
          <label>Dose <input name="dose_amount" inputmode="decimal" value="1" placeholder="1 mg or 400 mcg" required></label>
        </div>
        {step_cadence_fields("daily", 7, "")}
        <label>Instructions <input name="instructions" placeholder="Phase 1"></label>
        <div class="button-row">
          <button type="submit" {'disabled' if not protocol else ''}>Add step</button>
        </div>
      </form>
      {step_list(protocol['id']) if protocol else '<p class="meta">Save the protocol before adding steps.</p>'}
    </section>
    """


def step_list(protocol_id: int) -> str:
    with db() as conn:
        steps = query(conn, "SELECT * FROM protocol_steps WHERE protocol_id = ? ORDER BY sort_order, start_day", (protocol_id,))
    if not steps:
        return '<div class="empty">No steps yet.</div>'
    return '<div class="card-list">' + "".join(
        f"""
        <article class="item">
          <form method="post" action="/steps/save" class="stack">
            <input type="hidden" name="step_id" value="{step['id']}">
            <input type="hidden" name="protocol_id" value="{protocol_id}">
            <div class="grid three">
              <label>Start <input name="start_day" value="{step['start_day']}" required></label>
              <label>End <input name="end_day" value="{step['end_day']}" required></label>
              <label>Dose <input name="dose_amount" value="{format_dose(step['dose_amount'])}" placeholder="1 mg or 400 mcg" required></label>
            </div>
            {step_cadence_fields(step['cadence_type'], step['interval_days'], step['weekdays'])}
            <label>Instructions <input name="instructions" value="{h(step['instructions'])}"></label>
            <div class="button-row">
              <button class="secondary" type="submit">Save step</button>
            </div>
          </form>
          <form class="inline-form" method="post" action="/steps/delete" onsubmit="return confirm('Delete this step?');">
            <input type="hidden" name="step_id" value="{step['id']}">
            <input type="hidden" name="protocol_id" value="{protocol_id}">
            <button class="danger" type="submit">Delete</button>
          </form>
        </article>
        """
        for step in steps
    ) + "</div>"


def render_log(ctx: RequestContext, conn: sqlite3.Connection, params: dict[str, list[str]]) -> bytes:
    assert ctx.user
    colors = peptide_colors(conn)
    selected_peptide = params.get("peptide", [""])[0].strip()
    edit_id = int_or_default(params.get("edit", ["0"])[0], 0)
    selected_user_id = ctx.user["id"]
    if ctx.user["role"] == "admin":
        requested_user_id = int_or_default(params.get("user_id", [str(ctx.user["id"])])[0], ctx.user["id"])
        requested_user = one(conn, "SELECT id FROM users WHERE id = ? AND active = 1", (requested_user_id,))
        if requested_user:
            selected_user_id = requested_user["id"]
    return_to = current_path("/log", params, {"edit"})
    filter_options = ['<option value="">All peptides</option>']
    peptide_names = [row["name"] for row in query(conn, "SELECT name FROM peptides ORDER BY name")]
    if selected_peptide and selected_peptide not in peptide_names:
        filter_options.append(f'<option value="{h(selected_peptide)}" selected>{h(selected_peptide)}</option>')
    for name in peptide_names:
        filter_options.append(f'<option value="{h(name)}" {"selected" if name == selected_peptide else ""}>{h(name)}</option>')

    where = "WHERE d.user_id = ?"
    args: list[Any] = [selected_user_id]
    if selected_peptide:
        where += " AND d.peptide_name = ?"
        args.append(selected_peptide)
    logs = query(
        conn,
        f"SELECT d.*, u.display_name AS owner_name FROM dose_logs d JOIN users u ON u.id = d.user_id {where} ORDER BY d.logged_at DESC LIMIT 100",
        tuple(args),
    )
    log_html = "".join(
        log_edit_card(conn, row, colors, return_to, ctx.user) if row["id"] == edit_id else log_summary_card(row, colors, return_to, ctx.user)
        for row in logs
    ) or '<div class="empty">No dose logs match this filter.</div>'
    user_filter = ""
    if ctx.user["role"] == "admin":
        user_options = []
        for user in query(conn, "SELECT id, display_name, email FROM users WHERE active = 1 ORDER BY display_name, email"):
            selected = " selected" if int(user["id"]) == int(selected_user_id) else ""
            user_options.append(f'<option value="{user["id"]}"{selected}>{h(user["display_name"])} ({h(user["email"])})</option>')
        user_filter = f'<label>User <select name="user_id">{"".join(user_options)}</select></label>'
    body = f"""
    <section class="panel">
      <div class="panel-head"><h2>Manual dose</h2></div>
      {log_form(conn, actor_user=ctx.user, target_user_id=selected_user_id, return_to=return_to, button_label="Log manual dose")}
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Dose history</h2></div>
      <form method="get" action="/log" class="filter-bar">
        {user_filter}
        <label>Filter by peptide
          <select name="peptide">
            {"".join(filter_options)}
          </select>
        </label>
        <div class="button-row compact">
          <button class="secondary" type="submit">Apply</button>
          <a class="button secondary" href="/log">Clear</a>
        </div>
      </form>
      <div class="card-list">{log_html}</div>
    </section>
    """
    return layout(ctx, "/log", "Log", body)


def log_summary_card(row: sqlite3.Row, colors: dict[str, str], return_to: str, actor_user: sqlite3.Row) -> str:
    parsed = urllib.parse.urlparse(return_to)
    params = urllib.parse.parse_qs(parsed.query)
    edit_params = {"edit": row["id"]}
    if params.get("peptide", [""])[0]:
        edit_params["peptide"] = params["peptide"][0]
    if params.get("user_id", [""])[0]:
        edit_params["user_id"] = params["user_id"][0]
    edit_href = f"/log?{urllib.parse.urlencode(edit_params)}"
    return f"""
    <article class="item">
      <div class="item-title"><h3>{peptide_chip(row['peptide_name'], color_for_peptide(row['peptide_name'], colors))}</h3><span class="badge">{h(row['source'])}</span></div>
      <p class="meta">{h(row['logged_at'])} · {format_dose(row['actual_dose_amount'])} {h(row['dose_unit'])}</p>
      {f'<p class="meta">Logged for {h(row["owner_name"])}</p>' if actor_user['role'] == 'admin' else ''}
      <p class="meta">{'protocol day ' + str(row['protocol_day']) if row['protocol_day'] else ''} {h(row['site'])}</p>
      <p>{h(row['notes'])}</p>
      <div class="button-row compact">
        <a class="button secondary" href="{h(edit_href)}">Edit</a>
        <form class="inline-form" method="post" action="/logs/delete" onsubmit="return confirm('Delete this dose log?');">
          <input type="hidden" name="log_id" value="{row['id']}">
          <input type="hidden" name="return_to" value="{h(return_to)}">
          <button class="danger" type="submit">Delete</button>
        </form>
      </div>
    </article>
    """


def log_edit_card(conn: sqlite3.Connection, row: sqlite3.Row, colors: dict[str, str], return_to: str, actor_user: sqlite3.Row) -> str:
    return f"""
    <article class="item edit-item">
      <div class="item-title"><h3>Edit {peptide_chip(row['peptide_name'], color_for_peptide(row['peptide_name'], colors))}</h3><span class="badge">{h(row['source'])}</span></div>
      {log_form(conn, row, actor_user=actor_user, target_user_id=row['user_id'], return_to=return_to, button_label="Save dose")}
      <div class="button-row compact">
        <a class="button secondary" href="{h(return_to)}">Cancel</a>
        <form class="inline-form" method="post" action="/logs/delete" onsubmit="return confirm('Delete this dose log?');">
          <input type="hidden" name="log_id" value="{row['id']}">
          <input type="hidden" name="return_to" value="{h(return_to)}">
          <button class="danger" type="submit">Delete</button>
        </form>
      </div>
    </article>
    """


def render_calendar(ctx: RequestContext, conn: sqlite3.Connection, params: dict[str, list[str]]) -> bytes:
    assert ctx.user
    colors = peptide_colors(conn)
    selected_user_id = int(ctx.user["id"])
    selected_user = ctx.user
    user_query: dict[str, str] = {}
    if ctx.user["role"] == "admin":
        requested_user_id = int_or_default(params.get("user_id", [str(ctx.user["id"])])[0], ctx.user["id"])
        requested_user = one(conn, "SELECT id, display_name, email FROM users WHERE id = ? AND active = 1", (requested_user_id,))
        if requested_user:
            selected_user_id = int(requested_user["id"])
            selected_user = requested_user
        user_query = {"user_id": str(selected_user_id)}
    month_start = month_from_params(params)
    next_month = add_months(month_start, 1)
    prev_month = add_months(month_start, -1)
    month_end = next_month - timedelta(days=1)
    selected_date = date_from_param(params.get("date", [""])[0], today_date())
    if selected_date < month_start or selected_date > month_end:
        selected_date = month_start
    selected_iso = selected_date.isoformat()
    edit_id = int_or_default(params.get("edit", ["0"])[0], 0)
    calendar_return_params = {"month": month_start.strftime("%Y-%m"), "date": selected_iso, **user_query}
    calendar_return = f"/calendar?{urllib.parse.urlencode(calendar_return_params)}"
    rows = query(
        conn,
        """
        SELECT id, substr(logged_at, 1, 10) AS log_date, peptide_name, actual_dose_amount, dose_unit, logged_at
        FROM dose_logs
        WHERE user_id = ? AND substr(logged_at, 1, 10) >= ? AND substr(logged_at, 1, 10) <= ?
        ORDER BY log_date, logged_at, id
        """,
        (selected_user_id, month_start.isoformat(), month_end.isoformat()),
    )
    by_day: dict[str, list[sqlite3.Row]] = {}
    seen_peptides: dict[str, str] = {}
    for row in rows:
        by_day.setdefault(row["log_date"], []).append(row)
        seen_peptides[row["peptide_name"]] = color_for_peptide(row["peptide_name"], colors)

    weeks = calendar_lib.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month)
    weekday_header = "".join(f'<div class="calendar-weekday">{label}</div>' for _code, label in WEEKDAYS)
    day_cells: list[str] = []
    for week in weeks:
        for day in week:
            iso = day.isoformat()
            day_rows = by_day.get(iso, [])
            outside = " outside" if day.month != month_start.month else ""
            today_class = " today" if iso == today_iso() else ""
            selected_class = " selected" if iso == selected_iso else ""
            day_href = f"/calendar?{urllib.parse.urlencode({'month': month_start.strftime('%Y-%m'), 'date': iso, **user_query})}"
            chips = "".join(
                f"""
                <a class="calendar-dose" href="{h(day_href)}" title="{h(row['peptide_name'])}" aria-label="{h(row['peptide_name'])} {format_dose(row['actual_dose_amount'])} {h(row['dose_unit'])}" style="--peptide-color: {h(color_for_peptide(row['peptide_name'], colors))}">
                  <span class="calendar-dose-line">
                    <span class="calendar-dose-name">{h(peptide_short_code(row['peptide_name']))}</span>
                    <strong>{format_dose(row['actual_dose_amount'])} {h(row['dose_unit'])}</strong>
                  </span>
                </a>
                """
                for row in day_rows
            )
            day_cells.append(
                f"""
                <div class="calendar-day{outside}{today_class}{selected_class}">
                  <a class="calendar-date" href="{h(day_href)}">{day.day}</a>
                  <div class="calendar-doses">{chips}</div>
                </div>
                """
            )
    legend = "".join(
        peptide_chip(name, color)
        for name, color in sorted(seen_peptides.items(), key=lambda item: item[0].lower())
    ) or '<p class="meta">No doses logged this month.</p>'
    selected_logs = query(
        conn,
        """
        SELECT * FROM dose_logs
        WHERE user_id = ? AND substr(logged_at, 1, 10) = ?
        ORDER BY logged_at DESC, id DESC
        """,
        (selected_user_id, selected_iso),
    )
    previous_date = selected_date - timedelta(days=1)
    previous_counts = one(
        conn,
        """
        SELECT
          sum(CASE WHEN substr(logged_at, 12, 5) < '12:00' THEN 1 ELSE 0 END) AS am_count,
          sum(CASE WHEN substr(logged_at, 12, 5) >= '12:00' THEN 1 ELSE 0 END) AS pm_count
        FROM dose_logs
        WHERE user_id = ? AND substr(logged_at, 1, 10) = ?
        """,
        (selected_user_id, previous_date.isoformat()),
    )
    am_count = int(previous_counts["am_count"] or 0)
    pm_count = int(previous_counts["pm_count"] or 0)
    selected_log_html = "".join(
        calendar_log_edit_card(conn, row, colors, calendar_return, month_start, selected_iso)
        if row["id"] == edit_id
        else calendar_log_summary_card(row, colors, calendar_return, month_start, selected_iso)
        for row in selected_logs
    ) or '<div class="empty">No doses logged for this day.</div>'
    month_label = month_start.strftime("%B %Y")
    selected_label = selected_date.strftime("%A, %B %-d, %Y") if sys.platform != "win32" else selected_date.strftime("%A, %B %#d, %Y")
    user_filter = ""
    selected_user_label = selected_user["display_name"] if selected_user else ctx.user["display_name"]
    if ctx.user["role"] == "admin":
        user_options = []
        for user in query(conn, "SELECT id, display_name, email FROM users WHERE active = 1 ORDER BY display_name, email"):
            selected = " selected" if int(user["id"]) == int(selected_user_id) else ""
            user_options.append(f'<option value="{user["id"]}"{selected}>{h(user["display_name"])} ({h(user["email"])})</option>')
        user_filter = f"""
        <form method="get" action="/calendar" class="calendar-user-filter">
          <input type="hidden" name="month" value="{month_start.strftime('%Y-%m')}">
          <input type="hidden" name="date" value="{selected_iso}">
          <label>Calendar for <select name="user_id">{"".join(user_options)}</select></label>
          <button class="secondary" type="submit">Show</button>
        </form>
        """
    prev_href = f"/calendar?{urllib.parse.urlencode({'month': prev_month.strftime('%Y-%m'), **user_query})}"
    today_href = f"/calendar?{urllib.parse.urlencode({'month': today_date().strftime('%Y-%m'), 'date': today_iso(), **user_query})}"
    next_href = f"/calendar?{urllib.parse.urlencode({'month': next_month.strftime('%Y-%m'), **user_query})}"
    body = f"""
    <section class="panel">
      <div class="panel-head">
        <div><p class="eyebrow">Dose calendar</p><h2>{h(month_label)}</h2><p class="meta">Showing {h(selected_user_label)}</p></div>
        <div class="button-row compact">
          <a class="button secondary" href="{h(prev_href)}">Previous</a>
          <a class="button secondary" href="{h(today_href)}">Today</a>
          <a class="button secondary" href="{h(next_href)}">Next</a>
        </div>
      </div>
      {user_filter}
      <div class="calendar-legend">{legend}</div>
      <div class="calendar-grid">
        {weekday_header}
        {"".join(day_cells)}
      </div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <h2>{h(selected_label)}</h2>
        <div class="button-row compact">
          <form method="post" action="/logs/copy-previous-day" onsubmit="return confirm('Copy {am_count} morning dose{'s' if am_count != 1 else ''} from the previous day at 8:00 AM?');">
            <input type="hidden" name="target_date" value="{selected_iso}">
            <input type="hidden" name="copy_period" value="am">
            <input type="hidden" name="target_user_id" value="{selected_user_id}">
            <input type="hidden" name="return_to" value="{h(calendar_return)}">
            <button class="secondary" type="submit" {'disabled' if am_count == 0 else ''}>Previous day AM</button>
          </form>
          <form method="post" action="/logs/copy-previous-day" onsubmit="return confirm('Copy {pm_count} evening dose{'s' if pm_count != 1 else ''} from the previous day at 8:00 PM?');">
            <input type="hidden" name="target_date" value="{selected_iso}">
            <input type="hidden" name="copy_period" value="pm">
            <input type="hidden" name="target_user_id" value="{selected_user_id}">
            <input type="hidden" name="return_to" value="{h(calendar_return)}">
            <button class="secondary" type="submit" {'disabled' if pm_count == 0 else ''}>Previous day PM</button>
          </form>
        </div>
      </div>
      <h3>Add dose</h3>
      {log_form(conn, actor_user=ctx.user, target_user_id=selected_user_id, return_to=calendar_return, default_logged_at=datetime_local_for_date(selected_date), button_label="Add dose")}
      <div class="divider"></div>
      <div class="panel-head"><h2>Logged this day</h2></div>
      <div class="card-list">{selected_log_html}</div>
    </section>
    """
    return layout(ctx, "/calendar", "Calendar", body)


def calendar_log_summary_card(row: sqlite3.Row, colors: dict[str, str], return_to: str, month_start: date, selected_iso: str) -> str:
    params = urllib.parse.parse_qs(urllib.parse.urlparse(return_to).query)
    edit_params = {"month": month_start.strftime("%Y-%m"), "date": selected_iso, "edit": row["id"]}
    if params.get("user_id", [""])[0]:
        edit_params["user_id"] = params["user_id"][0]
    edit_href = f"/calendar?{urllib.parse.urlencode(edit_params)}"
    return f"""
    <article class="item">
      <div class="item-title"><h3>{peptide_chip(row['peptide_name'], color_for_peptide(row['peptide_name'], colors))}</h3><span class="badge">{h(row['source'])}</span></div>
      <p class="meta">{h(row['logged_at'])} · {format_dose(row['actual_dose_amount'])} {h(row['dose_unit'])} · {h(row['site'])}</p>
      <p>{h(row['notes'])}</p>
      <div class="button-row compact">
        <a class="button secondary" href="{h(edit_href)}">Edit</a>
        <form class="inline-form" method="post" action="/logs/delete" onsubmit="return confirm('Delete this dose log?');">
          <input type="hidden" name="log_id" value="{row['id']}">
          <input type="hidden" name="return_to" value="{h(return_to)}">
          <button class="danger" type="submit">Delete</button>
        </form>
      </div>
    </article>
    """


def calendar_log_edit_card(conn: sqlite3.Connection, row: sqlite3.Row, colors: dict[str, str], return_to: str, month_start: date, selected_iso: str) -> str:
    return f"""
    <article class="item edit-item">
      <div class="item-title"><h3>Edit {peptide_chip(row['peptide_name'], color_for_peptide(row['peptide_name'], colors))}</h3><span class="badge">{h(row['source'])}</span></div>
      {log_form(conn, row, return_to=return_to, button_label="Save dose")}
      <div class="button-row compact">
        <a class="button secondary" href="{h(return_to)}">Cancel</a>
        <form class="inline-form" method="post" action="/logs/delete" onsubmit="return confirm('Delete this dose log?');">
          <input type="hidden" name="log_id" value="{row['id']}">
          <input type="hidden" name="return_to" value="{h(return_to)}">
          <button class="danger" type="submit">Delete</button>
        </form>
      </div>
    </article>
    """


def render_admin(ctx: RequestContext, conn: sqlite3.Connection) -> bytes:
    assert ctx.user
    if ctx.user["role"] != "admin":
        return layout(ctx, "/admin", "Admin", '<section class="panel"><div class="empty">Admin access required.</div></section>')
    users = query(conn, "SELECT * FROM users ORDER BY role, email")
    peptides = query(conn, "SELECT * FROM peptides ORDER BY name")
    user_html = "".join(
        f"""
        <article class="item">
          <div class="item-title">
            <div><h3>{h(row['display_name'])}</h3><p class="meta">{h(row['email'])}</p></div>
            <span class="badge {'red' if not row['active'] else ''}">{h(row['role'])} · {'active' if row['active'] else 'disabled'}</span>
          </div>
          <form method="post" action="/admin/user-status" class="button-row">
            <input type="hidden" name="user_id" value="{row['id']}">
            <input type="hidden" name="active" value="{0 if row['active'] else 1}">
            <button class="secondary" type="submit">{'Disable' if row['active'] else 'Enable'}</button>
          </form>
        </article>
        """
        for row in users
    )
    peptide_html = "".join(
        f"""
        <article class="item">
          <div class="item-title">
            <div><h3>{peptide_chip(row['name'], row['color'])}</h3><p class="meta">{h(row['notes'])}</p></div>
          </div>
          <form method="post" action="/admin/peptides/update" class="stack">
            <input type="hidden" name="peptide_id" value="{row['id']}">
            <div class="grid three">
              <label>Name <input name="name" value="{h(row['name'])}" required></label>
              <label>Color <input name="color" type="color" value="{h(normalize_color(row['color'], default_color_for_peptide(row['name'])))}"></label>
              <label>Notes <input name="notes" value="{h(row['notes'])}" placeholder="optional"></label>
            </div>
            <div class="button-row"><button class="secondary" type="submit">Save peptide</button></div>
          </form>
          <form method="post" action="/admin/peptides/delete" onsubmit="return confirm('Delete this peptide from the dropdown? Existing logs and protocols keep their text.');">
            <input type="hidden" name="peptide_id" value="{row['id']}">
            <button class="danger" type="submit">Delete</button>
          </form>
        </article>
        """
        for row in peptides
    ) or '<div class="empty">No peptides yet.</div>'
    body = f"""
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>Backup</h2>
          <p class="meta">Download a consistent SQLite snapshot of all app data.</p>
        </div>
      </div>
      <div class="button-row"><a class="button" href="/backup">Export backup file</a></div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Peptides</h2></div>
      <form method="post" action="/admin/peptides" class="stack">
        <div class="grid three">
          <label>Name <input name="name" placeholder="BPC-157" required></label>
          <label>Color <input name="color" type="color" value="#60706a"></label>
          <label>Notes <input name="notes" placeholder="optional"></label>
        </div>
        <div class="button-row"><button type="submit">Add peptide</button></div>
      </form>
      <div class="card-list">{peptide_html}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Add user</h2></div>
      <form method="post" action="/admin/users" class="stack">
        <div class="grid two">
          <label>Email <input name="email" type="email" required></label>
          <label>Display name <input name="display_name" required></label>
          <label>Password <input name="password" type="password" required></label>
          <label>Role
            <select name="role">
              <option value="member">member</option>
              <option value="admin">admin</option>
            </select>
          </label>
        </div>
        <div class="button-row"><button type="submit">Create user</button></div>
      </form>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Users</h2></div>
      <div class="card-list">{user_html}</div>
    </section>
    """
    return layout(ctx, "/admin", "Admin", body)


def render_settings(ctx: RequestContext, conn: sqlite3.Connection) -> bytes:
    assert ctx.user
    counts = {
        "protocols": one(conn, "SELECT count(*) c FROM protocols")["c"],
        "enrollments": one(conn, "SELECT count(*) c FROM enrollments WHERE user_id = ?", (ctx.user["id"],))["c"],
        "logs": one(conn, "SELECT count(*) c FROM dose_logs WHERE user_id = ?", (ctx.user["id"],))["c"],
        "checkins": one(conn, "SELECT count(*) c FROM daily_checkins WHERE user_id = ?", (ctx.user["id"],))["c"],
    }
    audit_where = ""
    audit_args: tuple[Any, ...] = ()
    if ctx.user["role"] != "admin":
        audit_where = "WHERE e.user_id = ?"
        audit_args = (ctx.user["id"],)
    audit_rows = query(
        conn,
        f"""
        SELECT e.*, u.display_name, actor.display_name AS actor_name
        FROM dose_audit_events e
        LEFT JOIN users u ON u.id = e.user_id
        LEFT JOIN users actor ON actor.id = e.actor_user_id
        {audit_where}
        ORDER BY e.created_at DESC, e.id DESC
        LIMIT 50
        """,
        audit_args,
    )
    def audit_card(row: sqlite3.Row) -> str:
        error_html = f'<p class="meta">Error: {h(row["error"])}</p>' if row["error"] else ""
        return f"""
        <article class="item">
          <div class="item-title">
            <div>
              <h3>{h(row['event_type'].title())}: {h(row['action'].replace('_', ' '))}</h3>
              <p class="meta">{h(row['created_at'])} · for {h(row['display_name'] or 'Unknown user')} · entered by {h(row['actor_name'] or row['display_name'] or 'Unknown user')} · {h(row['client_ip'] or 'unknown client')}</p>
            </div>
            <span class="badge {'warn' if row['event_type'] == 'error' else ''}">{h(row['event_type'])}</span>
          </div>
          <p class="meta">
            {h(row['peptide_name'] or 'No peptide')} · {format_dose(row['actual_dose_amount']) if row['actual_dose_amount'] is not None else 'n/a'} {h(row['dose_unit'] or 'mg')}
            · logged_at {h(row['logged_at'] or 'n/a')} · log id {h(row['log_id'] or 'n/a')}
          </p>
          <p class="meta">{h(row['path'])} → {h(row['return_to'] or 'n/a')}</p>
          {error_html}
          <p class="meta">{h((row['user_agent'] or '')[:180])}</p>
        </article>
        """

    audit_html = "".join(audit_card(row) for row in audit_rows) or '<div class="empty">No dose audit events yet. New dose attempts, saves, errors, and deletes will appear here.</div>'
    body = f"""
    <section class="panel">
      <div class="panel-head"><h2>Data</h2></div>
      <div class="card-list">
        <article class="item"><h3>{counts['protocols']} protocols</h3><p class="meta">Published, draft, and retired definitions.</p></article>
        <article class="item"><h3>{counts['enrollments']} enrollments</h3><p class="meta">Your active and historical protocol activations.</p></article>
        <article class="item"><h3>{counts['logs']} dose logs</h3><p class="meta">Your tracked protocol and manual doses.</p></article>
        <article class="item"><h3>{counts['checkins']} check-ins</h3><p class="meta">Daily symptom and note entries.</p></article>
      </div>
    </section>
    <section class="panel">
      <h2>Install on iPhone</h2>
      <p class="meta">Open this site in Safari, tap Share, then Add to Home Screen.</p>
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>Recent Dose Audit</h2>
          <p class="meta">Newest 50 dose attempts, saves, deletes, and errors. Admins see all users.</p>
        </div>
      </div>
      <div class="card-list">{audit_html}</div>
    </section>
    <section class="panel">
      <h2>About</h2>
      <p class="meta">App version {h(APP_VERSION)}</p>
    </section>
    """
    return layout(ctx, "/settings", "Settings", body)


class App(BaseHTTPRequestHandler):
    server_version = "PeptidePowerAssistant/0.1"

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/", "/login", "/library", "/healthz"}:
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_GET(self) -> None:
        try:
            self.route_get()
        except Exception as exc:
            self.error_response(exc)

    def do_POST(self) -> None:
        try:
            self.route_post()
        except Exception as exc:
            self.error_response(exc)

    def route_get(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/healthz":
            return self.text("ok")
        if parsed.path.startswith("/static/"):
            return self.serve_static(STATIC_DIR / parsed.path.removeprefix("/static/"))
        if parsed.path == "/service-worker.js":
            return self.serve_static(STATIC_DIR / "service-worker.js")
        if parsed.path == "/login":
            return self.html(login_page())
        if parsed.path == "/library":
            return self.redirect(self.protocol_library_location())

        ctx = self.context()
        if not ctx.user:
            return self.redirect("/login")

        if parsed.path == "/backup":
            self.require_admin(ctx)
            return self.download_backup()

        params = urllib.parse.parse_qs(parsed.query)
        with db() as conn:
            if parsed.path == "/":
                return self.html(render_today(ctx, conn))
            if parsed.path == "/protocols":
                return self.html(render_protocols(ctx, conn, params))
            if parsed.path == "/log":
                return self.html(render_log(ctx, conn, params))
            if parsed.path == "/calendar":
                return self.html(render_calendar(ctx, conn, params))
            if parsed.path == "/admin":
                return self.html(render_admin(ctx, conn))
            if parsed.path == "/settings":
                return self.html(render_settings(ctx, conn))
        self.not_found()

    def route_post(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        data = self.form_data()
        if parsed.path == "/login":
            return self.login(data)
        if parsed.path == "/logout":
            return self.logout()

        dose_paths = {"/log/protocol", "/logs/save", "/logs/delete", "/logs/copy-previous-day", "/log/manual"}
        ctx = self.context()
        if not ctx.user:
            if parsed.path in dose_paths:
                self.record_dose_audit("error", "unauthenticated", None, None, parsed.path, data, error="No active session; redirected to login.")
            return self.redirect("/login")

        if parsed.path in dose_paths:
            return self.handle_dose_post(parsed.path, ctx.user, data)

        with db() as conn:
            if parsed.path == "/checkin":
                self.save_checkin(conn, ctx.user["id"], data)
                return self.redirect(with_flash("/", "Check-in saved"))
            if parsed.path == "/protocols/save":
                self.require_admin(ctx)
                protocol_id = self.save_protocol(conn, ctx.user["id"], data)
                return self.redirect(with_flash(f"/protocols?edit={protocol_id}", "Protocol saved"))
            if parsed.path == "/steps/save":
                self.require_admin(ctx)
                protocol_id = self.save_step(conn, data)
                return self.redirect(with_flash(f"/protocols?edit={protocol_id}", "Step saved"))
            if parsed.path == "/steps/delete":
                self.require_admin(ctx)
                protocol_id = int(data.get("protocol_id", "0"))
                conn.execute("DELETE FROM protocol_steps WHERE id = ?", (int(data.get("step_id", "0")),))
                return self.redirect(with_flash(f"/protocols?edit={protocol_id}", "Step deleted"))
            if parsed.path == "/protocols/publish":
                self.require_admin(ctx)
                protocol_id = int(data["protocol_id"])
                conn.execute(
                    "UPDATE protocols SET status = 'published', published_at = ?, updated_at = ? WHERE id = ?",
                    (now_iso(), now_iso(), protocol_id),
                )
                return self.redirect(with_flash("/protocols", "Protocol published"))
            if parsed.path == "/protocols/retire":
                self.require_admin(ctx)
                conn.execute(
                    "UPDATE protocols SET status = 'retired', updated_at = ? WHERE id = ?",
                    (now_iso(), int(data["protocol_id"])),
                )
                return self.redirect(with_flash("/protocols", "Protocol retired"))
            if parsed.path == "/protocols/delete":
                self.require_admin(ctx)
                conn.execute("DELETE FROM protocols WHERE id = ?", (int(data["protocol_id"]),))
                return self.redirect(with_flash("/protocols", "Protocol deleted"))
            if parsed.path == "/protocols/activate":
                self.activate_protocol(conn, ctx.user["id"], data)
                return self.redirect(with_flash("/protocols", "Protocol activated"))
            if parsed.path == "/enrollments/status":
                conn.execute(
                    "UPDATE enrollments SET status = ? WHERE id = ? AND user_id = ?",
                    (data["status"], int(data["enrollment_id"]), ctx.user["id"]),
                )
                return self.redirect(with_flash("/protocols", "Enrollment updated"))
            if parsed.path == "/admin/users":
                self.require_admin(ctx)
                self.create_user(conn, data)
                return self.redirect(with_flash("/admin", "User created"))
            if parsed.path == "/admin/user-status":
                self.require_admin(ctx)
                conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(data["active"]), int(data["user_id"])))
                return self.redirect(with_flash("/admin", "User updated"))
            if parsed.path == "/admin/peptides":
                self.require_admin(ctx)
                self.create_peptide(conn, data)
                return self.redirect(with_flash("/admin", "Peptide added"))
            if parsed.path == "/admin/peptides/update":
                self.require_admin(ctx)
                self.update_peptide(conn, data)
                return self.redirect(with_flash("/admin", "Peptide updated"))
            if parsed.path == "/admin/peptides/delete":
                self.require_admin(ctx)
                conn.execute("DELETE FROM peptides WHERE id = ?", (int(data["peptide_id"]),))
                return self.redirect(with_flash("/admin", "Peptide deleted"))
        self.not_found()

    def context(self) -> RequestContext:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        cookies = parse_cookies(self.headers.get("Cookie"))
        sid = cookies.get("sid", "")
        user_id = SESSIONS.get(sid)
        user = None
        if user_id:
            with db() as conn:
                user = one(conn, "SELECT * FROM users WHERE id = ? AND active = 1", (user_id,))
        return RequestContext(
            user=user,
            flash=params.get("flash", [None])[0],
            error=params.get("error", [None])[0],
        )

    def form_data(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(min(length, 1024 * 1024)).decode("utf-8")
        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
        return {key: ",".join(values) if len(values) > 1 else values[-1] for key, values in parsed.items()}

    def handle_dose_post(self, path: str, actor_user: sqlite3.Row, data: dict[str, str]) -> None:
        action = dose_action_for_path(path, data)
        actor_user_id = int(actor_user["id"])
        target_user_id = actor_user_id
        try:
            with db() as conn:
                target_user_id = self.dose_target_user_id(conn, actor_user, path, data)
            self.record_dose_audit("attempt", action, actor_user_id, target_user_id, path, data)
            with db() as conn:
                if path == "/log/protocol":
                    result = self.log_protocol(conn, actor_user_id, data)
                    redirect_to = with_flash("/", "Dose logged")
                elif path == "/logs/save":
                    result = self.save_dose_log(conn, actor_user, target_user_id, data)
                    return_to = safe_return_to(data.get("return_to"))
                    if actor_user["role"] == "admin" and not data.get("log_id") and return_to == "/log":
                        return_to = f"/log?user_id={target_user_id}"
                    redirect_to = with_flash(return_to, "Dose saved")
                elif path == "/logs/delete":
                    result = self.delete_dose_log(conn, actor_user, data)
                    redirect_to = with_flash(safe_return_to(data.get("return_to")), "Dose deleted")
                elif path == "/logs/copy-previous-day":
                    result = self.copy_previous_day(conn, target_user_id, data)
                    copied = int(result["copied_count"])
                    skipped = int(result["skipped_count"])
                    period = str(result["copy_period"]).upper()
                    message = f"Copied {copied} {period} dose{'s' if copied != 1 else ''} from the previous day"
                    if skipped:
                        message += f"; skipped {skipped} already present"
                    redirect_to = with_flash(safe_return_to(data.get("return_to"), "/calendar"), message)
                elif path == "/log/manual":
                    result = self.save_dose_log(conn, actor_user, target_user_id, data)
                    redirect_to = with_flash("/log", "Manual dose logged")
                else:
                    raise ValueError("Dose action not found.")
            self.record_dose_audit("success", action, actor_user_id, int(result.get("user_id", target_user_id)), path, data, result=result)
            return self.redirect(redirect_to)
        except Exception as exc:
            self.record_dose_audit("error", action, actor_user_id, target_user_id, path, data, error=str(exc))
            raise

    def dose_target_user_id(self, conn: sqlite3.Connection, actor_user: sqlite3.Row, path: str, data: dict[str, str]) -> int:
        actor_user_id = int(actor_user["id"])
        log_id = int_or_default(data.get("log_id"), 0)
        if log_id and path in {"/logs/save", "/logs/delete"}:
            log = one(conn, "SELECT user_id FROM dose_logs WHERE id = ?", (log_id,))
            if not log:
                raise ValueError("Dose log not found.")
            if actor_user["role"] != "admin" and int(log["user_id"]) != actor_user_id:
                raise ValueError("Dose log not found.")
            return int(log["user_id"])
        if path not in {"/logs/save", "/logs/copy-previous-day"} or actor_user["role"] != "admin":
            return actor_user_id
        requested_user_id = int_or_default(data.get("target_user_id"), actor_user_id)
        target = one(conn, "SELECT id FROM users WHERE id = ? AND active = 1", (requested_user_id,))
        if not target:
            raise ValueError("Selected user is not available.")
        return int(target["id"])

    def copy_previous_day(self, conn: sqlite3.Connection, user_id: int, data: dict[str, str]) -> dict[str, Any]:
        try:
            target_date = date.fromisoformat(data.get("target_date", ""))
        except ValueError as exc:
            raise ValueError("Choose a valid target date.") from exc
        source_date = target_date - timedelta(days=1)
        copy_period = data.get("copy_period", "").strip().lower()
        if copy_period not in {"am", "pm"}:
            raise ValueError("Choose AM or PM to copy.")
        time_filter = "substr(logged_at, 12, 5) < '12:00'" if copy_period == "am" else "substr(logged_at, 12, 5) >= '12:00'"
        target_time = "08:00:00" if copy_period == "am" else "20:00:00"
        source_rows = query(
            conn,
            f"""
            SELECT peptide_name, actual_dose_amount, dose_unit, site, notes, logged_at
            FROM dose_logs
            WHERE user_id = ? AND substr(logged_at, 1, 10) = ? AND {time_filter}
            ORDER BY logged_at, id
            """,
            (user_id, source_date.isoformat()),
        )
        copied_count = 0
        skipped_count = 0
        source_occurrences: dict[tuple[Any, ...], int] = {}
        for row in source_rows:
            logged_at = f"{target_date.isoformat()}T{target_time}"
            signature = (
                row["peptide_name"],
                row["actual_dose_amount"],
                row["dose_unit"],
                row["site"] or "",
                row["notes"] or "",
                logged_at,
            )
            source_occurrences[signature] = source_occurrences.get(signature, 0) + 1
            existing_count = one(
                conn,
                """
                SELECT count(*) AS count FROM dose_logs
                WHERE user_id = ? AND peptide_name = ? AND actual_dose_amount = ?
                  AND dose_unit = ? AND COALESCE(site, '') = ? AND COALESCE(notes, '') = ?
                  AND logged_at = ?
                """,
                (user_id, *signature),
            )["count"]
            if int(existing_count) >= source_occurrences[signature]:
                skipped_count += 1
                continue
            conn.execute(
                """
                INSERT INTO dose_logs
                  (user_id, source, peptide_name, actual_dose_amount, dose_unit, status, site, notes, logged_at)
                VALUES (?, 'manual', ?, ?, ?, 'completed', ?, ?, ?)
                """,
                (
                    user_id,
                    row["peptide_name"],
                    row["actual_dose_amount"],
                    row["dose_unit"],
                    row["site"] or "",
                    row["notes"] or "",
                    logged_at,
                ),
            )
            copied_count += 1
        return {
            "user_id": user_id,
            "logged_at": target_date.isoformat(),
            "copied_count": copied_count,
            "skipped_count": skipped_count,
            "copy_period": copy_period,
        }

    def record_dose_audit(
        self,
        event_type: str,
        action: str,
        actor_user_id: int | None,
        user_id: int | None,
        path: str,
        data: dict[str, str],
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        result = result or {}
        peptide_name = result.get("peptide_name") or peptide_name_from_form(data) or None
        amount = result.get("actual_dose_amount")
        if amount is None and data.get("actual_dose_amount"):
            try:
                amount = parse_dose_mg(data.get("actual_dose_amount", ""), "Actual dose")
            except ValueError:
                amount = None
        log_id = result.get("log_id") or int_or_default(data.get("log_id"), 0) or None
        logged_at = result.get("logged_at") or data.get("logged_at") or None
        site = result.get("site") or data.get("site", "").strip() or None
        return_to = data.get("return_to") or None
        client_ip = self.client_address[0] if self.client_address else ""
        user_agent = self.headers.get("User-Agent", "")
        try:
            with db() as audit_conn:
                audit_conn.execute(
                    """
                    INSERT INTO dose_audit_events
                      (event_type, action, actor_user_id, user_id, path, log_id, peptide_name, actual_dose_amount,
                       dose_unit, site, logged_at, return_to, client_ip, user_agent, error, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'mg', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type,
                        action,
                        actor_user_id,
                        user_id,
                        path,
                        log_id,
                        peptide_name,
                        amount,
                        site,
                        logged_at,
                        return_to,
                        client_ip,
                        user_agent,
                        error,
                        dose_audit_payload(data),
                        now_iso(),
                    ),
                )
            detail = f"{event_type} {action} actor={actor_user_id or 'none'} user={user_id or 'none'} peptide={peptide_name or 'n/a'} log_id={log_id or 'n/a'} logged_at={logged_at or 'n/a'}"
            if error:
                detail = f"{detail} error={error}"
            sys.stderr.write(f"dose_audit {detail}\n")
        except Exception as audit_exc:
            sys.stderr.write(f"dose_audit failed: {audit_exc}\n")

    def login(self, data: dict[str, str]) -> None:
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        with db() as conn:
            user = one(conn, "SELECT * FROM users WHERE email = ? AND active = 1", (email,))
        if not user or not verify_password(password, user["password_hash"]):
            return self.html(login_page("Invalid email or password."), HTTPStatus.UNAUTHORIZED)
        sid = secrets.token_urlsafe(32)
        SESSIONS[sid] = user["id"]
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie", f"sid={sid}; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def logout(self) -> None:
        cookies = parse_cookies(self.headers.get("Cookie"))
        sid = cookies.get("sid")
        if sid:
            SESSIONS.pop(sid, None)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", "sid=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")
        self.end_headers()

    def save_checkin(self, conn: sqlite3.Connection, user_id: int, data: dict[str, str]) -> None:
        conn.execute(
            """
            INSERT INTO daily_checkins
              (user_id, entry_date, appetite, energy, mental_acuity, mood, sleep_quality, side_effects, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, entry_date) DO UPDATE SET
              appetite = excluded.appetite,
              energy = excluded.energy,
              mental_acuity = excluded.mental_acuity,
              mood = excluded.mood,
              sleep_quality = excluded.sleep_quality,
              side_effects = excluded.side_effects,
              notes = excluded.notes
            """,
            (
                user_id,
                today_iso(),
                data.get("appetite", "same"),
                data.get("energy", "same"),
                data.get("mental_acuity", "same"),
                data.get("mood", "same"),
                data.get("sleep_quality", "ok"),
                data.get("side_effects", "").strip(),
                data.get("notes", "").strip(),
                now_iso(),
            ),
        )

    def log_protocol(self, conn: sqlite3.Connection, user_id: int, data: dict[str, str]) -> dict[str, Any]:
        enrollment = one(
            conn,
            """
            SELECT e.*, p.peptide_name
            FROM enrollments e JOIN protocols p ON p.id = e.protocol_id
            WHERE e.id = ? AND e.user_id = ?
            """,
            (int(data["enrollment_id"]), user_id),
        )
        step = one(conn, "SELECT * FROM protocol_steps WHERE id = ?", (int(data["step_id"]),))
        if not enrollment or not step:
            raise ValueError("Enrollment or step not found.")
        logged_at = now_iso()
        actual_dose = parse_dose_mg(data["actual_dose_amount"], "Actual dose")
        cursor = conn.execute(
            """
            INSERT INTO dose_logs
              (user_id, enrollment_id, protocol_id, protocol_step_id, protocol_day, source, peptide_name,
               scheduled_dose_amount, actual_dose_amount, dose_unit, status, site, notes, logged_at)
            VALUES (?, ?, ?, ?, ?, 'protocol', ?, ?, ?, 'mg', 'completed', ?, ?, ?)
            """,
            (
                user_id,
                enrollment["id"],
                enrollment["protocol_id"],
                step["id"],
                int(data["protocol_day"]),
                enrollment["peptide_name"],
                step["dose_amount"],
                actual_dose,
                data.get("site", "").strip(),
                data.get("notes", "").strip(),
                logged_at,
            ),
        )
        return {
            "log_id": cursor.lastrowid,
            "user_id": user_id,
            "peptide_name": enrollment["peptide_name"],
            "actual_dose_amount": actual_dose,
            "site": data.get("site", "").strip(),
            "logged_at": logged_at,
        }

    def save_dose_log(self, conn: sqlite3.Connection, actor_user: sqlite3.Row, target_user_id: int, data: dict[str, str]) -> dict[str, Any]:
        peptide_name = peptide_name_from_form(data)
        if not peptide_name:
            raise ValueError("Choose or enter a peptide name.")
        logged_at = submitted_datetime_or_now(data.get("logged_at"))
        log_id = int(data.get("log_id") or 0)
        actual_dose = parse_dose_mg(data["actual_dose_amount"], "Actual dose")
        values = (
            peptide_name,
            actual_dose,
            data.get("site", "").strip(),
            data.get("notes", "").strip(),
            logged_at,
        )
        if log_id:
            existing = one(conn, "SELECT user_id FROM dose_logs WHERE id = ?", (log_id,))
            if not existing or (actor_user["role"] != "admin" and int(existing["user_id"]) != int(actor_user["id"])):
                raise ValueError("Dose log not found.")
            target_user_id = int(existing["user_id"])
            cursor = conn.execute(
                """
                UPDATE dose_logs
                SET peptide_name = ?, actual_dose_amount = ?, site = ?, notes = ?, logged_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (*values, log_id, target_user_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Dose log not found.")
            return {
                "log_id": log_id,
                "user_id": target_user_id,
                "peptide_name": peptide_name,
                "actual_dose_amount": actual_dose,
                "site": data.get("site", "").strip(),
                "logged_at": logged_at,
            }
        cursor = conn.execute(
            """
            INSERT INTO dose_logs
              (user_id, source, peptide_name, actual_dose_amount, dose_unit, status, site, notes, logged_at)
            VALUES (?, 'manual', ?, ?, 'mg', 'completed', ?, ?, ?)
            """,
            (
                target_user_id,
                peptide_name,
                parse_dose_mg(data["actual_dose_amount"], "Actual dose"),
                data.get("site", "").strip(),
                data.get("notes", "").strip(),
                logged_at,
            ),
        )
        return {
            "log_id": cursor.lastrowid,
            "user_id": target_user_id,
            "peptide_name": peptide_name,
            "actual_dose_amount": actual_dose,
            "site": data.get("site", "").strip(),
            "logged_at": logged_at,
        }

    def delete_dose_log(self, conn: sqlite3.Connection, actor_user: sqlite3.Row, data: dict[str, str]) -> dict[str, Any]:
        log_id = int(data.get("log_id") or 0)
        if not log_id:
            raise ValueError("Dose log not found.")
        row = one(conn, "SELECT * FROM dose_logs WHERE id = ?", (log_id,))
        if row and actor_user["role"] != "admin" and int(row["user_id"]) != int(actor_user["id"]):
            row = None
        if not row:
            raise ValueError("Dose log not found.")
        cursor = conn.execute("DELETE FROM dose_logs WHERE id = ? AND user_id = ?", (log_id, row["user_id"]))
        if cursor.rowcount != 1:
            raise ValueError("Dose log not found.")
        return {
            "log_id": log_id,
            "user_id": row["user_id"],
            "peptide_name": row["peptide_name"],
            "actual_dose_amount": row["actual_dose_amount"],
            "site": row["site"],
            "logged_at": row["logged_at"],
        }

    def log_manual(self, conn: sqlite3.Connection, user_id: int, data: dict[str, str]) -> None:
        user = one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
        if not user:
            raise ValueError("User not found.")
        self.save_dose_log(conn, user, user_id, data)

    def save_protocol(self, conn: sqlite3.Connection, user_id: int, data: dict[str, str]) -> int:
        protocol_id = int(data.get("protocol_id") or 0)
        name = data["name"].strip()
        peptide_name = peptide_name_from_form(data)
        description = data.get("description", "").strip()
        if not peptide_name:
            raise ValueError("Choose or enter a peptide name.")
        if protocol_id:
            conn.execute(
                """
                UPDATE protocols
                SET name = ?, peptide_name = ?, description = ?, updated_at = ?
                WHERE id = ?
                """,
                (name, peptide_name, description, now_iso(), protocol_id),
            )
            return protocol_id
        cursor = conn.execute(
            """
            INSERT INTO protocols
              (name, peptide_name, description, status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, 'draft', ?, ?, ?)
            """,
            (name, peptide_name, description, user_id, now_iso(), now_iso()),
        )
        return int(cursor.lastrowid)

    def save_step(self, conn: sqlite3.Connection, data: dict[str, str]) -> int:
        protocol_id = int(data["protocol_id"])
        step_id = int(data.get("step_id") or 0)
        start_day = int(data["start_day"])
        end_day = int(data["end_day"])
        dose_amount = parse_dose_mg(data["dose_amount"])
        cadence_type = data.get("cadence_type", "daily").strip()
        if cadence_type not in {"daily", "every_n_days", "weekdays", "rest"}:
            cadence_type = "daily"
        if dose_amount <= 0:
            cadence_type = "rest"
        interval_days = max(1, int_or_default(data.get("interval_days"), 1))
        weekdays = normalize_weekdays(data.get("weekdays"))
        if cadence_type == "weekdays" and not weekdays:
            raise ValueError("Choose at least one weekday.")
        instructions = data.get("instructions", "").strip()
        if step_id:
            conn.execute(
                """
                UPDATE protocol_steps
                SET start_day = ?, end_day = ?, dose_amount = ?, cadence_type = ?,
                    interval_days = ?, weekdays = ?, instructions = ?
                WHERE id = ?
                """,
                (start_day, end_day, dose_amount, cadence_type, interval_days, weekdays, instructions, step_id),
            )
            return protocol_id
        count = one(conn, "SELECT count(*) c FROM protocol_steps WHERE protocol_id = ?", (protocol_id,))["c"]
        conn.execute(
            """
            INSERT INTO protocol_steps
              (protocol_id, sort_order, start_day, end_day, dose_amount, dose_unit,
               cadence_type, interval_days, weekdays, instructions)
            VALUES (?, ?, ?, ?, ?, 'mg', ?, ?, ?, ?)
            """,
            (protocol_id, count + 1, start_day, end_day, dose_amount, cadence_type, interval_days, weekdays, instructions),
        )
        return protocol_id

    def activate_protocol(self, conn: sqlite3.Connection, user_id: int, data: dict[str, str]) -> None:
        protocol_id = int(data["protocol_id"])
        existing = one(
            conn,
            "SELECT id FROM enrollments WHERE user_id = ? AND protocol_id = ? AND status IN ('active', 'paused')",
            (user_id, protocol_id),
        )
        if existing:
            conn.execute("UPDATE enrollments SET status = 'active' WHERE id = ?", (existing["id"],))
            return
        conn.execute(
            """
            INSERT INTO enrollments (user_id, protocol_id, start_date, status, reminder_time, created_at)
            VALUES (?, ?, ?, 'active', '09:00', ?)
            """,
            (user_id, protocol_id, today_iso(), now_iso()),
        )

    def create_user(self, conn: sqlite3.Connection, data: dict[str, str]) -> None:
        conn.execute(
            """
            INSERT INTO users (email, password_hash, display_name, role, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                data["email"].strip().lower(),
                password_hash(data["password"]),
                data["display_name"].strip(),
                data.get("role", "member"),
                now_iso(),
            ),
        )

    def create_peptide(self, conn: sqlite3.Connection, data: dict[str, str]) -> None:
        name = data["name"].strip()
        if not name:
            raise ValueError("Enter a peptide name.")
        color = normalize_color(data.get("color"), default_color_for_peptide(name))
        conn.execute(
            """
            INSERT OR IGNORE INTO peptides (name, notes, color, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (name, data.get("notes", "").strip(), color, now_iso()),
        )

    def update_peptide(self, conn: sqlite3.Connection, data: dict[str, str]) -> None:
        name = data["name"].strip()
        if not name:
            raise ValueError("Enter a peptide name.")
        conn.execute(
            """
            UPDATE peptides
            SET name = ?, notes = ?, color = ?
            WHERE id = ?
            """,
            (
                name,
                data.get("notes", "").strip(),
                normalize_color(data.get("color"), default_color_for_peptide(name)),
                int(data["peptide_id"]),
            ),
        )

    def require_admin(self, ctx: RequestContext) -> None:
        if not ctx.user or ctx.user["role"] != "admin":
            raise PermissionError("Admin access required.")

    def serve_static(self, path: Path) -> None:
        resolved = path.resolve()
        if not str(resolved).startswith(str(STATIC_DIR.resolve())) or not resolved.exists():
            return self.not_found()
        content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        data = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def download_backup(self) -> None:
        if not DB_PATH.exists():
            return self.not_found()
        backup_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="peptide-power-assistant-", suffix=".db", delete=False) as tmp:
                backup_path = Path(tmp.name)
            with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup_path) as target:
                source.backup(target)
            data = backup_path.read_bytes()
        finally:
            if backup_path:
                backup_path.unlink(missing_ok=True)
        timestamp = datetime.now(app_timezone()).strftime("%Y%m%d-%H%M%S")
        filename = f"peptide-power-assistant-{timestamp}.db"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.sqlite3")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def html(self, content: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def protocol_library_location(self) -> str:
        if PROTOCOL_LIBRARY_URL:
            return PROTOCOL_LIBRARY_URL
        proto = self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip() or "http"
        host = host_with_port(self.headers.get("Host", ""), PROTOCOL_LIBRARY_PORT or "8090")
        return f"{proto}://{host}/"

    def not_found(self) -> None:
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Not found")

    def text(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error_response(self, exc: Exception) -> None:
        status = HTTPStatus.FORBIDDEN if isinstance(exc, PermissionError) else HTTPStatus.BAD_REQUEST
        content = layout(RequestContext(self.context().user, error=str(exc)), "/settings", "Error", f'<section class="panel"><div class="empty">{h(exc)}</div></section>')
        self.html(content, status)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main() -> None:
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), App)
    print(f"{APP_NAME} running at http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
