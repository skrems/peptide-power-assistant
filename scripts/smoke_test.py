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
from pathlib import Path


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

    def post(self, path: str, data: dict[str, str]) -> str:
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=encoded, method="POST")
        with self.opener.open(request, timeout=10) as response:
            return response.read().decode("utf-8")


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

        login = client.post("/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        require(login, "Today")

        protocols = client.get("/protocols")
        require(protocols, "GHK-Cu 60-day ramp")
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
        client.post(
            "/steps/save",
            {
                "protocol_id": "2",
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
            "/protocols/save",
            {
                "protocol_id": "",
                "name": "MOTS-c MWF",
                "peptide_name": "",
                "peptide_name_other": "MOTS-c",
                "description": "Monday, Wednesday, Friday.",
            },
        )
        client.post(
            "/steps/save",
            {
                "protocol_id": "3",
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
                "logged_at": "2026-05-03T20:00",
                "actual_dose_amount": "6",
                "site": "Right Thigh",
                "notes": "manual edited",
            },
        )
        edited_log = client.get("/log?peptide=SS-31")
        require(edited_log, "manual edited")
        require(edited_log, "6 mg")
        require(edited_log, "Right Thigh")

        calendar = client.get("/calendar?month=2026-05")
        require(calendar, "Calendar")
        require(calendar, "May 2026")
        require(calendar, "SS-31")
        require(calendar, "--peptide-color: #111111")
        require(calendar, "Add dose")
        require(calendar, "Logged this day")

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
                "log_id": "3",
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
                "log_id": "3",
                "return_to": "/calendar?month=2026-05&date=2026-05-04",
            },
        )
        calendar_day = client.get("/calendar?month=2026-05&date=2026-05-04")
        if "calendar edited" in calendar_day:
            raise AssertionError("calendar delete did not remove the edited log")

        admin = client.get("/admin")
        require(admin, "Peptides")
        require(admin, "Add peptide")
        require(admin, 'value="#7e3bb5"')
        require(admin, 'value="#e86f00"')
        require(admin, 'value="#8b0000"')
        require(admin, "Add user")
        require(admin, ADMIN_EMAIL)

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
