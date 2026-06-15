from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeFileMetadataRepository:
    records: dict[str, dict]
    deleted_by_room: list[str]

    def __init__(self) -> None:
        self.records = {}
        self.deleted_by_room: list[str] = []
        self.deleted: list[str] = []

    async def create(self, data: dict) -> str: ...

    async def get(self, file_id: str) -> dict | None:
        return self.records.get(file_id)

    async def delete(self, file_id: str) -> bool: ...

    async def list_for_room(self, room_id: str) -> list[dict]: ...

    async def delete_for_room(self, room_id: str) -> int:
        self.deleted_by_room.append(room_id)
        deleted = 0
        keep: dict[str, dict] = {}
        for file_id, record in self.records.items():
            if record.get("room_id") == room_id:
                deleted += 1
            else:
                keep[file_id] = record
        self.records = keep
        return deleted


async def test_platform_attachment_metadata_reader_filters_by_room():
    from platform_module.attachments import PlatformAttachmentMetadataReader

    repo = FakeFileMetadataRepository()
    repo.records = {
        "file-1": {"file_id": "file-1", "room_id": "room-1"},
        "file-2": {"file_id": "file-2", "room_id": "room-2"},
    }
    reader = PlatformAttachmentMetadataReader(repo)

    assert await reader.get_for_room_file("room-1", "file-1") is not None
    assert await reader.get_for_room_file("room-1", "file-2") is None


async def test_platform_attachment_cleanup_for_room_forwards_to_metadata_repo():
    from platform_module.attachments import PlatformAttachmentCleanupPort

    repo = FakeFileMetadataRepository()
    repo.records = {
        "a": {"file_id": "a", "room_id": "room-1"},
        "b": {"file_id": "b", "room_id": "room-1"},
        "c": {"file_id": "c", "room_id": "room-2"},
    }
    cleanup = PlatformAttachmentCleanupPort(repo)

    deleted = await cleanup.delete_for_room("room-1")

    assert deleted == 2
    assert repo.deleted_by_room == ["room-1"]
    assert set(repo.records) == {"c"}
