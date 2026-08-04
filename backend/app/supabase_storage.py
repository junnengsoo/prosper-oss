from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import mimetypes
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .config import get_settings


DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_SIGNED_URL_TTL_SECONDS = 60 * 60 * 24
MAX_OBJECT_PATH_LENGTH = 1024


class SupabaseStorageError(RuntimeError):
    """Raised when Supabase Storage upload or signing fails."""


class ResponseLike(Protocol):
    text: str

    def json(self) -> Any: ...

    def raise_for_status(self) -> None: ...


class SupabaseStorageHttpClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> ResponseLike: ...

    def delete(self, url: str, **kwargs: Any) -> ResponseLike: ...


@dataclass(frozen=True)
class SupabaseStorageConfig:
    supabase_url: str
    service_role_key: str
    bucket: str
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    signed_url_ttl_seconds: int | None = DEFAULT_SIGNED_URL_TTL_SECONDS
    public_bucket: bool = False
    upsert: bool = False
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class SupabaseStorageUpload:
    file_path: str
    storage_provider: str
    storage_bucket: str
    storage_object_path: str
    signed_url: str | None
    signed_url_expires_at: datetime | None
    public_url: str | None

    def as_property_media_values(self) -> dict[str, object]:
        return {
            "file_path": self.file_path,
            "storage_provider": self.storage_provider,
            "storage_bucket": self.storage_bucket,
            "storage_object_path": self.storage_object_path,
            "signed_url": self.signed_url,
            "signed_url_expires_at": self.signed_url_expires_at,
            "public_url": self.public_url,
        }


def supabase_storage_config_from_settings() -> SupabaseStorageConfig:
    settings = get_settings()
    service_role_key = settings.supabase_secret_key
    return SupabaseStorageConfig(
        supabase_url=getattr(settings, "supabase_url", ""),
        service_role_key=service_role_key,
        bucket=getattr(settings, "supabase_storage_bucket", "property-media"),
        max_upload_bytes=getattr(settings, "supabase_max_upload_bytes", DEFAULT_MAX_UPLOAD_BYTES),
    )


def build_property_media_object_path(property_id: str, file_path: str | Path) -> str:
    filename = _safe_storage_filename(Path(file_path).name)
    return normalize_storage_object_path(f"{_safe_storage_segment(property_id)}/{filename}")


def normalize_storage_object_path(object_path: str) -> str:
    normalized = object_path.strip()
    if not normalized:
        raise ValueError("storage object path must not be blank")
    if len(normalized) > MAX_OBJECT_PATH_LENGTH:
        raise ValueError(f"storage object path must be {MAX_OBJECT_PATH_LENGTH} characters or fewer")
    if normalized.startswith("/") or "\\" in normalized:
        raise ValueError("storage object path must be relative and use forward slashes")
    if "?" in normalized or "#" in normalized:
        raise ValueError("storage object path must not include query strings or fragments")
    if any(ord(char) < 32 for char in normalized):
        raise ValueError("storage object path must not include control characters")

    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("storage object path must not include empty, current, or parent segments")
    return normalized


def upload_file_to_supabase_storage(
    file_path: str | Path,
    object_path: str,
    *,
    config: SupabaseStorageConfig | None = None,
    client: SupabaseStorageHttpClient | None = None,
    content_type: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> SupabaseStorageUpload:
    storage_config = config or supabase_storage_config_from_settings()
    _validate_storage_config(storage_config)
    local_path = _validate_upload_file(file_path, storage_config.max_upload_bytes)
    normalized_object_path = normalize_storage_object_path(object_path)
    resolved_content_type = content_type or mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"

    if client is None:
        with httpx.Client() as http_client:
            return _upload_file_with_client(
                local_path,
                normalized_object_path,
                storage_config,
                http_client,
                resolved_content_type,
                clock,
            )

    return _upload_file_with_client(
        local_path,
        normalized_object_path,
        storage_config,
        client,
        resolved_content_type,
        clock,
    )


def delete_file_from_supabase_storage(
    object_path: str,
    *,
    config: SupabaseStorageConfig | None = None,
    client: SupabaseStorageHttpClient | None = None,
) -> None:
    storage_config = config or supabase_storage_config_from_settings()
    _validate_storage_config(storage_config)
    normalized_object_path = normalize_storage_object_path(object_path)

    if client is None:
        with httpx.Client() as http_client:
            _delete_file_with_client(normalized_object_path, storage_config, http_client)
            return

    _delete_file_with_client(normalized_object_path, storage_config, client)


def create_signed_url_for_supabase_object(
    object_path: str,
    *,
    config: SupabaseStorageConfig | None = None,
    client: SupabaseStorageHttpClient | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[str, datetime]:
    storage_config = config or supabase_storage_config_from_settings()
    _validate_storage_config(storage_config)
    normalized_object_path = normalize_storage_object_path(object_path)
    base_url = _normalize_supabase_url(storage_config.supabase_url)
    bucket = storage_config.bucket.strip()

    if client is None:
        with httpx.Client() as http_client:
            return _create_signed_url(http_client, base_url, bucket, normalized_object_path, storage_config, clock)

    return _create_signed_url(client, base_url, bucket, normalized_object_path, storage_config, clock)


def _upload_file_with_client(
    local_path: Path,
    object_path: str,
    config: SupabaseStorageConfig,
    client: SupabaseStorageHttpClient,
    content_type: str,
    clock: Callable[[], datetime] | None,
) -> SupabaseStorageUpload:
    base_url = _normalize_supabase_url(config.supabase_url)
    bucket = config.bucket.strip()
    upload_url = f"{base_url}/storage/v1/object/{_quote_path(bucket)}/{_quote_path(object_path)}"
    headers = {
        "Authorization": f"Bearer {config.service_role_key}",
        "apikey": config.service_role_key,
        "Content-Type": content_type,
        "x-upsert": "true" if config.upsert else "false",
    }

    with local_path.open("rb") as upload_file:
        upload_response = client.post(
            upload_url,
            headers=headers,
            content=upload_file,
            timeout=config.timeout_seconds,
        )
    _raise_response_for_status(upload_response, "upload")

    signed_url = None
    signed_url_expires_at = None
    if config.signed_url_ttl_seconds is not None:
        signed_url, signed_url_expires_at = _create_signed_url(client, base_url, bucket, object_path, config, clock)

    public_url = None
    if config.public_bucket:
        public_url = f"{base_url}/storage/v1/object/public/{_quote_path(bucket)}/{_quote_path(object_path)}"

    return SupabaseStorageUpload(
        file_path=str(local_path),
        storage_provider="supabase",
        storage_bucket=bucket,
        storage_object_path=object_path,
        signed_url=signed_url,
        signed_url_expires_at=signed_url_expires_at,
        public_url=public_url,
    )


def _delete_file_with_client(
    object_path: str,
    config: SupabaseStorageConfig,
    client: SupabaseStorageHttpClient,
) -> None:
    base_url = _normalize_supabase_url(config.supabase_url)
    bucket = config.bucket.strip()
    delete_url = f"{base_url}/storage/v1/object/{_quote_path(bucket)}/{_quote_path(object_path)}"
    response = client.delete(
        delete_url,
        headers={
            "Authorization": f"Bearer {config.service_role_key}",
            "apikey": config.service_role_key,
        },
        timeout=config.timeout_seconds,
    )
    if getattr(response, "status_code", None) == 404:
        return
    _raise_response_for_status(response, "delete")


def _create_signed_url(
    client: SupabaseStorageHttpClient,
    base_url: str,
    bucket: str,
    object_path: str,
    config: SupabaseStorageConfig,
    clock: Callable[[], datetime] | None,
) -> tuple[str, datetime]:
    ttl_seconds = config.signed_url_ttl_seconds
    if ttl_seconds is None or ttl_seconds <= 0:
        raise ValueError("signed URL TTL must be positive when signing is enabled")

    sign_url = f"{base_url}/storage/v1/object/sign/{_quote_path(bucket)}/{_quote_path(object_path)}"
    response = client.post(
        sign_url,
        headers={
            "Authorization": f"Bearer {config.service_role_key}",
            "apikey": config.service_role_key,
            "Content-Type": "application/json",
        },
        json={"expiresIn": ttl_seconds},
        timeout=config.timeout_seconds,
    )
    _raise_response_for_status(response, "signed URL")

    body = _response_json(response, "signed URL")
    signed_url = body.get("signedURL") or body.get("signedUrl") or body.get("signed_url")
    if not isinstance(signed_url, str) or not signed_url.strip():
        raise SupabaseStorageError("Supabase signed URL response did not include a signed URL")

    now = (clock or _utc_now)()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return _absolute_supabase_url(base_url, signed_url.strip()), now + timedelta(seconds=ttl_seconds)


def _validate_storage_config(config: SupabaseStorageConfig) -> None:
    if not config.supabase_url.strip():
        raise ValueError("supabase_url must be configured")
    if not config.service_role_key.strip():
        raise ValueError("Supabase secret key must be configured")
    if not config.bucket.strip():
        raise ValueError("Supabase storage bucket must be configured")
    if "/" in config.bucket.strip():
        raise ValueError("Supabase storage bucket must not include slashes")
    if config.max_upload_bytes <= 0:
        raise ValueError("max_upload_bytes must be positive")


def _validate_upload_file(file_path: str | Path, max_upload_bytes: int) -> Path:
    local_path = Path(file_path).expanduser()
    if not local_path.is_file():
        raise ValueError(f"upload file does not exist: {local_path}")
    size = local_path.stat().st_size
    if size > max_upload_bytes:
        raise ValueError(f"upload file is {size} bytes, exceeding limit of {max_upload_bytes} bytes")
    return local_path


def _raise_response_for_status(response: ResponseLike, operation: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        detail = response.text[:1200]
        raise SupabaseStorageError(f"Supabase Storage {operation} failed: {detail}") from error


def _response_json(response: ResponseLike, operation: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise SupabaseStorageError(f"Supabase Storage {operation} response was not JSON") from error
    if not isinstance(body, dict):
        raise SupabaseStorageError(f"Supabase Storage {operation} response was not an object")
    return body


def _normalize_supabase_url(value: str) -> str:
    return value.strip().rstrip("/")


def _absolute_supabase_url(base_url: str, value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/object/"):
        return f"{base_url}/storage/v1{value}"
    if value.startswith("object/"):
        return f"{base_url}/storage/v1/{value}"
    return f"{base_url}/{value.lstrip('/')}"


def _quote_path(value: str) -> str:
    return "/".join(quote(part, safe="") for part in value.split("/"))


def _safe_storage_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    segment = segment.strip(".-_")
    if not segment:
        raise ValueError("storage path segment must contain at least one safe character")
    return segment


def _safe_storage_filename(value: str) -> str:
    filename = _safe_storage_segment(value)
    if "." not in filename:
        return filename
    stem, suffix = filename.rsplit(".", 1)
    if not stem or not suffix:
        raise ValueError("storage filename must include a valid name and extension")
    return filename


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
