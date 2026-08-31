from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_cli_doctor_reports_healthy_state_without_printing_secrets(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    deepseek_key = "sk-doctor-secret"
    bridge_token = "bridge-secret-token"
    access_password = "dashboard-password-secret"
    session_secret = "session-cookie-secret"
    _run_cli(["init-db"], tmp_path, database_path, media_root, deepseek_key=deepseek_key, bridge_token=bridge_token)
    _insert_enabled_playbooks_for_available_listings(database_path)
    media_path = media_root / "properties" / "PROP-001" / "living.jpg"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"photo")
    _insert_media(database_path, "PROP-001", media_path, enabled=True)
    before = _database_snapshot(database_path)

    with _json_server({"ok": True, "app": "prosper"}) as backend_url:
        with _dashboard_server() as dashboard_url:
            with _json_server({"ok": True, "bridge": "prosper-bridge"}) as bridge_url:
                completed = _run_cli(
                    [
                        "doctor",
                        "--backend-url",
                        backend_url,
                        "--dashboard-url",
                        dashboard_url,
                        "--bridge-url",
                        bridge_url,
                    ],
                    tmp_path,
                    database_path,
                    media_root,
                    deepseek_key=deepseek_key,
                    bridge_token=bridge_token,
                    bridge_url=bridge_url,
                    extra_env={
                        "AUTH_REQUIRED": "true",
                        "ACCESS_PASSWORD": access_password,
                        "SESSION_SECRET": session_secret,
                    },
                )

    assert completed.returncode == 0
    assert "[PASS] dependencies:" in completed.stdout
    assert "[PASS] database: SQLite database exists and is readable." in completed.stdout
    assert "Prosper currently does not include schema migrations" in completed.stdout
    assert "[PASS] media:" in completed.stdout
    assert "0 warning(s), 0 failure(s)" in completed.stdout
    assert deepseek_key not in completed.stdout
    assert bridge_token not in completed.stdout
    assert access_password not in completed.stdout
    assert session_secret not in completed.stdout
    assert completed.stderr == ""
    assert _database_snapshot(database_path) == before


def test_cli_doctor_reports_degraded_local_state_as_warnings(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    _run_cli(["init-db"], tmp_path, database_path, media_root)

    completed = _run_cli(["doctor", "--timeout-seconds", "0.05"], tmp_path, database_path, media_root)

    assert completed.returncode == 0
    assert "[WARN] environment: Dashboard authentication is disabled" in completed.stdout
    assert "[WARN] deepseek: DeepSeek API key is not configured" in completed.stdout
    assert "[WARN] bridge-token: Bridge token is not configured" in completed.stdout
    assert "[WARN] playbooks: At least one enabled Playbook is required before live startup." in completed.stdout
    assert "[WARN] playbooks: Available Rental Listings without enabled Playbooks:" in completed.stdout
    assert "[WARN] runtime-backend: Backend endpoint is not reachable" in completed.stdout
    assert "[WARN] runtime-dashboard: Dashboard endpoint is not reachable" in completed.stdout
    assert "[WARN] runtime-bridge: Bridge endpoint is not reachable" in completed.stdout
    assert "0 failure(s)" in completed.stdout


def test_cli_doctor_fails_for_unsafe_environment_without_printing_secret_values(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    placeholder_key = "replace-with-your-key"
    _run_cli(["init-db"], tmp_path, database_path, media_root, deepseek_key="sk-ok", bridge_token="token-ok")
    _insert_enabled_playbooks_for_available_listings(database_path)

    completed = _run_cli(
        ["doctor", "--timeout-seconds", "0.05"],
        tmp_path,
        database_path,
        media_root,
        check=False,
        extra_env={
            "AUTH_REQUIRED": "true",
            "ACCESS_PASSWORD": "",
            "SESSION_SECRET": "",
            "DEEPSEEK_API_KEY": placeholder_key,
            "PROSPER_BRIDGE_BASE_URL": "http://192.0.2.10:8788",
            "PROSPER_BRIDGE_TOKEN": "",
        },
    )

    assert completed.returncode != 0
    assert "[FAIL] environment:" in completed.stdout
    assert "[FAIL] deepseek: DeepSeek API key appears to use a placeholder value." in completed.stdout
    assert "[FAIL] bridge-token: Bridge token is required when the bridge is not on loopback." in completed.stdout
    assert placeholder_key not in completed.stdout


def test_cli_doctor_fails_for_missing_and_unreadable_database_without_creating_it(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"

    missing = _run_cli(["doctor", "--timeout-seconds", "0.05"], tmp_path, database_path, media_root, check=False)

    assert missing.returncode != 0
    assert "[FAIL] database: Configured SQLite database file is missing." in missing.stdout
    assert not database_path.exists()
    assert not media_root.exists()

    database_path.parent.mkdir(parents=True)
    database_path.write_bytes(b"")
    database_path.chmod(0)
    try:
        unreadable = _run_cli(["doctor", "--timeout-seconds", "0.05"], tmp_path, database_path, media_root, check=False)
    finally:
        database_path.chmod(0o600)

    assert unreadable.returncode != 0
    assert "[FAIL] database: Configured SQLite database could not be opened read-only." in unreadable.stdout
    assert not media_root.exists()


def test_cli_doctor_fails_for_missing_schema_table(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY)")

    completed = _run_cli(["doctor", "--timeout-seconds", "0.05"], tmp_path, database_path, media_root, check=False)

    assert completed.returncode != 0
    assert "Prosper currently does not include schema migrations" in completed.stdout
    assert "[FAIL] schema: Missing required tables:" in completed.stdout
    assert "property_playbooks" in completed.stdout


def test_cli_doctor_strict_runtime_requires_backend_dashboard_and_bridge(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    _run_cli(["init-db"], tmp_path, database_path, media_root, deepseek_key="sk-ok", bridge_token="token-ok")

    completed = _run_cli(
        ["doctor", "--strict-runtime", "--timeout-seconds", "0.05"],
        tmp_path,
        database_path,
        media_root,
        deepseek_key="sk-ok",
        bridge_token="token-ok",
        check=False,
    )

    assert completed.returncode != 0
    assert "[FAIL] playbooks: At least one enabled Playbook is required before live startup." in completed.stdout
    assert "[FAIL] runtime-backend: Backend endpoint is not reachable" in completed.stdout
    assert "[FAIL] runtime-dashboard: Dashboard endpoint is not reachable" in completed.stdout
    assert "[FAIL] runtime-bridge: Bridge endpoint is not reachable" in completed.stdout


def _run_cli(
    args: list[str],
    cwd: Path,
    database_path: Path,
    media_root: Path,
    *,
    check: bool = True,
    deepseek_key: str = "",
    bridge_token: str = "",
    bridge_url: str = "http://127.0.0.1:8788",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if args[:1] != ["doctor"]:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT_DIR / "backend"),
        "DATABASE_URL": f"sqlite:///{database_path}",
        "MEDIA_ROOT": str(media_root),
        "PROSPER_ENV_FILE": str(cwd / ".env.missing"),
        "DEEPSEEK_API_KEY": deepseek_key,
        "PROSPER_BRIDGE_TOKEN": bridge_token,
        "PROSPER_BRIDGE_BASE_URL": bridge_url,
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def _insert_media(database_path: Path, property_id: str, media_path: Path, *, enabled: bool) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO property_media (property_id, media_type, file_path, caption, sort_order, enabled)
            VALUES (?, 'photo', ?, '', 0, ?)
            """,
            (property_id, str(media_path), enabled),
        )
        connection.commit()


def _insert_enabled_playbooks_for_available_listings(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT property_id FROM properties WHERE status = 'available'").fetchall()
        for (property_id,) in rows:
            connection.execute(
                """
                INSERT OR IGNORE INTO property_playbooks (property_id, initial_reply_blocks, enabled)
                VALUES (?, '[{"type":"message","text":"Available"}]', 1)
                """,
                (property_id,),
            )
        connection.commit()


def _database_snapshot(database_path: Path) -> dict[str, int]:
    tables = [
        "app_config",
        "contacts",
        "conversations",
        "messages",
        "properties",
        "property_media",
        "property_playbooks",
        "stage_runs",
    ]
    with sqlite3.connect(database_path) as connection:
        return {table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables}


@contextmanager
def _json_server(payload: dict[str, object]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=2)


@contextmanager
def _dashboard_server() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"<!doctype html><title>Prosper Dashboard</title>"
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=2)
