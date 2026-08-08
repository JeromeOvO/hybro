import asyncio
from unittest.mock import AsyncMock

import pytest
from pymongo.errors import DuplicateKeyError

from dal.runtime_store.cancellation_repository import (
    MongoCancellationMarkerRepository,
)


@pytest.mark.asyncio
async def test_request_uses_deterministic_atomic_key_and_preserves_marker_schema():
    collection = AsyncMock()
    collection.find_one.return_value = None
    repository = MongoCancellationMarkerRepository(collection)

    assert await repository.request("message-1", "user-1") is True

    collection.find_one.assert_awaited_once_with({"message_id": "message-1"})
    query, update = collection.update_one.await_args.args
    assert query == {"_id": "cancellation:message-1"}
    assert update["$set"] == {"reconciliation_status": "pending"}
    assert update["$setOnInsert"]["message_id"] == "message-1"
    assert update["$setOnInsert"]["user_id"] == "user-1"
    assert "cancelled_at" in update["$setOnInsert"]
    assert collection.update_one.await_args.kwargs == {"upsert": True}


@pytest.mark.asyncio
async def test_concurrent_first_requests_converge_on_one_deterministic_document():
    class ConcurrentFirstRequestCollection:
        def __init__(self):
            self.find_count = 0
            self.both_read_missing = asyncio.Event()
            self.documents = {}
            self.upsert_filters = []

        async def find_one(self, query):
            assert query == {"message_id": "message-1"}
            self.find_count += 1
            if self.find_count == 2:
                self.both_read_missing.set()
            await self.both_read_missing.wait()
            return None

        async def update_one(self, query, update, *, upsert=False):
            assert upsert is True
            self.upsert_filters.append(query)
            await asyncio.sleep(0)
            key = query["_id"]
            if key in self.documents:
                raise DuplicateKeyError("concurrent deterministic _id insert")
            self.documents[key] = {"_id": key, **update["$setOnInsert"]}
            self.documents[key].update(update["$set"])

        async def update_many(self, query, update):
            for document in self.documents.values():
                if document["message_id"] == query["message_id"]:
                    document.update(update["$set"])

    collection = ConcurrentFirstRequestCollection()
    repository = MongoCancellationMarkerRepository(collection)

    results = await asyncio.gather(
        repository.request("message-1", "user-1"),
        repository.request("message-1", "user-2"),
    )

    assert results == [True, True]
    assert collection.upsert_filters == [
        {"_id": "cancellation:message-1"},
        {"_id": "cancellation:message-1"},
    ]
    assert list(collection.documents) == ["cancellation:message-1"]
    marker = collection.documents["cancellation:message-1"]
    assert marker["message_id"] == "message-1"
    assert marker["user_id"] in {"user-1", "user-2"}
    assert marker["reconciliation_status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_status", ["pending", "reconciled"])
async def test_request_updates_all_legacy_duplicates_without_inserting(
    existing_status,
):
    collection = AsyncMock()
    collection.find_one.return_value = {
        "_id": "legacy-1",
        "message_id": "message-1",
        "reconciliation_status": existing_status,
    }
    repository = MongoCancellationMarkerRepository(collection)

    assert await repository.request("message-1", "user-1") is True

    collection.update_many.assert_awaited_once_with(
        {"message_id": "message-1"},
        {"$set": {"reconciliation_status": "pending"}},
    )
    collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_pending_uses_compatible_query_sort_projection_and_page_cursor():
    collection = AsyncMock()
    collection.find.return_value = [{"message_id": "message-2"}]
    repository = MongoCancellationMarkerRepository(collection)

    result = await repository.list_pending(limit=25, after_message_id="message-1")

    assert result == [{"message_id": "message-2"}]
    collection.find.assert_awaited_once_with(
        {
            "message_id": {"$type": "string", "$gt": "message-1"},
            "reconciliation_status": "pending",
        },
        projection={"_id": 0},
        sort=[("message_id", 1)],
        limit=25,
    )


@pytest.mark.asyncio
async def test_mark_reconciled_updates_all_legacy_duplicates():
    collection = AsyncMock()
    collection.update_many.return_value = 2
    repository = MongoCancellationMarkerRepository(collection)

    assert await repository.mark_reconciled("message-1") is True

    query, update = collection.update_many.await_args.args
    assert query == {"message_id": "message-1"}
    assert update["$set"]["reconciliation_status"] == "reconciled"
    assert "reconciled_at" in update["$set"]
    collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_reconciled_returns_false_when_marker_is_missing():
    collection = AsyncMock()
    collection.update_many.return_value = 0
    repository = MongoCancellationMarkerRepository(collection)

    assert await repository.mark_reconciled("missing") is False


@pytest.mark.asyncio
async def test_mutation_failures_keep_existing_false_error_semantics():
    collection = AsyncMock()
    collection.find_one.return_value = None
    collection.update_one.side_effect = RuntimeError("mongo unavailable")
    collection.update_many.side_effect = RuntimeError("mongo unavailable")
    repository = MongoCancellationMarkerRepository(collection)

    assert await repository.request("message-1", "user-1") is False
    assert await repository.mark_reconciled("message-1") is False


@pytest.mark.asyncio
async def test_scan_failure_propagates():
    collection = AsyncMock()
    collection.find.side_effect = RuntimeError("mongo unavailable")
    repository = MongoCancellationMarkerRepository(collection)

    with pytest.raises(RuntimeError, match="mongo unavailable"):
        await repository.list_pending()
