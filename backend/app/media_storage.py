from __future__ import annotations

from dataclasses import dataclass
import mimetypes
from pathlib import Path
import re
import secrets
from typing import BinaryIO, Protocol

from .config import get_settings


class MediaStorageRecord(Protocol):
    file_path: str


@dataclass(frozen=True)
class StoredMediaFile:
    file_path: str


@dataclass(frozen=True)
class MediaStorageDescriptor:
    file_path: str
    local_file_exists: bool
    send_url: str
    display_reference: str
    sendable: bool


def media_root() -> Path:
    root = get_settings().media_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_component(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return normalized or fallback


def _safe_filename(value: str) -> str:
    name = Path(value or "property-media").name
    stem = _safe_component(Path(name).stem, "property-media")
    suffix = Path(name).suffix.lower()
    return f"{stem}{suffix}"


def store_uploaded_file(
    source: BinaryIO,
    *,
    property_id: str,
    filename: str,
    max_bytes: int | None = None,
) -> StoredMediaFile:
    target_dir = media_root() / "properties" / _safe_component(property_id, "property")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{secrets.token_hex(8)}-{_safe_filename(filename)}"
    temporary = target.with_suffix(f"{target.suffix}.uploading")
    limit = max_bytes if max_bytes is not None else get_settings().media_max_upload_bytes
    written_bytes = 0
    try:
        with temporary.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                written_bytes += len(chunk)
                if written_bytes > limit:
                    raise ValueError(f"upload file exceeds limit of {limit} bytes")
                output.write(chunk)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return StoredMediaFile(file_path=str(target))


def _path_inside_media_root(file_path: str | Path) -> Path:
    path = Path(file_path).expanduser().resolve()
    try:
        path.relative_to(media_root())
    except ValueError as error:
        raise ValueError("media file must be inside the configured media root") from error
    return path


def delete_stored_file(file_path: str) -> bool:
    path = _path_inside_media_root(file_path)
    if not path.exists():
        return False
    if not path.is_file():
        raise ValueError("media path is not a file")
    path.unlink()
    return True


def describe_media_storage(record: MediaStorageRecord) -> MediaStorageDescriptor:
    file_path = (record.file_path or "").strip()
    local_file_exists = bool(file_path) and Path(file_path).is_file()
    return MediaStorageDescriptor(
        file_path=file_path,
        local_file_exists=local_file_exists,
        send_url=file_path,
        display_reference=file_path,
        sendable=local_file_exists,
    )


def media_content_type(file_path: str) -> str:
    return mimetypes.guess_type(file_path)[0] or "application/octet-stream"
