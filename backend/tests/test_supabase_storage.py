from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.supabase_storage import (
    SupabaseStorageConfig,
    SupabaseStorageError,
    build_property_media_object_path,
    delete_file_from_supabase_storage,
    upload_file_to_supabase_storage,
)


class FakeSupabaseResponse:
    def __init__(self, body=None, status_code: int = 200, text: str = "", method: str = "POST"):
        self.body = body if body is not None else {}
        self.status_code = status_code
        self.text = text
        self.method = method

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request(self.method, "https://example.supabase.co/storage/v1/object/test")
            response = httpx.Response(self.status_code, text=self.text, request=request)
            raise httpx.HTTPStatusError("Supabase error", request=request, response=response)


class FakeSupabaseClient:
    def __init__(self, signed_url_body=None, upload_status_code: int = 200, delete_status_code: int = 200):
        self.calls = []
        self.signed_url_body = signed_url_body or {"signedURL": "/object/sign/property-media/RTF-023/living.jpg?token=abc"}
        self.upload_status_code = upload_status_code
        self.delete_status_code = delete_status_code

    def post(self, url, **kwargs):
        body = None
        if kwargs.get("content") is not None:
            body = kwargs["content"].read()
        self.calls.append(SimpleNamespace(method="POST", url=url, kwargs=kwargs, body=body))
        if "/storage/v1/object/sign/" in url:
            return FakeSupabaseResponse(self.signed_url_body)
        return FakeSupabaseResponse(status_code=self.upload_status_code, text="upload failed")

    def delete(self, url, **kwargs):
        self.calls.append(SimpleNamespace(method="DELETE", url=url, kwargs=kwargs, body=None))
        return FakeSupabaseResponse(status_code=self.delete_status_code, text="delete failed", method="DELETE")


def test_build_property_media_object_path_sanitizes_property_and_filename(tmp_path):
    media_path = tmp_path / "Living Room 01.JPG"

    assert build_property_media_object_path(" RTF 023! ", media_path) == "RTF-023/Living-Room-01.JPG"


def test_upload_file_to_supabase_storage_uploads_and_returns_media_values(tmp_path):
    media_path = tmp_path / "living room.jpg"
    media_path.write_bytes(b"fake image bytes")
    client = FakeSupabaseClient()
    now = datetime(2026, 7, 4, 8, 30, tzinfo=timezone.utc)
    config = SupabaseStorageConfig(
        supabase_url="https://example.supabase.co/",
        service_role_key="service-role-key",
        bucket="property-media",
        max_upload_bytes=100,
        signed_url_ttl_seconds=600,
        public_bucket=True,
        upsert=True,
    )

    result = upload_file_to_supabase_storage(
        media_path,
        "RTF-023/living room.jpg",
        config=config,
        client=client,
        clock=lambda: now,
    )

    assert len(client.calls) == 2
    assert client.calls[0].url == "https://example.supabase.co/storage/v1/object/property-media/RTF-023/living%20room.jpg"
    assert client.calls[0].body == b"fake image bytes"
    assert client.calls[0].kwargs["headers"]["Authorization"] == "Bearer service-role-key"
    assert client.calls[0].kwargs["headers"]["apikey"] == "service-role-key"
    assert client.calls[0].kwargs["headers"]["Content-Type"] == "image/jpeg"
    assert client.calls[0].kwargs["headers"]["x-upsert"] == "true"
    assert client.calls[1].url == "https://example.supabase.co/storage/v1/object/sign/property-media/RTF-023/living%20room.jpg"
    assert client.calls[1].kwargs["json"] == {"expiresIn": 600}
    assert result.storage_provider == "supabase"
    assert result.file_path == str(media_path)
    assert result.storage_bucket == "property-media"
    assert result.storage_object_path == "RTF-023/living room.jpg"
    assert result.signed_url == "https://example.supabase.co/storage/v1/object/sign/property-media/RTF-023/living.jpg?token=abc"
    assert result.signed_url_expires_at == datetime(2026, 7, 4, 8, 40, tzinfo=timezone.utc)
    assert result.public_url == "https://example.supabase.co/storage/v1/object/public/property-media/RTF-023/living%20room.jpg"
    assert result.as_property_media_values()["storage_provider"] == "supabase"


def test_upload_file_to_supabase_storage_can_skip_signing_for_public_bucket(tmp_path):
    media_path = tmp_path / "tour.mp4"
    media_path.write_bytes(b"fake video")
    client = FakeSupabaseClient()
    config = SupabaseStorageConfig(
        supabase_url="https://example.supabase.co",
        service_role_key="service-role-key",
        bucket="property-media",
        signed_url_ttl_seconds=None,
        public_bucket=True,
    )

    result = upload_file_to_supabase_storage(media_path, "RTF-023/tour.mp4", config=config, client=client)

    assert len(client.calls) == 1
    assert result.signed_url is None
    assert result.signed_url_expires_at is None
    assert result.public_url == "https://example.supabase.co/storage/v1/object/public/property-media/RTF-023/tour.mp4"


@pytest.mark.parametrize("object_path", ["/absolute.jpg", "../escape.jpg", "RTF-023//living.jpg", "RTF-023\\living.jpg"])
def test_upload_file_to_supabase_storage_rejects_unsafe_object_paths(tmp_path, object_path):
    media_path = tmp_path / "living.jpg"
    media_path.write_bytes(b"fake image")
    client = FakeSupabaseClient()
    config = SupabaseStorageConfig(
        supabase_url="https://example.supabase.co",
        service_role_key="service-role-key",
        bucket="property-media",
    )

    with pytest.raises(ValueError):
        upload_file_to_supabase_storage(media_path, object_path, config=config, client=client)

    assert client.calls == []


def test_upload_file_to_supabase_storage_rejects_files_over_limit(tmp_path):
    media_path = tmp_path / "large.jpg"
    media_path.write_bytes(b"too large")
    client = FakeSupabaseClient()
    config = SupabaseStorageConfig(
        supabase_url="https://example.supabase.co",
        service_role_key="service-role-key",
        bucket="property-media",
        max_upload_bytes=3,
    )

    with pytest.raises(ValueError, match="exceeding limit"):
        upload_file_to_supabase_storage(media_path, "RTF-023/large.jpg", config=config, client=client)

    assert client.calls == []


def test_upload_file_to_supabase_storage_wraps_supabase_upload_errors(tmp_path):
    media_path = tmp_path / "living.jpg"
    media_path.write_bytes(b"fake image")
    client = FakeSupabaseClient(upload_status_code=500)
    config = SupabaseStorageConfig(
        supabase_url="https://example.supabase.co",
        service_role_key="service-role-key",
        bucket="property-media",
    )

    with pytest.raises(SupabaseStorageError, match="upload failed"):
        upload_file_to_supabase_storage(media_path, "RTF-023/living.jpg", config=config, client=client)


def test_delete_file_from_supabase_storage_deletes_object():
    client = FakeSupabaseClient()
    config = SupabaseStorageConfig(
        supabase_url="https://example.supabase.co/",
        service_role_key="service-role-key",
        bucket="property-media",
    )

    delete_file_from_supabase_storage("RTF-023/living room.jpg", config=config, client=client)

    assert len(client.calls) == 1
    assert client.calls[0].method == "DELETE"
    assert client.calls[0].url == "https://example.supabase.co/storage/v1/object/property-media/RTF-023/living%20room.jpg"
    assert client.calls[0].kwargs["headers"]["Authorization"] == "Bearer service-role-key"
    assert client.calls[0].kwargs["headers"]["apikey"] == "service-role-key"


def test_delete_file_from_supabase_storage_treats_missing_object_as_deleted():
    client = FakeSupabaseClient(delete_status_code=404)
    config = SupabaseStorageConfig(
        supabase_url="https://example.supabase.co",
        service_role_key="service-role-key",
        bucket="property-media",
    )

    delete_file_from_supabase_storage("RTF-023/missing.jpg", config=config, client=client)

    assert len(client.calls) == 1


def test_delete_file_from_supabase_storage_wraps_supabase_delete_errors():
    client = FakeSupabaseClient(delete_status_code=500)
    config = SupabaseStorageConfig(
        supabase_url="https://example.supabase.co",
        service_role_key="service-role-key",
        bucket="property-media",
    )

    with pytest.raises(SupabaseStorageError, match="delete failed"):
        delete_file_from_supabase_storage("RTF-023/living.jpg", config=config, client=client)
