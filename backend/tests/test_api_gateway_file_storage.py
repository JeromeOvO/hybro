from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api_gateway.file_storage import ObjectStorageFileStorage
from common.errors import FileStoragePlatformError


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


class RecordingObjectStorage:
    def __init__(self):
        self.put_calls = []
        self.presign_calls = []
        self.delete_calls = []
        self.get_bytes_calls = []

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.put_calls.append((key, data, content_type))
        return key

    async def get_presigned_url(
        self, key: str, ttl: int = 3600, filename: str | None = None
    ) -> str:
        self.presign_calls.append((key, ttl, filename))
        return f"https://signed.example/{key}?ttl={ttl}"

    async def delete(self, key: str) -> bool:
        self.delete_calls.append(key)
        return True

    async def get_bytes(self, key: str, *, max_bytes: int) -> bytes | None:
        self.get_bytes_calls.append((key, max_bytes))
        return b"stored-bytes"


class RecordingFileUploadsCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])
        self.find_one_queries = []
        self.find_queries = []
        self.delete_queries = []

    async def insert_one(self, doc):
        self.docs.append(doc)
        return SimpleNamespace(inserted_id="inserted")

    async def find_one(self, query):
        self.find_one_queries.append(query)
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None

    def find(self, query):
        self.find_queries.append(query)
        docs = [
            doc
            for doc in self.docs
            if all(doc.get(key) == value for key, value in query.items())
        ]
        return AsyncCursor(docs)

    async def delete_one(self, query):
        self.delete_queries.append(query)
        before = len(self.docs)
        self.docs = [
            doc
            for doc in self.docs
            if not all(doc.get(key) == value for key, value in query.items())
        ]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class FailingObjectStorage:
    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        raise RuntimeError("missing credentials")


@pytest.mark.asyncio
async def test_object_storage_file_storage_uploads_and_records_metadata():
    now = datetime(2026, 6, 30, tzinfo=UTC)
    object_storage = RecordingObjectStorage()
    collection = RecordingFileUploadsCollection()
    storage = ObjectStorageFileStorage(
        object_storage=object_storage,
        file_uploads_collection=collection,
        file_id_factory=lambda: "file-123",
        now=lambda: now,
    )

    uploaded = await storage.upload(
        file_bytes=b"hello",
        filename="../../report.pdf",
        owner_id="user-1",
        room_id="room-1",
        content_type="application/pdf",
    )

    assert object_storage.put_calls == [
        ("uploads/room-1/file-123/report.pdf", b"hello", "application/pdf")
    ]
    assert object_storage.presign_calls == [
        ("uploads/room-1/file-123/report.pdf", 3600, "report.pdf")
    ]
    assert collection.docs == [
        {
            "file_id": "file-123",
            "room_id": "room-1",
            "user_id": "user-1",
            "s3_key": "uploads/room-1/file-123/report.pdf",
            "mime_type": "application/pdf",
            "file_name": "report.pdf",
            "size_bytes": 5,
            "uploaded_at": now,
        }
    ]
    assert uploaded.file_id == "file-123"
    assert uploaded.file_name == "report.pdf"
    assert uploaded.mime_type == "application/pdf"
    assert uploaded.size_bytes == 5
    assert uploaded.url == "https://signed.example/uploads/room-1/file-123/report.pdf?ttl=3600"


@pytest.mark.asyncio
async def test_object_storage_file_storage_reads_and_deletes_existing_uploads():
    object_storage = RecordingObjectStorage()
    collection = RecordingFileUploadsCollection(
        [
            {
                "file_id": "file-1",
                "room_id": "room-1",
                "user_id": "user-1",
                "s3_key": "uploads/room-1/file-1/a.txt",
                "mime_type": "text/plain",
                "file_name": "a.txt",
                "size_bytes": 3,
            },
            {
                "file_id": "file-2",
                "room_id": "room-2",
                "user_id": "user-1",
                "s3_key": "uploads/room-2/file-2/b.txt",
                "mime_type": "text/plain",
                "file_name": "b.txt",
                "size_bytes": 4,
            },
        ]
    )
    storage = ObjectStorageFileStorage(
        object_storage=object_storage,
        file_uploads_collection=collection,
    )

    url = await storage.get_url("file-1", ttl=60)
    listed = await storage.list_for_room("room-1")
    deleted = await storage.delete("file-1")

    assert url == "https://signed.example/uploads/room-1/file-1/a.txt?ttl=60"
    assert object_storage.presign_calls == [
        ("uploads/room-1/file-1/a.txt", 60, "a.txt")
    ]
    assert [item.file_id for item in listed] == ["file-1"]
    assert object_storage.delete_calls == ["uploads/room-1/file-1/a.txt"]
    assert collection.delete_queries == [{"file_id": "file-1"}]
    assert deleted is True


@pytest.mark.asyncio
async def test_object_storage_file_storage_reads_bytes_by_storage_key():
    object_storage = RecordingObjectStorage()
    storage = ObjectStorageFileStorage(
        object_storage=object_storage,
        file_uploads_collection=RecordingFileUploadsCollection(),
    )

    data = await storage.get_bytes("uploads/room/file/report.pdf", max_bytes=1024)

    assert data == b"stored-bytes"
    assert object_storage.get_bytes_calls == [
        ("uploads/room/file/report.pdf", 1024)
    ]


@pytest.mark.asyncio
async def test_object_storage_file_storage_logs_underlying_upload_failure(caplog):
    storage = ObjectStorageFileStorage(
        object_storage=FailingObjectStorage(),
        file_uploads_collection=RecordingFileUploadsCollection(),
        file_id_factory=lambda: "file-123",
    )

    with pytest.raises(FileStoragePlatformError):
        await storage.upload(
            file_bytes=b"hello",
            filename="report.pdf",
            owner_id="user-1",
            room_id="room-1",
            content_type="application/pdf",
        )

    assert "File storage upload failed" in caplog.text
    assert "missing credentials" in caplog.text
