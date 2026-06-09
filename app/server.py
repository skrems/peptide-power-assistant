from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_NAME = "Peptide Power Assistant"
ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
DB_PATH = Path(os.environ.get("PEPTIDE_DB", ROOT / "data" / "app.db"))
HOST = os.environ.get("PEPTIDE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PEPTIDE_PORT", "8080"))
SECRET = os.environ.get("PEPTIDE_SECRET", secrets.token_hex(32))
ADMIN_EMAIL = os.environ.get("PEPTIDE_ADMIN_EMAIL", "admin@example.local").strip().lower()
ADMIN_PASSWORD = os.environ.get("PEPTIDE_ADMIN_PASSWORD", "change-me-now")

SESSIONS: dict[str, int] = {}


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
    return datetime.now().replace(microsecond=0).isoformat()


def today_iso() -> str:
    return date.today().isoformat()


def h(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


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
              cadence_type TEXT NOT NULL CHECK (cadence_type IN ('daily', 'rest')),
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
        seed_admin(conn)
        seed_peptides(conn)
        seed_ghk_protocol(conn)


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
        ("GHK-Cu", "Copper peptide protocols."),
        ("SS-31", "Daily protocol candidate."),
        ("Retatrutide", "Weekly or every-six-days protocol candidate."),
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO peptides (name, notes, created_at)
        VALUES (?, ?, ?)
        """,
        [(name, notes, now_iso()) for name, notes in defaults],
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


def icon(name: str) -> str:
    icons = {
        "today": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 5h16v15H4z"/><path d="M8 3v4M16 3v4M4 10h16"/></svg>',
        "protocols": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M7 4h10l3 3v13H7z"/><path d="M17 4v4h4M10 12h7M10 16h7"/></svg>',
        "log": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 4h14v16H5z"/><path d="M9 8h6M9 12h6M9 16h4"/></svg>',
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
        {nav_item("/log", "Log", active, icon("log"))}
        {admin_nav}
        {nav_item("/settings", "Settings", active, icon("settings"))}
      </nav>
      <script>
        if ("serviceWorker" in navigator) {{
          navigator.serviceWorker.register("/service-worker.js").catch(() => undefined);
        }}
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


def with_flash(path: str, message: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}flash={urllib.parse.quote(message)}"


def get_due_tasks(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
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
        day = protocol_day(enrollment["start_date"])
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
              <label>Actual dose <input name="actual_dose_amount" inputmode="decimal" value="{format_dose(step['dose_amount'])}" required></label>
              <label>Site <input name="site" placeholder="optional"></label>
              <label class="grid-span">Notes <input name="notes" placeholder="optional"></label>
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
              <p>{format_dose(step['dose_amount'])} {h(step['dose_unit'])} · days {step['start_day']}-{step['end_day']}</p>
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
          <div class="item-title"><h3>{h(row['peptide_name'])}</h3><span class="badge">{h(row['status'])}</span></div>
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
        <span class="badge">{date.today().strftime('%b %-d, %Y') if sys.platform != 'win32' else date.today().strftime('%b %#d, %Y')}</span>
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
            f"Days {step['start_day']}-{step['end_day']}: {format_dose(step['dose_amount'])} {h(step['dose_unit'])} {h(step['cadence_type'])}"
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
          <label>Dose mg <input name="dose_amount" inputmode="decimal" value="1" required></label>
        </div>
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
              <label>Dose mg <input name="dose_amount" value="{format_dose(step['dose_amount'])}" required></label>
            </div>
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


def render_log(ctx: RequestContext, conn: sqlite3.Connection) -> bytes:
    assert ctx.user
    logs = query(
        conn,
        "SELECT * FROM dose_logs WHERE user_id = ? ORDER BY logged_at DESC LIMIT 100",
        (ctx.user["id"],),
    )
    log_html = "".join(
        f"""
        <article class="item">
          <div class="item-title"><h3>{h(row['peptide_name'])}</h3><span class="badge">{h(row['source'])}</span></div>
          <p class="meta">{h(row['logged_at'])} · {format_dose(row['actual_dose_amount'])} {h(row['dose_unit'])}</p>
          <p class="meta">{'protocol day ' + str(row['protocol_day']) if row['protocol_day'] else ''} {h(row['site'])}</p>
          <p>{h(row['notes'])}</p>
        </article>
        """
        for row in logs
    ) or '<div class="empty">No dose logs yet.</div>'
    body = f"""
    <section class="panel">
      <div class="panel-head"><h2>Manual dose</h2></div>
      <form method="post" action="/log/manual" class="stack">
        <div class="grid three">
          <label>Peptide
            <select name="peptide_name">
              {peptide_select(conn)}
            </select>
          </label>
          <label>Dose mg <input name="actual_dose_amount" inputmode="decimal" required></label>
          <label>Site <input name="site" placeholder="optional"></label>
        </div>
        <label>Other peptide name <input name="peptide_name_other" placeholder="Only needed if not in the dropdown"></label>
        <label>Notes <input name="notes" placeholder="optional"></label>
        <div class="button-row"><button type="submit">Log manual dose</button></div>
      </form>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Dose history</h2></div>
      <div class="card-list">{log_html}</div>
    </section>
    """
    return layout(ctx, "/log", "Log", body)


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
            <div><h3>{h(row['name'])}</h3><p class="meta">{h(row['notes'])}</p></div>
          </div>
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
      <div class="panel-head"><h2>Peptides</h2></div>
      <form method="post" action="/admin/peptides" class="stack">
        <div class="grid two">
          <label>Name <input name="name" placeholder="BPC-157" required></label>
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
    body = f"""
    <section class="panel">
      <div class="panel-head"><h2>Data</h2></div>
      <div class="card-list">
        <article class="item"><h3>{counts['protocols']} protocols</h3><p class="meta">Published, draft, and retired definitions.</p></article>
        <article class="item"><h3>{counts['enrollments']} enrollments</h3><p class="meta">Your active and historical protocol activations.</p></article>
        <article class="item"><h3>{counts['logs']} dose logs</h3><p class="meta">Your tracked protocol and manual doses.</p></article>
        <article class="item"><h3>{counts['checkins']} check-ins</h3><p class="meta">Daily symptom and note entries.</p></article>
      </div>
      <div class="button-row"><a class="button secondary" href="/backup">Download SQLite backup</a></div>
    </section>
    <section class="panel">
      <h2>Install on iPhone</h2>
      <p class="meta">Open this site in Safari, tap Share, then Add to Home Screen.</p>
    </section>
    """
    return layout(ctx, "/settings", "Settings", body)


class App(BaseHTTPRequestHandler):
    server_version = "PeptidePowerAssistant/0.1"

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/", "/login", "/healthz"}:
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

        ctx = self.context()
        if not ctx.user:
            return self.redirect("/login")

        params = urllib.parse.parse_qs(parsed.query)
        with db() as conn:
            if parsed.path == "/":
                return self.html(render_today(ctx, conn))
            if parsed.path == "/protocols":
                return self.html(render_protocols(ctx, conn, params))
            if parsed.path == "/log":
                return self.html(render_log(ctx, conn))
            if parsed.path == "/admin":
                return self.html(render_admin(ctx, conn))
            if parsed.path == "/settings":
                return self.html(render_settings(ctx, conn))
            if parsed.path == "/backup":
                return self.download_backup()
        self.not_found()

    def route_post(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        data = self.form_data()
        if parsed.path == "/login":
            return self.login(data)
        if parsed.path == "/logout":
            return self.logout()

        ctx = self.context()
        if not ctx.user:
            return self.redirect("/login")

        with db() as conn:
            if parsed.path == "/checkin":
                self.save_checkin(conn, ctx.user["id"], data)
                return self.redirect(with_flash("/", "Check-in saved"))
            if parsed.path == "/log/protocol":
                self.log_protocol(conn, ctx.user["id"], data)
                return self.redirect(with_flash("/", "Dose logged"))
            if parsed.path == "/log/manual":
                self.log_manual(conn, ctx.user["id"], data)
                return self.redirect(with_flash("/log", "Manual dose logged"))
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
        return {key: values[-1] for key, values in parsed.items()}

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

    def log_protocol(self, conn: sqlite3.Connection, user_id: int, data: dict[str, str]) -> None:
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
        conn.execute(
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
                float(data["actual_dose_amount"]),
                data.get("site", "").strip(),
                data.get("notes", "").strip(),
                now_iso(),
            ),
        )

    def log_manual(self, conn: sqlite3.Connection, user_id: int, data: dict[str, str]) -> None:
        peptide_name = peptide_name_from_form(data)
        if not peptide_name:
            raise ValueError("Choose or enter a peptide name.")
        conn.execute(
            """
            INSERT INTO dose_logs
              (user_id, source, peptide_name, actual_dose_amount, dose_unit, status, site, notes, logged_at)
            VALUES (?, 'manual', ?, ?, 'mg', 'completed', ?, ?, ?)
            """,
            (
                user_id,
                peptide_name,
                float(data["actual_dose_amount"]),
                data.get("site", "").strip(),
                data.get("notes", "").strip(),
                now_iso(),
            ),
        )

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
        dose_amount = float(data["dose_amount"])
        cadence_type = "rest" if dose_amount <= 0 else "daily"
        instructions = data.get("instructions", "").strip()
        if step_id:
            conn.execute(
                """
                UPDATE protocol_steps
                SET start_day = ?, end_day = ?, dose_amount = ?, cadence_type = ?, instructions = ?
                WHERE id = ?
                """,
                (start_day, end_day, dose_amount, cadence_type, instructions, step_id),
            )
            return protocol_id
        count = one(conn, "SELECT count(*) c FROM protocol_steps WHERE protocol_id = ?", (protocol_id,))["c"]
        conn.execute(
            """
            INSERT INTO protocol_steps
              (protocol_id, sort_order, start_day, end_day, dose_amount, dose_unit, cadence_type, instructions)
            VALUES (?, ?, ?, ?, ?, 'mg', ?, ?)
            """,
            (protocol_id, count + 1, start_day, end_day, dose_amount, cadence_type, instructions),
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
        conn.execute(
            """
            INSERT OR IGNORE INTO peptides (name, notes, created_at)
            VALUES (?, ?, ?)
            """,
            (name, data.get("notes", "").strip(), now_iso()),
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
        data = DB_PATH.read_bytes()
        filename = f"peptide-power-assistant-{today_iso()}.db"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
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
