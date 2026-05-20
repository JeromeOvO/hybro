import pytest

from platform_module import PlatformConfig, PlatformDeps
from common.errors import FileStoragePlatformError
from platform_module.files import PlatformFileStorage


class FakeObjectStorage:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.puts.append((key, data, content_type))
        return key

    async def get_presigned_url(self, key: str, ttl: int = 3600) -> str:
        return f"https://files.example/{key}?ttl={ttl}"

    async def delete(self, key: str) -> bool:
        self.deleted.append(key)
        return True


class FakeFileMetadataRepository:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.fail_create = fail_create
        self.created: list[dict] = []
        self.records: dict[str, dict] = {}
        self.deleted: list[str] = []

    async def create(self, data: dict) -> str:
        if self.fail_create:
            raise RuntimeError("metadata unavailable")
        self.created.append(data)
        self.records[data["file_id"]] = data
        return data["file_id"]

    async def get(self, file_id: str) -> dict | None:
        return self.records.get(file_id)

    async def delete(self, file_id: str) -> bool:
        self.deleted.append(file_id)
        return self.records.pop(file_id, None) is not None

    async def list_for_room(self, room_id: str) -> list[dict]:
        return [
            record
            for record in self.records.values()
            if record.get("room_id") == room_id
        ]


def _storage(
    *,
    max_upload_size_bytes: int = 1024,
    metadata_repository: FakeFileMetadataRepository | None = None,
    object_storage: FakeObjectStorage | None = None,
) -> tuple[PlatformFileStorage, FakeObjectStorage, FakeFileMetadataRepository]:
    objects = object_storage or FakeObjectStorage()
    metadata = metadata_repository or FakeFileMetadataRepository()
    service = PlatformFileStorage(
        config=PlatformConfig(max_upload_size_bytes=max_upload_size_bytes),
        deps=PlatformDeps(
            object_storage=objects,
            file_metadata_repository=metadata,
            file_id_factory=lambda: "file-1",
        ),
    )
    return service, objects, metadata


async def test_upload_stores_object_and_metadata_then_returns_presigned_url():
    service, objects, metadata = _storage()

    result = await service.upload(
        b"\x89PNG\r\n\x1a\n",
        "image.png",
        owner_id="user-1",
        room_id="room-1",
        content_type="image/png",
    )

    assert result.file_id == "file-1"
    assert result.url == "https://files.example/uploads/room-1/file-1/image.png?ttl=3600"
    assert objects.puts == [
        ("uploads/room-1/file-1/image.png", b"\x89PNG\r\n\x1a\n", "image/png")
    ]
    assert metadata.created[0] | {
        "uploaded_at": metadata.created[0]["uploaded_at"],
    } == {
        "file_id": "file-1",
        "room_id": "room-1",
        "user_id": "user-1",
        "s3_key": "uploads/room-1/file-1/image.png",
        "mime_type": "image/png",
        "file_name": "image.png",
        "size_bytes": 8,
        "uploaded_at": metadata.created[0]["uploaded_at"],
    }


async def test_upload_rejects_unsupported_mime_type():
    service, objects, metadata = _storage()

    with pytest.raises(FileStoragePlatformError) as exc_info:
        await service.upload(
            b"MZ",
            "program.exe",
            owner_id="user-1",
            room_id="room-1",
            content_type="application/x-msdownload",
        )

    assert exc_info.value.status_code == 415
    assert objects.puts == []
    assert metadata.created == []


async def test_upload_rejects_oversized_file():
    service, objects, metadata = _storage(max_upload_size_bytes=4)

    with pytest.raises(FileStoragePlatformError) as exc_info:
        await service.upload(
            b"\x89PNG\r\n\x1a\n",
            "image.png",
            owner_id="user-1",
            room_id="room-1",
            content_type="image/png",
        )

    assert exc_info.value.status_code == 413
    assert objects.puts == []
    assert metadata.created == []


async def test_upload_rejects_magic_byte_mismatch():
    service, objects, metadata = _storage()

    with pytest.raises(FileStoragePlatformError) as exc_info:
        await service.upload(
            b"\x89PNG\r\n\x1a\n",
            "file.pdf",
            owner_id="user-1",
            room_id="room-1",
            content_type="application/pdf",
        )

    assert exc_info.value.status_code == 422
    assert objects.puts == []
    assert metadata.created == []


async def test_upload_rejects_same_family_magic_byte_mismatch():
    service, objects, metadata = _storage()

    with pytest.raises(FileStoragePlatformError) as exc_info:
        await service.upload(
            b"\x89PNG\r\n\x1a\n",
            "image.jpg",
            owner_id="user-1",
            room_id="room-1",
            content_type="image/jpeg",
        )

    assert exc_info.value.status_code == 422
    assert objects.puts == []
    assert metadata.created == []


@pytest.mark.parametrize(
    ("declared", "content"),
    [
        ("audio/mp4", b"\x00\x00\x00\x18ftyp" + b"\x00" * 8),
        ("audio/webm", b"\x1aE\xdf\xa3" + b"\x00" * 8),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK\x03\x04" + b"\x00" * 8,
        ),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK\x03\x04" + b"\x00" * 8,
        ),
    ],
)
async def test_upload_allows_compatible_container_mime_types(
    declared: str, content: bytes
):
    service, objects, _metadata = _storage()

    result = await service.upload(
        content,
        "container.bin",
        owner_id="user-1",
        room_id="room-1",
        content_type=declared,
    )

    assert result.mime_type == declared
    assert objects.puts[0][2] == declared


async def test_upload_deletes_object_when_metadata_write_fails():
    metadata = FakeFileMetadataRepository(fail_create=True)
    service, objects, _metadata = _storage(metadata_repository=metadata)

    with pytest.raises(FileStoragePlatformError) as exc_info:
        await service.upload(
            b"\x89PNG\r\n\x1a\n",
            "image.png",
            owner_id="user-1",
            room_id="room-1",
            content_type="image/png",
        )

    assert exc_info.value.status_code == 500
    assert objects.deleted == ["uploads/room-1/file-1/image.png"]


async def test_get_url_uses_metadata_key_and_requested_ttl():
    service, _objects, metadata = _storage()
    await service.upload(
        b"\x89PNG\r\n\x1a\n",
        "image.png",
        owner_id="user-1",
        room_id="room-1",
        content_type="image/png",
    )

    assert (
        await service.get_url("file-1", ttl=42)
        == "https://files.example/uploads/room-1/file-1/image.png?ttl=42"
    )
    assert await service.get_url("missing") is None
    assert metadata.deleted == []


async def test_delete_removes_object_then_metadata():
    service, objects, metadata = _storage()
    await service.upload(
        b"\x89PNG\r\n\x1a\n",
        "image.png",
        owner_id="user-1",
        room_id="room-1",
        content_type="image/png",
    )

    assert await service.delete("file-1") is True
    assert objects.deleted == ["uploads/room-1/file-1/image.png"]
    assert metadata.deleted == ["file-1"]
    assert await service.delete("missing") is False


async def test_list_for_room_returns_file_info_records():
    service, _objects, _metadata = _storage()
    await service.upload(
        b"\x89PNG\r\n\x1a\n",
        "image.png",
        owner_id="user-1",
        room_id="room-1",
        content_type="image/png",
    )

    files = await service.list_for_room("room-1")

    assert len(files) == 1
    assert files[0].file_id == "file-1"
    assert files[0].file_name == "image.png"
    assert await service.list_for_room("room-2") == []
