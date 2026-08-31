from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
from typing import Any

from sqlalchemy.engine import make_url

from .config import RUNTIME_DIR, ROOT_DIR, get_settings


MANIFEST_VERSION = 1
DATABASE_ARCHIVE_PATH = "database/prosper.sqlite3"
MANIFEST_ARCHIVE_PATH = "manifest.json"
EXCLUDED_MEDIA_ROOT_PARTS = {
    ".cache",
    "__pycache__",
    "backups",
    "build",
    "cache",
    "dist",
    "logs",
    "node_modules",
}
EXCLUDED_MEDIA_FILENAMES = {
    ".env",
    ".env.local",
    ".env.prod",
    "creds.json",
    "credentials.json",
    "session.json",
}
EXCLUDED_MEDIA_NAME_FRAGMENTS = (
    "api_key",
    "apikey",
    "bridge-token",
    "bridge_token",
    "pairing",
    "session-secret",
    "session_secret",
    "whatsapp-creds",
    "whatsapp_creds",
)


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    manifest: dict[str, Any]


def create_backup(output_dir: Path | None = None, *, name: str | None = None) -> BackupResult:
    settings = get_settings()
    source_database = _sqlite_database_path(settings.database_url)
    if not source_database.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_database}")

    destination_dir = (output_dir or (RUNTIME_DIR / "backups")).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    archive_name = _backup_archive_name(name)
    final_archive = destination_dir / archive_name
    if final_archive.exists():
        raise FileExistsError(f"Backup archive already exists: {final_archive}")

    incomplete_archive = final_archive.with_name(f".{final_archive.name}.incomplete")
    incomplete_archive.unlink(missing_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="prosper-backup-") as temporary_root:
            temporary = Path(temporary_root)
            snapshot_path = temporary / "prosper.sqlite3"
            _copy_sqlite_snapshot(source_database, snapshot_path)

            created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            schema_revision = _sqlite_schema_revision(snapshot_path)
            media_root = settings.media_root.expanduser().resolve()
            media_entries, skipped_media = _property_media_entries(snapshot_path, media_root)

            file_entries = [_file_manifest_entry(snapshot_path, DATABASE_ARCHIVE_PATH, "sqlite_database")]
            for media_path, archive_path, metadata in media_entries:
                file_entries.append(_file_manifest_entry(media_path, archive_path, "property_media", metadata))

            manifest: dict[str, Any] = {
                "manifest_version": MANIFEST_VERSION,
                "backup_type": "prosper_backup",
                "created_at": created_at,
                "source": {
                    "database": str(source_database),
                    "media_root": str(media_root),
                },
                "database": {
                    "archive_path": DATABASE_ARCHIVE_PATH,
                    "schema_revision": schema_revision,
                },
                "files": file_entries,
                "skipped_media": skipped_media,
            }

            manifest_path = temporary / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

            with tarfile.open(incomplete_archive, "w:gz", dereference=False) as archive:
                archive.add(manifest_path, arcname=MANIFEST_ARCHIVE_PATH, recursive=False)
                archive.add(snapshot_path, arcname=DATABASE_ARCHIVE_PATH, recursive=False)
                for media_path, archive_path, _metadata in media_entries:
                    archive.add(media_path, arcname=archive_path, recursive=False)

            verify_backup(incomplete_archive)
            incomplete_archive.replace(final_archive)
            return BackupResult(archive_path=final_archive, manifest=manifest)
    except Exception:
        incomplete_archive.unlink(missing_ok=True)
        final_archive.unlink(missing_ok=True)
        raise


def verify_backup(archive_path: Path) -> dict[str, Any]:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        _validate_archive_members(members)
        manifest_member = archive.getmember(MANIFEST_ARCHIVE_PATH)
        manifest_file = archive.extractfile(manifest_member)
        if manifest_file is None:
            raise ValueError("Backup manifest is not readable")
        manifest = json.loads(manifest_file.read().decode("utf-8"))

        expected_files = {entry["archive_path"]: entry for entry in manifest.get("files", [])}
        regular_members = {member.name for member in members if member.isfile()}
        if regular_members != {MANIFEST_ARCHIVE_PATH, *expected_files}:
            raise ValueError("Backup archive contents do not match manifest inventory")

        for archive_name, entry in expected_files.items():
            member = archive.getmember(archive_name)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"Backup file is not readable: {archive_name}")
            payload = extracted.read()
            if len(payload) != entry["size_bytes"]:
                raise ValueError(f"Backup file size mismatch: {archive_name}")
            checksum = hashlib.sha256(payload).hexdigest()
            if checksum != entry["sha256"]:
                raise ValueError(f"Backup file checksum mismatch: {archive_name}")

        database_member = archive.getmember(DATABASE_ARCHIVE_PATH)
        database_file = archive.extractfile(database_member)
        if database_file is None:
            raise ValueError("Backup database snapshot is not readable")
        with tempfile.TemporaryDirectory(prefix="prosper-backup-verify-") as temporary_root:
            database_path = Path(temporary_root) / "prosper.sqlite3"
            database_path.write_bytes(database_file.read())
            _assert_sqlite_integrity(database_path)

        return manifest


def _sqlite_database_path(database_url: str) -> Path:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        raise ValueError("Prosper backups support SQLite databases only")
    if not url.database or url.database == ":memory:":
        raise ValueError("Prosper backups require a file-backed SQLite database")
    path = Path(url.database).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def _backup_archive_name(name: str | None) -> str:
    if name:
        archive_name = Path(name).name
        if archive_name != name:
            raise ValueError("Backup archive name must not include a directory")
        if not archive_name.endswith(".tar.gz"):
            archive_name = f"{archive_name}.tar.gz"
        return archive_name
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"prosper-backup-{stamp}.tar.gz"


def _copy_sqlite_snapshot(source_database: Path, snapshot_path: Path) -> None:
    source_uri = f"file:{source_database.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source:
        source.execute("PRAGMA busy_timeout=30000")
        with sqlite3.connect(snapshot_path) as destination:
            source.backup(destination)
            destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _sqlite_schema_revision(database_path: Path) -> dict[str, Any]:
    with sqlite3.connect(database_path) as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        rows = connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE sql IS NOT NULL
            ORDER BY type, name, tbl_name
            """
        ).fetchall()
    schema_payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "kind": "sqlite_user_version",
        "user_version": user_version,
        "application_id": application_id,
        "schema_sha256": hashlib.sha256(schema_payload).hexdigest(),
    }


def _property_media_entries(database_path: Path, media_root: Path) -> tuple[list[tuple[Path, str, dict[str, Any]]], list[dict[str, Any]]]:
    rows = _property_media_rows(database_path)
    entries: list[tuple[Path, str, dict[str, Any]]] = []
    skipped: list[dict[str, Any]] = []
    seen_archive_paths: set[str] = set()

    for row in rows:
        media_id, property_id, media_type, file_path, enabled = row
        reason = _media_skip_reason(file_path, media_root)
        if reason:
            skipped.append(_skipped_media(media_id, property_id, file_path, reason))
            continue

        source_path = Path(file_path).expanduser().resolve()
        relative_path = source_path.relative_to(media_root)
        archive_path = f"media/{relative_path.as_posix()}"
        if archive_path in seen_archive_paths:
            skipped.append(_skipped_media(media_id, property_id, file_path, "duplicate_archive_path"))
            continue
        seen_archive_paths.add(archive_path)
        entries.append(
            (
                source_path,
                archive_path,
                {
                    "media_id": media_id,
                    "property_id": property_id,
                    "media_type": media_type,
                    "enabled": bool(enabled),
                    "source_path": str(source_path),
                },
            )
        )

    entries.sort(key=lambda item: item[1])
    skipped.sort(key=lambda item: (item["property_id"], item["media_id"], item["reason"]))
    return entries, skipped


def _property_media_rows(database_path: Path) -> list[tuple[int, str, str, str, int]]:
    with sqlite3.connect(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'property_media'"
        ).fetchone()
        if not exists:
            return []
        return list(
            connection.execute(
                """
                SELECT id, property_id, media_type, file_path, enabled
                FROM property_media
                ORDER BY property_id, sort_order, id
                """
            ).fetchall()
        )


def _media_skip_reason(file_path: str, media_root: Path) -> str | None:
    if not file_path or not file_path.strip():
        return "blank_path"
    candidate_path = Path(file_path).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = ROOT_DIR / candidate_path
    try:
        candidate_path.relative_to(media_root)
    except ValueError:
        return "outside_media_root"
    if _has_symlink_component(candidate_path, media_root):
        return "symlink_not_followed"
    source_path = candidate_path.resolve()
    relative_path = source_path.relative_to(media_root)
    if not relative_path.parts:
        return "media_root_not_file"
    if not source_path.is_file():
        return "missing_file"
    if not relative_path.parts or relative_path.parts[0] != "properties":
        return "outside_managed_property_media"
    if any(part in EXCLUDED_MEDIA_ROOT_PARTS for part in relative_path.parts):
        return "excluded_runtime_path"
    lower_name = source_path.name.lower()
    if lower_name in EXCLUDED_MEDIA_FILENAMES or lower_name.startswith(".env."):
        return "excluded_secret_name"
    if any(fragment in lower_name for fragment in EXCLUDED_MEDIA_NAME_FRAGMENTS):
        return "excluded_secret_name"
    return None


def _has_symlink_component(path: Path, media_root: Path) -> bool:
    try:
        relative = path.relative_to(media_root)
    except ValueError:
        return True
    current = media_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _skipped_media(media_id: int, property_id: str, file_path: str, reason: str) -> dict[str, Any]:
    return {
        "media_id": media_id,
        "property_id": property_id,
        "file_path": file_path,
        "reason": reason,
    }


def _file_manifest_entry(
    source_path: Path,
    archive_path: str,
    role: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "archive_path": archive_path,
        "role": role,
        "size_bytes": source_path.stat().st_size,
        "sha256": _sha256_file(source_path),
        **(metadata or {}),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_archive_members(members: list[tarfile.TarInfo]) -> None:
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe archive member path: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Backup archive must not contain links: {member.name}")


def _assert_sqlite_integrity(database_path: Path) -> None:
    with sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise ValueError("Backup SQLite snapshot failed integrity_check")
