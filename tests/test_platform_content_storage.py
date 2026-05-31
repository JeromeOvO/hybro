from datetime import UTC, datetime, timedelta

import pytest

from models.compaction import ContentReference, StorageType
from platform_module import PlatformConfig, PlatformDeps
from platform_module.content_storage import (
    ContentExpiredError,
    PlatformContentStorage,
    hash_content,
)


class FakeContentStorageRepository:
    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.by_document_id: dict[str, dict] = {}
        self.deleted_turns: list[tuple[str, str]] = []
        self.deleted_rooms: list[str] = []

    async def upsert_full_content(self, **kwargs) -> str:
        self.upserts.append(kwargs)
        self.by_document_id[kwargs["document_id"]] = kwargs
        return kwargs["document_id"]

    async def get_content_by_document_id(self, document_id: str) -> dict | None:
        return self.by_document_id.get(document_id)

    async def get_content_by_turn_id(self, room_id: str, turn_id: str) -> dict | None:
        for record in self.by_document_id.values():
            if record["room_id"] == room_id and record["turn_id"] == turn_id:
                return record
        return None

    async def delete_content_by_turn_id(self, room_id: str, turn_id: str) -> bool:
        self.deleted_turns.append((room_id, turn_id))
        return True

    async def delete_content_by_room_id(self, room_id: str) -> int:
        self.deleted_rooms.append(room_id)
        return 3

    async def get_content_stats_for_room(self, room_id: str) -> dict:
        return {"room_id": room_id, "total_documents": 2}

    async def text_search(self, room_id: str, query: str, limit: int = 50) -> list[dict]:
        return []

    async def hydrate_turn_notes(self, room_id: str, turn_ids: list[str]) -> list[dict]:
        return []


class FakeObjectTextStorage:
    def __init__(self, objects: dict[str, str | None]) -> None:
        self.objects = objects
        self.reads: list[str] = []

    async def download_text(self, key: str) -> str | None:
        self.reads.append(key)
        return self.objects.get(key)


class FakeObjectProtocolStorage:
    def __init__(self, objects: dict[str, str | None]) -> None:
        self.objects = objects
        self.reads: list[str] = []

    async def get_text(self, key: str) -> str | None:
        self.reads.append(key)
        return self.objects.get(key)


def _service(
    *,
    repository: FakeContentStorageRepository | None = None,
    object_storage: FakeObjectTextStorage | None = None,
    ttl_seconds: int = 0,
) -> tuple[PlatformContentStorage, FakeContentStorageRepository]:
    repo = repository or FakeContentStorageRepository()
    service = PlatformContentStorage(
        config=PlatformConfig(content_storage_ttl_seconds=ttl_seconds),
        deps=PlatformDeps(
            content_storage_repository=repo,
            object_storage=object_storage,
            clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    return service, repo


def test_hash_content_is_deterministic():
    assert hash_content("same") == hash_content("same")
    assert hash_content("same") != hash_content("different")


async def test_upsert_full_content_writes_hash_and_optional_expiry():
    service, repo = _service(ttl_seconds=60)

    document_id = await service.upsert_full_content(
        room_id="room-1",
        turn_id="turn-1",
        content="full content",
        content_type="text",
        turn_notes={"topic": "billing"},
    )

    assert document_id == "conversation_content:room-1:turn-1"
    assert repo.upserts == [
        {
            "document_id": "conversation_content:room-1:turn-1",
            "room_id": "room-1",
            "turn_id": "turn-1",
            "content": "full content",
            "content_type": "text",
            "content_hash": hash_content("full content"),
            "stored_at": datetime(2026, 1, 1, tzinfo=UTC),
            "expires_at": datetime(2026, 1, 1, tzinfo=UTC)
            + timedelta(seconds=60),
            "turn_notes": {"topic": "billing"},
        }
    ]


async def test_get_content_by_document_and_turn_id_returns_content_only():
    service, _repo = _service()
    document_id = await service.upsert_full_content(
        room_id="room-1",
        turn_id="turn-1",
        content="full content",
        content_type="text",
    )

    assert await service.get_content_by_document_id(document_id) == "full content"
    assert await service.get_content_by_turn_id("room-1", "turn-1") == "full content"
    assert await service.get_content_by_document_id("missing") is None


async def test_expired_mongodb_content_is_not_hydrated_even_before_ttl_cleanup():
    service, repo = _service(ttl_seconds=60)
    expired = {
        "document_id": "conversation_content:room-1:turn-1",
        "room_id": "room-1",
        "turn_id": "turn-1",
        "content": "expired content",
        "expires_at": datetime(2025, 12, 31, 23, 59, tzinfo=UTC),
    }
    repo.by_document_id[expired["document_id"]] = expired
    content_ref = ContentReference(
        storage_type=StorageType.MONGODB,
        collection="conversation_content",
        document_id=expired["document_id"],
        created_at=datetime.now(UTC),
    )

    assert await service.get_content_by_document_id(expired["document_id"]) is None
    assert await service.get_content_by_turn_id("room-1", "turn-1") is None
    with pytest.raises(ContentExpiredError) as exc_info:
        await service.expand_content_reference(content_ref, "turn-1")
    assert exc_info.value.document_id == expired["document_id"]


async def test_expand_mongodb_reference_uses_repository_document():
    service, _repo = _service()
    document_id = await service.upsert_full_content(
        room_id="room-1",
        turn_id="turn-1",
        content="expanded content",
        content_type="text",
    )
    content_ref = ContentReference(
        storage_type=StorageType.MONGODB,
        collection="conversation_content",
        document_id=document_id,
        created_at=datetime.now(UTC),
    )

    assert (
        await service.expand_content_reference(content_ref, "turn-1")
        == "expanded content"
    )


async def test_expand_mongodb_reference_missing_document_raises_expired():
    service, _repo = _service()
    content_ref = ContentReference(
        storage_type=StorageType.MONGODB,
        collection="conversation_content",
        document_id="missing-doc",
        created_at=datetime.now(UTC),
    )

    with pytest.raises(ContentExpiredError) as exc_info:
        await service.expand_content_reference(content_ref, "turn-1")

    assert exc_info.value.turn_id == "turn-1"
    assert exc_info.value.document_id == "missing-doc"


async def test_expand_s3_reference_uses_injected_object_storage():
    objects = FakeObjectTextStorage({"objects/content.txt": "s3 content"})
    service, _repo = _service(object_storage=objects)
    content_ref = ContentReference(
        storage_type=StorageType.S3,
        s3_bucket="bucket",
        s3_key="objects/content.txt",
        created_at=datetime.now(UTC),
    )

    assert await service.expand_content_reference(content_ref, "turn-1") == "s3 content"
    assert objects.reads == ["objects/content.txt"]


async def test_expand_s3_reference_supports_object_storage_protocol_get_text():
    objects = FakeObjectProtocolStorage({"objects/content.txt": "s3 content"})
    service, _repo = _service(object_storage=objects)
    content_ref = ContentReference(
        storage_type=StorageType.S3,
        s3_bucket="bucket",
        s3_key="objects/content.txt",
        created_at=datetime.now(UTC),
    )

    assert await service.expand_content_reference(content_ref, "turn-1") == "s3 content"
    assert objects.reads == ["objects/content.txt"]


async def test_expand_s3_reference_missing_key_or_object_fails():
    service, _repo = _service(object_storage=FakeObjectTextStorage({}))
    missing_key = ContentReference(
        storage_type=StorageType.S3,
        s3_bucket="bucket",
        created_at=datetime.now(UTC),
    )
    missing_object = ContentReference(
        storage_type=StorageType.S3,
        s3_bucket="bucket",
        s3_key="missing",
        created_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="no s3_key"):
        await service.expand_content_reference(missing_key, "turn-1")
    with pytest.raises(ContentExpiredError) as exc_info:
        await service.expand_content_reference(missing_object, "turn-1")
    assert exc_info.value.document_id == "missing"


async def test_url_reference_remains_blocked():
    service, _repo = _service()
    content_ref = ContentReference(
        storage_type=StorageType.URL,
        url="https://example.com/content.txt",
        created_at=datetime.now(UTC),
    )

    with pytest.raises(NotImplementedError):
        await service.expand_content_reference(content_ref, "turn-1")


async def test_delete_and_stats_delegate_to_repository():
    service, repo = _service()

    assert await service.delete_content_by_turn_id("room-1", "turn-1") is True
    assert await service.delete_content_by_room_id("room-1") == 3
    assert await service.get_content_stats_for_room("room-1") == {
        "room_id": "room-1",
        "total_documents": 2,
    }
    assert repo.deleted_turns == [("room-1", "turn-1")]
    assert repo.deleted_rooms == ["room-1"]
