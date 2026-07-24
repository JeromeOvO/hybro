from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from common.errors import FileStoragePlatformError
from room_files import MemoryFileContentStore, RoomFiles


class InMemoryCollection:
    def __init__(self):
        self.docs: list[dict] = []

    async def insert_one(self, doc):
        self.docs.append(deepcopy(doc))
        return SimpleNamespace(inserted_id=doc["file_id"])

    async def find_one(self, query):
        for doc in self.docs:
            if _matches(doc, query):
                return deepcopy(doc)
        return None

    async def update_one(self, query, update):
        for doc in self.docs:
            if not _matches(doc, query):
                continue
            for key, value in update.get("$set", {}).items():
                doc[key] = value
            for key, value in update.get("$inc", {}).items():
                doc[key] = doc.get(key, 0) + value
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def delete_one(self, query):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not _matches(doc, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    def find(self, query):
        return AsyncCursor([deepcopy(doc) for doc in self.docs if _matches(doc, query)])


class AsyncCursor:
    def __init__(self, docs):
        self._docs = iter(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._docs)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _matches(doc, query):
    for key, value in query.items():
        actual = doc.get(key)
        if isinstance(value, dict) and "$lt" in value:
            if actual is None or actual >= value["$lt"]:
                return False
        elif actual != value:
            return False
    return True


async def test_room_files_upload_persists_ready_metadata_and_content():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    file_id = uuid4().hex
    collection = InMemoryCollection()
    content_store = MemoryFileContentStore()
    files = RoomFiles(
        metadata=collection,
        content=content_store,
        file_id_factory=lambda: file_id,
        now=lambda: now,
        max_upload_bytes=5 * 1024 * 1024,
    )

    uploaded = await files.upload(
        file_bytes=b"hello",
        filename="../../report.txt",
        owner_id="user-1",
        room_id="room-1",
        content_type="text/plain",
    )

    assert uploaded.file_id == file_id
    assert uploaded.url == f"/api/v1/files/{file_id}/content"
    assert await files.get_bytes(file_id, max_bytes=5) == b"hello"
    assert collection.docs == [
        {
            "file_id": file_id,
            "room_id": "room-1",
            "owner_id": "user-1",
            "source": "user_upload",
            "file_name": "report.txt",
            "mime_type": "text/plain",
            "size_bytes": 5,
            "sha256": (
                "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
            ),
            "status": "ready",
            "version": 2,
            "reference_claims": [],
            "created_at": now,
            "updated_at": now,
        }
    ]


async def test_room_files_normalizes_mime_types_before_persistence():
    collection = InMemoryCollection()
    rooms = InMemoryCollection()
    rooms.docs.append({"room_id": "room-1", "room_owner_id": "user-1"})
    files = RoomFiles(
        metadata=collection,
        content=MemoryFileContentStore(),
        rooms=rooms,
    )

    uploaded = await files.upload(
        file_bytes=b"user",
        filename="user.txt",
        owner_id="user-1",
        room_id="room-1",
        content_type="Text/Plain; charset=UTF-8",
    )
    agent = await files.store_agent_artifact(
        room_id="room-1",
        source_message_id="message-1",
        origin_key="origin-unsafe-mime",
        content=b"agent",
        file_name="agent.txt",
        mime_type="text/plain\r\nX-Injected: yes",
    )

    assert uploaded.mime_type == "text/plain"
    assert agent["mime_type"] == "application/octet-stream"
    assert [doc["mime_type"] for doc in collection.docs] == [
        "text/plain",
        "application/octet-stream",
    ]


async def test_room_files_rejects_upload_before_writing_metadata_or_content():
    collection = InMemoryCollection()
    content_store = MemoryFileContentStore()
    files = RoomFiles(
        metadata=collection,
        content=content_store,
        max_upload_bytes=5,
    )

    with pytest.raises(FileStoragePlatformError) as exc_info:
        await files.upload(
            file_bytes=b"123456",
            filename="large.txt",
            owner_id="user-1",
            room_id="room-1",
            content_type="text/plain",
        )

    assert exc_info.value.status_code == 413
    assert collection.docs == []


async def test_room_files_materializes_agent_artifact_idempotently():
    collection = InMemoryCollection()
    rooms = InMemoryCollection()
    rooms.docs.append({"room_id": "room-1", "room_owner_id": "user-1"})
    content_store = MemoryFileContentStore()
    files = RoomFiles(
        metadata=collection,
        content=content_store,
        rooms=rooms,
    )

    first = await files.store_agent_artifact(
        room_id="room-1",
        source_message_id="message-1",
        origin_key="origin-1",
        content=b"result",
        file_name="result.txt",
        mime_type="text/plain",
    )
    second = await files.store_agent_artifact(
        room_id="room-1",
        source_message_id="message-1",
        origin_key="origin-1",
        content=b"result",
        file_name="result.txt",
        mime_type="text/plain",
    )

    assert first["file_id"] == second["file_id"]
    assert first["source"] == "agent_artifact"
    assert first["owner_id"] == "user-1"
    assert len(collection.docs) == 1


async def test_room_files_deletes_only_superseded_agent_artifacts():
    ids = iter(["a" * 32, "b" * 32])
    collection = InMemoryCollection()
    rooms = InMemoryCollection()
    rooms.docs.append({"room_id": "room-1", "room_owner_id": "user-1"})
    content_store = MemoryFileContentStore()
    files = RoomFiles(
        metadata=collection,
        content=content_store,
        rooms=rooms,
        file_id_factory=lambda: next(ids),
    )
    old = await files.store_agent_artifact(
        room_id="room-1",
        source_message_id="message-1",
        origin_key="origin-old",
        content=b"old",
        file_name="old.txt",
        mime_type="text/plain",
    )
    new = await files.store_agent_artifact(
        room_id="room-1",
        source_message_id="message-1",
        origin_key="origin-new",
        content=b"new",
        file_name="new.txt",
        mime_type="text/plain",
    )

    deleted = await files.delete_superseded_agent_artifacts(
        room_id="room-1",
        source_message_id="message-1",
        file_ids={old["file_id"]},
    )

    assert deleted == 1
    assert await files.get_bytes(old["file_id"], max_bytes=3) is None
    assert await files.get_bytes(new["file_id"], max_bytes=3) == b"new"


async def test_recovery_removes_crash_orphaned_agent_artifact():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    orphan_file_id = "c" * 32
    collection = InMemoryCollection()
    collection.docs.append(
        {
            "file_id": orphan_file_id,
            "room_id": "room-1",
            "owner_id": "user-1",
            "source": "agent_artifact",
            "source_message_id": "message-1",
            "origin_key": "origin-orphan",
            "file_name": "orphan.txt",
            "mime_type": "text/plain",
            "size_bytes": 6,
            "sha256": "sha",
            "status": "ready",
            "version": 2,
            "reference_claims": [],
            "created_at": now - timedelta(hours=25),
            "updated_at": now - timedelta(hours=25),
        }
    )
    content_store = MemoryFileContentStore()
    await content_store.write(orphan_file_id, b"orphan", "text/plain")
    agent_messages = SimpleNamespace(find_one=AsyncMock(return_value=None))
    files = RoomFiles(
        metadata=collection,
        content=content_store,
        agent_messages=agent_messages,
        now=lambda: now,
    )

    recovered = await files._recover_superseded_agent_artifacts(
        now - timedelta(hours=24)
    )

    assert recovered == 1
    assert collection.docs == []
    assert await content_store.read(orphan_file_id, max_bytes=6) is None
