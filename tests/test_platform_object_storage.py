from __future__ import annotations

from io import BytesIO

import pytest

from common.errors import ObjectStorageError
from platform_module.object_storage import PlatformObjectStorage


class FakeObjectStorageDAL:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.put_files: list[tuple[str, object, str, int | None]] = []
        self.presigned: list[tuple[str, int, str | None]] = []
        self.deletes: list[str] = []
        self.deleted_prefixes: list[str] = []
        self.heads: dict[str, dict | None] = {}
        self.texts: dict[str, str | None] = {}
        self.public_urls: dict[str, str] = {}
        self.delete_error: ObjectStorageError | None = None

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.puts.append((key, data, content_type))
        return key

    async def put_file(
        self,
        key: str,
        file_data,
        content_type: str = "",
        content_length: int | None = None,
    ) -> str:
        self.put_files.append((key, file_data, content_type, content_length))
        return key

    async def get_presigned_url(
        self,
        key: str,
        ttl: int = 3600,
        filename: str | None = None,
    ) -> str:
        self.presigned.append((key, ttl, filename))
        return f"https://files.example/{key}?ttl={ttl}&name={filename or ''}"

    async def delete(self, key: str) -> bool:
        self.deletes.append(key)
        if self.delete_error is not None:
            raise self.delete_error
        return True

    async def head(self, key: str) -> dict | None:
        return self.heads.get(key)

    async def delete_prefix(self, prefix: str) -> int:
        self.deleted_prefixes.append(prefix)
        return 2

    def get_public_url(self, key: str) -> str:
        return self.public_urls.get(key, f"https://public.example/{key}")

    async def get_text(self, key: str) -> str | None:
        return self.texts.get(key)


def test_platform_object_storage_is_exported() -> None:
    import platform_module

    assert platform_module.PlatformObjectStorage is PlatformObjectStorage
    assert "PlatformObjectStorage" in platform_module.__all__


@pytest.mark.asyncio
async def test_upload_file_routes_bytes_and_file_like_inputs() -> None:
    dal = FakeObjectStorageDAL()
    storage = PlatformObjectStorage(dal, default_presigned_url_ttl=90)
    file_like = BytesIO(b"stream")

    raw_result = await storage.upload_file(
        b"bytes",
        "objects/raw.txt",
        "text/plain",
        content_length=5,
    )
    file_result = await storage.upload_file(
        file_like,
        "objects/file.txt",
        "text/plain",
        content_length=6,
    )

    assert raw_result == "objects/raw.txt"
    assert file_result == "objects/file.txt"
    assert dal.puts == [("objects/raw.txt", b"bytes", "text/plain")]
    assert dal.put_files == [("objects/file.txt", file_like, "text/plain", 6)]


@pytest.mark.asyncio
async def test_presigned_url_uses_default_ttl_override_and_filename_cache() -> None:
    now = 1000.0
    storage = PlatformObjectStorage(
        FakeObjectStorageDAL(),
        default_presigned_url_ttl=120,
        clock=lambda: now,
    )
    dal = storage._dal

    first = await storage.generate_presigned_url("objects/report", filename="a.pdf")
    second = await storage.generate_presigned_url("objects/report", filename="a.pdf")
    other_filename = await storage.generate_presigned_url(
        "objects/report",
        filename="b.pdf",
    )
    explicit_ttl = await storage.generate_presigned_url(
        "objects/report",
        filename="a.pdf",
        expires_in=30,
    )

    assert first == second
    assert other_filename != first
    assert explicit_ttl != first
    assert dal.presigned == [
        ("objects/report", 120, "a.pdf"),
        ("objects/report", 120, "b.pdf"),
        ("objects/report", 30, "a.pdf"),
    ]


@pytest.mark.asyncio
async def test_presigned_url_cache_refreshes_after_half_ttl() -> None:
    current_time = 1000.0

    def clock() -> float:
        return current_time

    dal = FakeObjectStorageDAL()
    storage = PlatformObjectStorage(dal, default_presigned_url_ttl=100, clock=clock)

    first = await storage.generate_presigned_url("objects/report")
    current_time = 1049.0
    before_expiry = await storage.generate_presigned_url("objects/report")
    current_time = 1051.0
    after_expiry = await storage.generate_presigned_url("objects/report")

    assert first == before_expiry
    assert after_expiry == first
    assert dal.presigned == [
        ("objects/report", 100, None),
        ("objects/report", 100, None),
    ]


@pytest.mark.asyncio
async def test_batch_presigned_urls_preserves_keys_and_filename_mapping() -> None:
    dal = FakeObjectStorageDAL()
    storage = PlatformObjectStorage(dal, default_presigned_url_ttl=90)

    result = await storage.batch_presigned_urls(
        ["objects/a", "objects/b"],
        filenames={"objects/a": "a.txt"},
        expires_in=45,
    )

    assert result == {
        "objects/a": "https://files.example/objects/a?ttl=45&name=a.txt",
        "objects/b": "https://files.example/objects/b?ttl=45&name=",
    }
    assert dal.presigned == [
        ("objects/a", 45, "a.txt"),
        ("objects/b", 45, None),
    ]


@pytest.mark.asyncio
async def test_upload_and_delete_invalidate_cached_presigned_urls() -> None:
    dal = FakeObjectStorageDAL()
    storage = PlatformObjectStorage(dal, default_presigned_url_ttl=90)

    await storage.generate_presigned_url("objects/a", filename="a.txt")
    await storage.generate_presigned_url("objects/a", filename="b.txt")
    await storage.upload_file(b"new", "objects/a", "text/plain")
    await storage.generate_presigned_url("objects/a", filename="a.txt")
    assert dal.presigned == [
        ("objects/a", 90, "a.txt"),
        ("objects/a", 90, "b.txt"),
        ("objects/a", 90, "a.txt"),
    ]

    await storage.delete_file("objects/a")
    await storage.generate_presigned_url("objects/a", filename="a.txt")
    assert dal.presigned == [
        ("objects/a", 90, "a.txt"),
        ("objects/a", 90, "b.txt"),
        ("objects/a", 90, "a.txt"),
        ("objects/a", 90, "a.txt"),
    ]


@pytest.mark.asyncio
async def test_delete_file_returns_false_only_for_object_storage_error() -> None:
    dal = FakeObjectStorageDAL()
    storage = PlatformObjectStorage(dal, default_presigned_url_ttl=90)
    dal.delete_error = ObjectStorageError("delete failed")

    assert await storage.delete_file("objects/a") is False


@pytest.mark.asyncio
async def test_metadata_prefix_public_url_and_text_methods_delegate_to_dal() -> None:
    dal = FakeObjectStorageDAL()
    dal.heads["objects/a"] = {"content_type": "text/plain"}
    dal.texts["objects/a"] = "hello"
    storage = PlatformObjectStorage(dal, default_presigned_url_ttl=90)

    assert await storage.head_file("objects/a") == {"content_type": "text/plain"}
    assert await storage.head_file("missing") is None
    assert await storage.delete_prefix("objects/") == 2
    assert storage.get_public_url("objects/a") == "https://public.example/objects/a"
    assert await storage.download_text("objects/a") == "hello"
    assert dal.deleted_prefixes == ["objects/"]
