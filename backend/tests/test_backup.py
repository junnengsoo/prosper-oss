from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
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


def _run_cli(
    args: list[str],
    cwd: Path,
    database_path: Path,
    media_root: Path,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT_DIR / "backend"),
        "DATABASE_URL": f"sqlite:///{database_path}",
        "MEDIA_ROOT": str(media_root),
        "PROSPER_ENV_FILE": str(cwd / ".env.missing"),
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
