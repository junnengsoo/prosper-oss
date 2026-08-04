from io import BytesIO
from pathlib import Path

import pytest

from app.config import get_settings
from app.media_storage import delete_stored_file, describe_media_storage, store_uploaded_file


@pytest.fixture
def local_media_root(tmp_path, monkeypatch):
    root = tmp_path / "media"
    monkeypatch.setenv("MEDIA_ROOT", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def test_store_uploaded_file_uses_safe_runtime_path(local_media_root):
    result = store_uploaded_file(
        BytesIO(b"image-bytes"),
        property_id=" RTF 023! ",
        filename="../Living Room 01.JPG",
    )

    path = Path(result.file_path)
    assert path.is_file()
    assert path.parent.parent.name == "properties"
    assert path.parent.name == "RTF-023"
    assert path.read_bytes() == b"image-bytes"

    descriptor = describe_media_storage(result)
    assert descriptor.local_file_exists is True
    assert descriptor.sendable is True
    assert descriptor.send_url == result.file_path


def test_store_uploaded_file_rejects_files_over_limit(local_media_root):
    with pytest.raises(ValueError, match="exceeds limit"):
        store_uploaded_file(BytesIO(b"12345"), property_id="RTF-023", filename="large.jpg", max_bytes=4)

    assert list(local_media_root.rglob("*.uploading")) == []
    assert list(local_media_root.rglob("*.jpg")) == []


def test_delete_stored_file_only_deletes_managed_files(local_media_root, tmp_path):
    result = store_uploaded_file(BytesIO(b"image"), property_id="RTF-023", filename="living.jpg")
    assert delete_stored_file(result.file_path) is True
    assert not Path(result.file_path).exists()
    assert delete_stored_file(result.file_path) is False

    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"do not delete")
    with pytest.raises(ValueError, match="inside the configured media root"):
        delete_stored_file(str(outside))
    assert outside.exists()
