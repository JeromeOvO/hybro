from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.utils.time import utcnow
from jobs.cleanup_orphaned_uploads import (
    OrphanedUploadCleaner,
    OrphanedUploadCleanerDeps,
)


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


def _cleaner_with_deps(
    *,
    docs,
    reference=None,
    storage_deleted=True,
    storage_error: Exception | None = None,
):
    file_uploads = MagicMock()
    file_uploads.find.return_value = AsyncCursor(docs)
    file_uploads.delete_one = AsyncMock()

    messages = MagicMock()
    messages.find_one = AsyncMock(return_value=reference)

    storage = MagicMock()
    storage.delete = AsyncMock(return_value=storage_deleted)
    if storage_error is not None:
        storage.delete.side_effect = storage_error

    cleaner = OrphanedUploadCleaner()
    cleaner.set_cleanup_deps(
        OrphanedUploadCleanerDeps(
            file_uploads_collection=file_uploads,
            room_user_messages_collection=messages,
            object_storage=storage,
        )
    )
    return cleaner, file_uploads, messages, storage


@pytest.mark.asyncio
async def test_orphaned_upload_cleaner_deletes_unreferenced_upload():
    doc = {
        "_id": "mongo-id",
        "file_id": "file-1",
        "s3_key": "uploads/file-1",
        "uploaded_at": utcnow() - timedelta(hours=48),
    }
    cleaner, file_uploads, messages, storage = _cleaner_with_deps(docs=[doc])

    deleted = await cleaner.cleanup_orphaned_uploads()

    assert deleted == 1
    messages.find_one.assert_awaited_once_with(
        {"message_content.attachments.file_id": "file-1"}
    )
    storage.delete.assert_awaited_once_with("uploads/file-1")
    file_uploads.delete_one.assert_awaited_once_with({"_id": "mongo-id"})


@pytest.mark.asyncio
async def test_orphaned_upload_cleaner_keeps_referenced_upload():
    doc = {"_id": "mongo-id", "file_id": "file-1", "s3_key": "uploads/file-1"}
    cleaner, file_uploads, _messages, storage = _cleaner_with_deps(
        docs=[doc],
        reference={"message_id": "msg-1"},
    )

    deleted = await cleaner.cleanup_orphaned_uploads()

    assert deleted == 0
    storage.delete.assert_not_awaited()
    file_uploads.delete_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_orphaned_upload_cleaner_keeps_metadata_when_storage_delete_fails():
    doc = {"_id": "mongo-id", "file_id": "file-1", "s3_key": "uploads/file-1"}
    cleaner, file_uploads, _messages, storage = _cleaner_with_deps(
        docs=[doc],
        storage_deleted=False,
    )

    deleted = await cleaner.cleanup_orphaned_uploads()

    assert deleted == 0
    storage.delete.assert_awaited_once_with("uploads/file-1")
    file_uploads.delete_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_orphaned_upload_cleaner_keeps_metadata_when_storage_delete_raises():
    doc = {"_id": "mongo-id", "file_id": "file-1", "s3_key": "uploads/file-1"}
    cleaner, file_uploads, _messages, storage = _cleaner_with_deps(
        docs=[doc],
        storage_error=RuntimeError("s3 unavailable"),
    )

    deleted = await cleaner.cleanup_orphaned_uploads()

    assert deleted == 0
    storage.delete.assert_awaited_once_with("uploads/file-1")
    file_uploads.delete_one.assert_not_awaited()
