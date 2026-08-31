from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tarfile

from app.backup import verify_backup


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_cli_backup_creates_verified_archive_with_managed_media_boundaries(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    output_dir = runtime / "backups"
    _run_cli(["init-db"], tmp_path, database_path, media_root)

    enabled_media = media_root / "properties" / "RTF-001" / "enabled.jpg"
    disabled_media = media_root / "properties" / "RTF-001" / "disabled.jpg"
    enabled_media.parent.mkdir(parents=True)
    enabled_media.write_bytes(b"enabled media")
    disabled_media.write_bytes(b"disabled media")

    outside_media = tmp_path / "outside.jpg"
    outside_media.write_bytes(b"outside media")
    secret_file = media_root / "properties" / "RTF-001" / ".env"
    secret_file.write_text("DEEPSEEK_API_KEY=not-backed-up")
    runtime_env_file = runtime / ".env"
    runtime_env_file.write_text("SESSION_SECRET=not-backed-up")
    bridge_credentials = runtime / "bridge" / "auth" / "creds.json"
    bridge_credentials.parent.mkdir(parents=True)
    bridge_credentials.write_text("whatsapp pairing credentials")
    cache_file = runtime / ".cache" / "state.json"
    cache_file.parent.mkdir()
    cache_file.write_text("cached data")
    build_file = runtime / "build" / "bundle.js"
    build_file.parent.mkdir()
    build_file.write_text("compiled output")
    logs_file = runtime / "prosper.log"
    logs_file.write_text("tenant log line")
    previous_backup = output_dir / "old-backup.tar.gz"
    previous_backup.parent.mkdir(parents=True)
    previous_backup.write_bytes(b"old backup")

    symlink_path = media_root / "properties" / "RTF-001" / "linked.jpg"
    symlink_path.symlink_to(outside_media)

    _insert_media(database_path, "RTF-001", "photo", enabled_media, True)
    _insert_media(database_path, "RTF-001", "photo", disabled_media, False)
    _insert_media(database_path, "RTF-001", "photo", outside_media, True)
    _insert_media(database_path, "RTF-001", "photo", secret_file, True)
    _insert_media(database_path, "RTF-001", "photo", symlink_path, True)

    completed = _run_cli(["backup", "--output-dir", str(output_dir), "--name", "smoke"], tmp_path, database_path, media_root)

    assert "Created verified Prosper backup:" in completed.stdout
    archive_path = output_dir / "smoke.tar.gz"
    assert archive_path.is_file()
    assert verify_backup(archive_path)["manifest_version"] == 1

    with tarfile.open(archive_path, "r:gz") as archive:
        names = sorted(archive.getnames())
        manifest = json.loads(archive.extractfile("manifest.json").read().decode("utf-8"))  # type: ignore[union-attr]
        backed_up_database = archive.extractfile("database/prosper.sqlite3").read()  # type: ignore[union-attr]

    assert "database/prosper.sqlite3" in names
    assert "media/properties/RTF-001/enabled.jpg" in names
    assert "media/properties/RTF-001/disabled.jpg" in names
    assert "media/properties/RTF-001/.env" not in names
    assert "media/properties/RTF-001/linked.jpg" not in names
    assert ".env" not in names
    assert "bridge/auth/creds.json" not in names
    assert ".cache/state.json" not in names
    assert "build/bundle.js" not in names
    assert "prosper.log" not in names
    assert "old-backup.tar.gz" not in names
    assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)

    media_files = {entry["archive_path"]: entry for entry in manifest["files"] if entry["role"] == "property_media"}
    assert media_files["media/properties/RTF-001/enabled.jpg"]["enabled"] is True
    assert media_files["media/properties/RTF-001/disabled.jpg"]["enabled"] is False
    assert manifest["database"]["schema_revision"]["kind"] == "sqlite_user_version"
    assert manifest["database"]["schema_revision"]["schema_sha256"]
    assert {item["reason"] for item in manifest["skipped_media"]} == {
        "excluded_secret_name",
        "outside_media_root",
        "symlink_not_followed",
    }

    snapshot_path = tmp_path / "snapshot.sqlite3"
    snapshot_path.write_bytes(backed_up_database)
    with sqlite3.connect(snapshot_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT count(*) FROM property_media").fetchone()[0] == 5


def test_cli_backup_failure_leaves_no_complete_archive(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    _run_cli(["init-db"], tmp_path, database_path, media_root)
    output_dir_file = runtime / "backups"
    output_dir_file.write_text("not a directory")

    failed = _run_cli(
        ["backup", "--output-dir", str(output_dir_file), "--name", "smoke"],
        tmp_path,
        database_path,
        media_root,
        check=False,
    )

    assert failed.returncode != 0
    assert not (runtime / "smoke.tar.gz").exists()
    assert not list(runtime.glob("*.incomplete"))


def test_cli_restore_replaces_data_and_preserves_rollback_snapshot(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    output_dir = runtime / "backups"
    _run_cli(["init-db"], tmp_path, database_path, media_root)

    backed_up_media = media_root / "properties" / "RTF-050" / "living.jpg"
    backed_up_media.parent.mkdir(parents=True)
    backed_up_media.write_bytes(b"backup media")
    _insert_restore_fixture(database_path, "backup", backed_up_media)
    _run_cli(["backup", "--output-dir", str(output_dir), "--name", "restore-source"], tmp_path, database_path, media_root)
    archive_path = output_dir / "restore-source.tar.gz"

    replaced_media = media_root / "properties" / "RTF-999" / "old.jpg"
    replaced_media.parent.mkdir(parents=True, exist_ok=True)
    replaced_media.write_bytes(b"old media")
    bridge_credentials = runtime / "bridge" / "auth" / "creds.json"
    bridge_credentials.parent.mkdir(parents=True)
    bridge_credentials.write_text("current pairing credentials")
    _replace_restore_fixture(database_path, "active", replaced_media)

    completed = _run_cli(
        ["restore", str(archive_path), "--confirm-restore"],
        tmp_path,
        database_path,
        media_root,
    )

    assert "Restored Prosper backup:" in completed.stdout
    assert "WhatsApp pairing credentials are not included; re-pairing may be required." in completed.stdout
    assert _count_rows(database_path, "contacts") == 1
    assert _count_rows(database_path, "conversations") == 1
    assert _count_rows(database_path, "messages") == 1
    assert _count_rows(database_path, "stage_runs") == 1
    assert _count_rows(database_path, "properties") == 1
    assert _count_rows(database_path, "property_playbooks") == 1
    assert _count_rows(database_path, "property_media") == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT display_name FROM contacts").fetchone()[0] == "backup tenant"
        assert connection.execute("SELECT text FROM messages").fetchone()[0] == "backup message"
        assert connection.execute("SELECT property_name FROM properties").fetchone()[0] == "backup listing"
        assert connection.execute("SELECT initial_reply_blocks FROM property_playbooks").fetchone()[0] == '[{"text":"backup"}]'
    assert backed_up_media.read_bytes() == b"backup media"
    assert not replaced_media.exists()
    assert bridge_credentials.read_text() == "current pairing credentials"

    rollbacks = sorted((runtime / "restore-rollbacks").glob("prosper-rollback-*"))
    assert len(rollbacks) == 1
    rollback_database = rollbacks[0] / "database" / "prosper.sqlite3"
    rollback_media = rollbacks[0] / "media" / "properties" / "RTF-999" / "old.jpg"
    assert rollback_database.is_file()
    assert rollback_media.read_bytes() == b"old media"
    with sqlite3.connect(rollback_database) as connection:
        assert connection.execute("SELECT display_name FROM contacts").fetchone()[0] == "active tenant"


def test_cli_restore_validation_failure_leaves_current_data_unchanged(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    output_dir = runtime / "backups"
    _run_cli(["init-db"], tmp_path, database_path, media_root)

    current_media = media_root / "properties" / "RTF-999" / "current.jpg"
    current_media.parent.mkdir(parents=True)
    current_media.write_bytes(b"current media")
    _replace_restore_fixture(database_path, "current", current_media)

    _run_cli(["backup", "--output-dir", str(output_dir), "--name", "corrupt-source"], tmp_path, database_path, media_root)
    archive_path = output_dir / "corrupt-source.tar.gz"
    corrupt_archive = output_dir / "corrupt.tar.gz"
    corrupt_archive.write_bytes(archive_path.read_bytes()[:-20] + b"corrupt")

    failed = _run_cli(
        ["restore", str(corrupt_archive), "--confirm-restore"],
        tmp_path,
        database_path,
        media_root,
        check=False,
    )

    assert failed.returncode != 0
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT display_name FROM contacts").fetchone()[0] == "current tenant"
    assert current_media.read_bytes() == b"current media"
    assert not (runtime / "restore-rollbacks").exists()


def test_cli_restore_incomplete_archive_leaves_current_data_unchanged(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    output_dir = runtime / "backups"
    _run_cli(["init-db"], tmp_path, database_path, media_root)

    current_media = media_root / "properties" / "RTF-999" / "current.jpg"
    current_media.parent.mkdir(parents=True)
    current_media.write_bytes(b"current media")
    _replace_restore_fixture(database_path, "current", current_media)

    incomplete_archive = output_dir / "missing-manifest.tar.gz"
    incomplete_archive.parent.mkdir(parents=True)
    with tarfile.open(incomplete_archive, "w:gz") as archive:
        archive.add(database_path, arcname="database/prosper.sqlite3", recursive=False)

    failed = _run_cli(
        ["restore", str(incomplete_archive), "--confirm-restore"],
        tmp_path,
        database_path,
        media_root,
        check=False,
    )

    assert failed.returncode != 0
    assert "missing required file: manifest.json" in failed.stderr
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT display_name FROM contacts").fetchone()[0] == "current tenant"
    assert current_media.read_bytes() == b"current media"
    assert not (runtime / "restore-rollbacks").exists()


def test_cli_restore_requires_explicit_confirmation_before_changing_data(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    output_dir = runtime / "backups"
    _run_cli(["init-db"], tmp_path, database_path, media_root)

    backed_up_media = media_root / "properties" / "RTF-050" / "backup.jpg"
    backed_up_media.parent.mkdir(parents=True)
    backed_up_media.write_bytes(b"backup")
    _insert_restore_fixture(database_path, "backup", backed_up_media)
    _run_cli(["backup", "--output-dir", str(output_dir), "--name", "needs-confirmation"], tmp_path, database_path, media_root)

    current_media = media_root / "properties" / "RTF-999" / "current.jpg"
    current_media.parent.mkdir(parents=True, exist_ok=True)
    current_media.write_bytes(b"current")
    _replace_restore_fixture(database_path, "current", current_media)

    failed = _run_cli(
        ["restore", str(output_dir / "needs-confirmation.tar.gz")],
        tmp_path,
        database_path,
        media_root,
        check=False,
    )

    assert failed.returncode != 0
    assert "Confirmation required" in failed.stderr
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT display_name FROM contacts").fetchone()[0] == "current tenant"
    assert current_media.read_bytes() == b"current"
    assert not (runtime / "restore-rollbacks").exists()


def test_cli_restore_refuses_when_prosper_service_port_is_listening(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    output_dir = runtime / "backups"
    _run_cli(["init-db"], tmp_path, database_path, media_root)
    _run_cli(["backup", "--output-dir", str(output_dir), "--name", "service-check"], tmp_path, database_path, media_root)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        failed = _run_cli(
            ["restore", str(output_dir / "service-check.tar.gz"), "--confirm-restore"],
            tmp_path,
            database_path,
            media_root,
            check=False,
            service_ports=[port],
        )

    assert failed.returncode != 0
    assert "Prosper services appear active" in failed.stderr


def test_cli_cleanup_removes_only_selected_operational_data_and_backup_archives(tmp_path):
    runtime = tmp_path / "runtime"
    database_path = runtime / "prosper.sqlite3"
    media_root = runtime / "media"
    output_dir = runtime / "backups"
    _run_cli(["init-db"], tmp_path, database_path, media_root)
    managed_media = media_root / "properties" / "RTF-050" / "cleanup.jpg"
    managed_media.parent.mkdir(parents=True)
    managed_media.write_bytes(b"managed")
    bridge_credentials = runtime / "bridge" / "auth" / "creds.json"
    bridge_credentials.parent.mkdir(parents=True)
    bridge_credentials.write_text("pairing credentials")
    _run_cli(["backup", "--output-dir", str(output_dir), "--name", "remove-me"], tmp_path, database_path, media_root)
    _run_cli(["backup", "--output-dir", str(output_dir), "--name", "keep-me"], tmp_path, database_path, media_root)

    data_cleanup = _run_cli(
        ["cleanup-data", "--database", "--media", "--confirm-cleanup"],
        tmp_path,
        database_path,
        media_root,
    )
    archive_cleanup = _run_cli(
        ["cleanup-backups", "--backup-dir", str(output_dir), "remove-me.tar.gz", "--confirm-cleanup"],
        tmp_path,
        database_path,
        media_root,
    )

    assert "Removed database" in data_cleanup.stdout
    assert "Removed managed property media" in data_cleanup.stdout
    assert "Removed backup archive" in archive_cleanup.stdout
    assert not database_path.exists()
    assert not (media_root / "properties").exists()
    assert bridge_credentials.read_text() == "pairing credentials"
    assert not (output_dir / "remove-me.tar.gz").exists()
    assert (output_dir / "keep-me.tar.gz").is_file()


def _run_cli(
    args: list[str],
    cwd: Path,
    database_path: Path,
    media_root: Path,
    *,
    check: bool = True,
    service_ports: list[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT_DIR / "backend"),
        "DATABASE_URL": f"sqlite:///{database_path}",
        "MEDIA_ROOT": str(media_root),
        "PROSPER_ENV_FILE": str(cwd / ".env.missing"),
        "PROSPER_RESTORE_SERVICE_PORTS": "" if service_ports is None else ",".join(str(port) for port in service_ports),
    }
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *args],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def _insert_media(database_path: Path, property_id: str, media_type: str, file_path: Path, enabled: bool) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO property_media (property_id, media_type, file_path, caption, sort_order, enabled)
            VALUES (?, ?, ?, '', 0, ?)
            """,
            (property_id, media_type, str(file_path), enabled),
        )
        connection.commit()


def _insert_restore_fixture(database_path: Path, label: str, media_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM messages")
        connection.execute("DELETE FROM stage_runs")
        connection.execute("DELETE FROM conversations")
        connection.execute("DELETE FROM contacts")
        connection.execute("DELETE FROM property_media")
        connection.execute("DELETE FROM property_playbooks")
        connection.execute("DELETE FROM properties")
        cursor = connection.execute(
            """
            INSERT INTO contacts (chat_jid, display_name, phone, status)
            VALUES (?, ?, ?, 'active')
            """,
            (f"{label}@s.whatsapp.net", f"{label} tenant", f"+65{len(label)}"),
        )
        contact_id = cursor.lastrowid
        cursor = connection.execute(
            """
            INSERT INTO conversations (contact_id, source, status, current_stage, matched_property_id)
            VALUES (?, 'whatsapp', 'active', 'triage', 'RTF-050')
            """,
            (contact_id,),
        )
        conversation_id = cursor.lastrowid
        connection.execute(
            """
            INSERT INTO messages (conversation_id, chat_jid, sender_jid, message_id, direction, source, text, timestamp_ms)
            VALUES (?, ?, ?, ?, 'inbound', 'whatsapp', ?, ?)
            """,
            (conversation_id, f"{label}@s.whatsapp.net", f"{label}@s.whatsapp.net", f"{label}-message", f"{label} message", 123),
        )
        connection.execute(
            """
            INSERT INTO stage_runs (conversation_id, stage, input_snapshot, output_json, status, model)
            VALUES (?, 'triage', ?, ?, 'succeeded', 'test-model')
            """,
            (conversation_id, f"{label} input", f'{{"label":"{label}"}}'),
        )
        connection.execute(
            """
            INSERT INTO properties (property_id, property_name, status, tenant_facing_caveats)
            VALUES ('RTF-050', ?, 'available', '')
            """,
            (f"{label} listing",),
        )
        connection.execute(
            """
            INSERT INTO property_playbooks (property_id, initial_reply_blocks, enabled)
            VALUES ('RTF-050', ?, 1)
            """,
            (f'[{{"text":"{label}"}}]',),
        )
        connection.execute(
            """
            INSERT INTO property_media (property_id, media_type, file_path, caption, sort_order, enabled)
            VALUES ('RTF-050', 'photo', ?, '', 0, 1)
            """,
            (str(media_path),),
        )
        connection.commit()


def _replace_restore_fixture(database_path: Path, label: str, media_path: Path) -> None:
    _insert_restore_fixture(database_path, label, media_path)


def _count_rows(database_path: Path, table: str) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
