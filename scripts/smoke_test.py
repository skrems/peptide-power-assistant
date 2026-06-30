#!/usr/bin/env python3
from __future__ import annotations

import http.cookiejar
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
ADMIN_EMAIL = "admin@example.local"
ADMIN_PASSWORD = "change-me-now"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Client:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def get(self, path: str) -> str:
        with self.opener.open(f"{self.base_url}{path}", timeout=10) as response:
            return response.read().decode("utf-8")

    def get_bytes(self, path: str) -> tuple[bytes, dict[str, str]]:
        with self.opener.open(f"{self.base_url}{path}", timeout=10) as response:
            return response.read(), dict(response.headers.items())

    def post(self, path: str, data: dict[str, str]) -> str:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=encoded, method="POST")
        with self.opener.open(request, timeout=10) as response:
            return response.read().decode("utf-8")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def redirect_location(url: str) -> str:
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(url, timeout=10)
    except urllib.error.HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            return exc.headers["Location"]
        raise
    raise AssertionError("expected redirect")


def wait_for_server(client: Client, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 15
    last_error: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early with code {proc.returncode}")
        try:
            if client.get("/healthz").strip() == "ok":
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"server did not become healthy: {last_error}")


def require(text: str, needle: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing expected text: {needle}")


def protocol_id(db_path: Path, name: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id FROM protocols WHERE name = ?", (name,)).fetchone()
    if not row:
        raise AssertionError(f"protocol not found: {name}")
    return str(row[0])


def user_id(db_path: Path, email: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        raise AssertionError(f"user not found: {email}")
    return str(row[0])


def main() -> int:
    port = free_port()
    db_path = Path(tempfile.gettempdir()) / f"peptide-power-smoke-{port}.db"
    db_path.unlink(missing_ok=True)
    env = {
        **os.environ,
        "PEPTIDE_HOST": "127.0.0.1",
        "PEPTIDE_PORT": str(port),
        "PEPTIDE_DB": str(db_path),
        "PEPTIDE_ADMIN_EMAIL": ADMIN_EMAIL,
        "PEPTIDE_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "PEPTIDE_SECRET": "smoke-test-secret",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.server"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    client = Client(f"http://127.0.0.1:{port}")
    try:
        wait_for_server(client, proc)

        library_location = redirect_location(f"http://127.0.0.1:{port}/library")
        if library_location != "http://127.0.0.1:8090/":
            raise AssertionError(f"unexpected library redirect: {library_location}")

        login = client.post("/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        require(login, "Today")

        protocols = client.get("/protocols")
        require(protocols, "GHK-Cu 60-day ramp")
        require(protocols, "Selank SK10 12-week cycle")
        require(protocols, "Days 1-3: 0.25 mg · Daily")
        require(protocols, "Days 4-14: 0.4 mg · Daily")
        require(protocols, "Days 29-42: 0 mg · Rest")
        require(protocols, "Days 71-84: 0 mg · Rest")
        require(protocols, "Tesamorelin TSM10 12-week cycle")
        require(protocols, "Days 1-7: 1 mg · Mon, Tue, Wed, Thu, Fri")
        require(protocols, "Days 8-28: 1.5 mg · Mon, Tue, Wed, Thu, Fri")
        require(protocols, "Days 29-56: 2 mg · Mon, Tue, Wed, Thu, Fri")
        require(protocols, "Days 57-84: 2 mg · Mon, Tue, Wed, Thu, Fri")
        require(protocols, "<select name=\"peptide_name\">")
        require(protocols, "Days 1-15: 1 mg · Daily")
        require(protocols, "Days 16-30: 2 mg · Daily")
        require(protocols, "Days 31-60: 0 mg · Rest")
        require(protocols, "Every N days")
        require(protocols, "Selected weekdays")
        require(protocols, "Edit")
        require(protocols, "Delete")

        client.post(
            "/protocols/save",
            {
                "protocol_id": "",
                "name": "Retatrutide weekly",
                "peptide_name": "Retatrutide",
                "peptide_name_other": "",
                "description": "Every 7 days.",
            },
        )
        retatrutide_id = protocol_id(db_path, "Retatrutide weekly")
        client.post(
            "/steps/save",
            {
                "protocol_id": retatrutide_id,
                "start_day": "1",
                "end_day": "84",
                "dose_amount": "2",
                "cadence_type": "every_n_days",
                "interval_days": "7",
                "weekdays": "",
                "instructions": "Weekly dose",
            },
        )
        client.post(
            "/steps/save",
            {
                "protocol_id": retatrutide_id,
                "start_day": "4",
                "end_day": "14",
                "dose_amount": "400 mcg",
                "cadence_type": "daily",
                "interval_days": "1",
                "weekdays": "",
                "instructions": "Microgram dose",
            },
        )
        client.post(
            "/protocols/save",
            {
                "protocol_id": "",
                "name": "MOTS-c MWF",
                "peptide_name": "",
                "peptide_name_other": "MOTS-c",
                "description": "Monday, Wednesday, Friday.",
            },
        )
        mots_id = protocol_id(db_path, "MOTS-c MWF")
        client.post(
            "/steps/save",
            {
                "protocol_id": mots_id,
                "start_day": "1",
                "end_day": "42",
                "dose_amount": "5",
                "cadence_type": "weekdays",
                "interval_days": "1",
                "weekdays": "mon,wed,fri",
                "instructions": "MWF dose",
            },
        )
        protocols = client.get("/protocols")
        require(protocols, "Retatrutide weekly")
        require(protocols, "Days 1-84: 2 mg · Every 7 days")
        require(protocols, "Days 4-14: 0.4 mg · Daily")
        require(protocols, "MOTS-c MWF")
        require(protocols, "Days 1-42: 5 mg · Mon, Wed, Fri")

        client.post("/protocols/activate", {"protocol_id": "1"})
        today = client.get("/")
        require(today, "protocol day 1")
        require(today, "1 mg")
        require(today, "Log completed")
        require(today, "/static/body-map.svg")
        require(today, "Left Deltoid")

        client.post(
            "/log/protocol",
            {
                "enrollment_id": "1",
                "step_id": "1",
                "protocol_day": "1",
                "actual_dose_amount": "1",
                "site": "abdomen",
                "notes": "smoke test",
            },
        )
        logged_today = client.get("/")
        require(logged_today, "done")
        require(logged_today, "smoke test")
        require(logged_today, "--peptide-color: #7e3bb5")

        log = client.get("/log")
        require(log, "<select name=\"peptide_name\">")
        require(log, 'name="logged_at" type="datetime-local"')
        require(log, "Morning")
        require(log, "Night")
        require(log, "SS-31")
        require(log, "/static/body-map.svg")
        require(log, "Right Thigh")
        client.post(
            "/log/manual",
            {
                "peptide_name": "SS-31",
                "peptide_name_other": "",
                "logged_at": "2026-05-03T20:00",
                "actual_dose_amount": "5",
                "site": "arm",
                "notes": "manual smoke",
            },
        )
        log = client.get("/log")
        require(log, "Dose history")
        require(log, "GHK-Cu")
        require(log, "abdomen")
        require(log, "2026-05-03T20:00:00")
        require(log, "manual smoke")
        require(log, "--peptide-color: #111111")
        require(log, "Filter by peptide")
        require(log, "Edit")
        require(log, "Delete")

        filtered_log = client.get("/log?peptide=SS-31")
        require(filtered_log, "manual smoke")
        require(filtered_log, "value=\"SS-31\" selected")

        edit_log = client.get("/log?peptide=SS-31&edit=2")
        require(edit_log, "edit-item")
        require(edit_log, "SS-31")
        require(edit_log, "Save dose")
        client.post(
            "/logs/save",
            {
                "log_id": "2",
                "return_to": "/log?peptide=SS-31",
                "peptide_name": "SS-31",
                "peptide_name_other": "",
                "logged_at": "2026-05-03T08:00",
                "actual_dose_amount": "6",
                "site": "Right Thigh",
                "notes": "manual edited",
            },
        )
        edited_log = client.get("/log?peptide=SS-31")
        require(edited_log, "manual edited")
        require(edited_log, "6 mg")
        require(edited_log, "Right Thigh")

        client.post(
            "/logs/save",
            {
                "log_id": "",
                "return_to": "/log?peptide=SS-31",
                "peptide_name": "SS-31",
                "peptide_name_other": "",
                "logged_at": "2026-05-03T21:00",
                "actual_dose_amount": "1.5",
                "site": "Left Thigh",
                "notes": "second same-day dose",
            },
        )

        settings = client.get("/settings")
        require(settings, "App version v1.15")
        require(settings, "Recent Dose Audit")
        require(settings, "Success: manual create")

        expected_time_before = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%H:%M")
        calendar = client.get("/calendar?month=2026-05")
        expected_time_after = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%H:%M")
        require(calendar, "Calendar")
        require(calendar, "May 2026")
        require(calendar, "SS-31")
        require(calendar, "--peptide-color: #111111")
        require(calendar, "6 mg")
        require(calendar, "1.5 mg")
        require(calendar, "Add dose")
        require(calendar, "Previous day AM")
        require(calendar, "Previous day PM")
        require(calendar, "Logged this day")
        expected_values = {
            f'value="2026-05-01T{expected_time_before}"',
            f'value="2026-05-01T{expected_time_after}"',
        }
        if not any(value in calendar for value in expected_values):
            raise AssertionError("calendar add form did not default to the current local time")

        client.post(
            "/logs/save",
            {
                "log_id": "",
                "return_to": "/calendar?month=2026-05&date=2026-05-04",
                "peptide_name": "Retatrutide",
                "peptide_name_other": "",
                "logged_at": "2026-05-04T08:00",
                "actual_dose_amount": "2",
                "site": "Left Abdomen",
                "notes": "calendar add",
            },
        )
        calendar_day = client.get("/calendar?month=2026-05&date=2026-05-04")
        require(calendar_day, "Retatrutide")
        require(calendar_day, "calendar add")
        require(calendar_day, "--peptide-color: #8b0000")
        require(calendar_day, "Edit")
        client.post(
            "/logs/save",
            {
                "log_id": "4",
                "return_to": "/calendar?month=2026-05&date=2026-05-04",
                "peptide_name": "Retatrutide",
                "peptide_name_other": "",
                "logged_at": "2026-05-04T20:00",
                "actual_dose_amount": "2.5",
                "site": "Right Abdomen",
                "notes": "calendar edited",
            },
        )
        calendar_day = client.get("/calendar?month=2026-05&date=2026-05-04")
        require(calendar_day, "calendar edited")
        require(calendar_day, "2.5 mg")
        client.post(
            "/logs/delete",
            {
                "log_id": "4",
                "return_to": "/calendar?month=2026-05&date=2026-05-04",
            },
        )
        calendar_day = client.get("/calendar?month=2026-05&date=2026-05-04")
        if "calendar edited" in calendar_day:
            raise AssertionError("calendar delete did not remove the edited log")

        copied_am = client.post(
            "/logs/copy-previous-day",
            {
                "target_date": "2026-05-04",
                "copy_period": "am",
                "return_to": "/calendar?month=2026-05&date=2026-05-04",
            },
        )
        require(copied_am, "Copied 1 AM dose from the previous day")
        require(copied_am, "manual edited")
        require(copied_am, "2026-05-04T08:00:00")
        copied_pm = client.post(
            "/logs/copy-previous-day",
            {
                "target_date": "2026-05-04",
                "copy_period": "pm",
                "return_to": "/calendar?month=2026-05&date=2026-05-04",
            },
        )
        require(copied_pm, "Copied 1 PM dose from the previous day")
        require(copied_pm, "second same-day dose")
        require(copied_pm, "2026-05-04T20:00:00")
        copied_pm_again = client.post(
            "/logs/copy-previous-day",
            {
                "target_date": "2026-05-04",
                "copy_period": "pm",
                "return_to": "/calendar?month=2026-05&date=2026-05-04",
            },
        )
        require(copied_pm_again, "Copied 0 PM doses from the previous day; skipped 1 already present")
        with sqlite3.connect(db_path) as conn:
            copied_count = conn.execute(
                "SELECT count(*) FROM dose_logs WHERE user_id = 1 AND substr(logged_at, 1, 10) = '2026-05-04'",
            ).fetchone()[0]
        if copied_count != 2:
            raise AssertionError(f"copy previous day created {copied_count} rows, expected 2")

        for peptide_name, logged_at in (("SS-31", "2026-05-08T11:59"), ("Semax", "2026-05-08T12:00")):
            client.post(
                "/logs/save",
                {
                    "log_id": "",
                    "return_to": "/calendar?month=2026-05&date=2026-05-08",
                    "peptide_name": peptide_name,
                    "peptide_name_other": "",
                    "logged_at": logged_at,
                    "actual_dose_amount": "1",
                    "site": "",
                    "notes": "copy boundary test",
                },
            )
        for period in ("am", "pm"):
            client.post(
                "/logs/copy-previous-day",
                {
                    "target_date": "2026-05-09",
                    "copy_period": period,
                    "return_to": "/calendar?month=2026-05&date=2026-05-09",
                },
            )
        with sqlite3.connect(db_path) as conn:
            boundary_rows = conn.execute(
                """
                SELECT peptide_name, logged_at FROM dose_logs
                WHERE user_id = 1 AND notes = 'copy boundary test' AND substr(logged_at, 1, 10) = '2026-05-09'
                ORDER BY peptide_name
                """,
            ).fetchall()
        if boundary_rows != [("SS-31", "2026-05-09T08:00:00"), ("Semax", "2026-05-09T20:00:00")]:
            raise AssertionError(f"AM/PM noon boundary was classified incorrectly: {boundary_rows}")

        admin = client.get("/admin")
        require(admin, "Peptides")
        require(admin, "Add peptide")
        require(admin, 'value="#7e3bb5"')
        require(admin, 'value="#e86f00"')
        require(admin, 'value="#8b0000"')
        require(admin, "Add user")
        require(admin, ADMIN_EMAIL)
        require(admin, "Export backup file")

        backup_data, backup_headers = client.get_bytes("/backup")
        if not backup_data.startswith(b"SQLite format 3"):
            raise AssertionError("backup export did not return a SQLite database")
        if "peptide-power-assistant-" not in backup_headers.get("Content-Disposition", ""):
            raise AssertionError("backup export did not include the expected filename")

        client.post(
            "/admin/users",
            {
                "email": "member@example.local",
                "display_name": "Smoke Member",
                "password": "member-password",
                "role": "member",
            },
        )
        member_id = user_id(db_path, "member@example.local")
        admin_log = client.get("/log")
        require(admin_log, "Log for")
        require(admin_log, "Smoke Member (member@example.local)")
        client.post(
            "/logs/save",
            {
                "log_id": "",
                "target_user_id": member_id,
                "return_to": "/log",
                "peptide_name": "GHK-Cu",
                "peptide_name_other": "",
                "logged_at": "2026-05-06T08:00",
                "actual_dose_amount": "1",
                "site": "Left Abdomen",
                "notes": "admin entered for member",
            },
        )
        member_history = client.get(f"/log?user_id={member_id}")
        require(member_history, "admin entered for member")
        require(member_history, "Logged for Smoke Member")

        member_client = Client(f"http://127.0.0.1:{port}")
        member_client.post("/login", {"email": "member@example.local", "password": "member-password"})
        member_log = member_client.get("/log")
        require(member_log, "admin entered for member")
        if "Log for" in member_log:
            raise AssertionError("member account was shown the admin user selector")
        member_client.post(
            "/logs/save",
            {
                "log_id": "",
                "target_user_id": user_id(db_path, ADMIN_EMAIL),
                "return_to": "/log",
                "peptide_name": "SS-31",
                "peptide_name_other": "",
                "logged_at": "2026-05-07T08:00",
                "actual_dose_amount": "1",
                "site": "Right Thigh",
                "notes": "member ownership guard",
            },
        )
        with sqlite3.connect(db_path) as conn:
            owner = conn.execute(
                "SELECT user_id FROM dose_logs WHERE notes = 'member ownership guard'",
            ).fetchone()
        if not owner or str(owner[0]) != member_id:
            raise AssertionError("member was able to assign a dose to another user")
        try:
            member_client.get_bytes("/backup")
        except urllib.error.HTTPError as exc:
            if exc.code != 403:
                raise AssertionError(f"member backup export returned {exc.code}, expected 403") from exc
        else:
            raise AssertionError("member account was allowed to export backup")

        print(f"Smoke test passed at http://127.0.0.1:{port}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
