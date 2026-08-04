from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class MediaStorageRecord(Protocol):
    file_path: str
    storage_provider: str
    storage_bucket: str | None
    storage_object_path: str | None
    signed_url: str | None
    signed_url_expires_at: datetime | None
    public_url: str | None


@dataclass(frozen=True)
class MediaStorageDescriptor:
    provider: str
    file_path: str
    storage_bucket: str | None
    storage_object_path: str | None
    signed_url: str | None
    signed_url_expires_at: datetime | None
    public_url: str | None
    local_file_exists: bool
    send_url: str
    display_reference: str
    sendable: bool

    @property
    def uses_remote_url(self) -> bool:
        return self.send_url != self.file_path


def normalize_storage_provider(value: str | None) -> str:
    provider = (value or "local").strip().lower()
    return provider or "local"


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def describe_media_storage(record: MediaStorageRecord) -> MediaStorageDescriptor:
    provider = normalize_storage_provider(getattr(record, "storage_provider", None))
    file_path = (getattr(record, "file_path", "") or "").strip()
    storage_bucket = normalize_optional_text(getattr(record, "storage_bucket", None))
    storage_object_path = normalize_optional_text(getattr(record, "storage_object_path", None))
    signed_url = normalize_optional_text(getattr(record, "signed_url", None))
    signed_url_expires_at = getattr(record, "signed_url_expires_at", None)
    public_url = normalize_optional_text(getattr(record, "public_url", None))

    usable_signed_url = signed_url if signed_url and not signed_url_is_expired(signed_url_expires_at) else None
    send_url = usable_signed_url or public_url or file_path
    local_file_exists = bool(file_path) and Path(file_path).is_file()
    sendable = bool(usable_signed_url or public_url or local_file_exists)

    return MediaStorageDescriptor(
        provider=provider,
        file_path=file_path,
        storage_bucket=storage_bucket,
        storage_object_path=storage_object_path,
        signed_url=signed_url,
        signed_url_expires_at=signed_url_expires_at,
        public_url=public_url,
        local_file_exists=local_file_exists,
        send_url=send_url,
        display_reference=media_display_reference(
            provider=provider,
            file_path=file_path,
            storage_bucket=storage_bucket,
            storage_object_path=storage_object_path,
            signed_url=signed_url,
            public_url=public_url,
        ),
        sendable=sendable,
    )


def signed_url_is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    now = datetime.now(timezone.utc)
    comparable = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    return comparable <= now


def media_display_reference(
    *,
    provider: str,
    file_path: str,
    storage_bucket: str | None,
    storage_object_path: str | None,
    signed_url: str | None,
    public_url: str | None,
) -> str:
    if provider != "local" and storage_bucket and storage_object_path:
        return f"{provider}://{storage_bucket}/{storage_object_path}"
    if public_url:
        return public_url
    if signed_url:
        return f"{provider or 'remote'} signed URL"
    return file_path
