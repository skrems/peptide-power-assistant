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
        require(protocols, "Days 1-15: 1 mg daily")
        require(protocols, "Days 16-30: 2 mg daily")
        require(protocols, "Days 31-60: 0 mg rest")
        require(protocols, "Edit")
        require(protocols, "Delete")

        client.post("/protocols/activate", {"protocol_id": "1"})
        today = client.get("/")
        require(today, "protocol day 1")
        require(today, "1 mg")
        require(today, "Log completed")
        require(today, "/static/body-sites.svg")
        require(today, "Abdomen left")

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

        log = client.get("/log")
        require(log, "<select name=\"peptide_name\">")
        require(log, "SS-31")
        require(log, "/static/body-sites.svg")
        require(log, "Thigh right")
        client.post(
            "/log/manual",
            {
                "peptide_name": "SS-31",
                "peptide_name_other": "",
                "actual_dose_amount": "5",
                "site": "arm",
                "notes": "manual smoke",
            },
        )
        log = client.get("/log")
        require(log, "Dose history")
        require(log, "GHK-Cu")
        require(log, "abdomen")
        require(log, "manual smoke")

        admin = client.get("/admin")
        require(admin, "Peptides")
        require(admin, "Add peptide")
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
