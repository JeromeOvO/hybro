from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from dal.runtime_store import RuntimeRepositoryStore


class FakeMongo:
    def __init__(self, collections: dict[str, RecordingCollection] | None = None):
        self.collections = collections or {}

    def collection(self, name: str):
        self.collections.setdefault(name, RecordingCollection())
        return self.collections[name]


class RecordingCollection:
    def __init__(
        self,
        results: list[Any] | None = None,
        *,
        side_effect: Exception | None = None,
    ) -> None:
        self.results = list(results or [])
        self.side_effect = side_effect
        self.update_one_calls: list[tuple[dict, dict | list, dict]] = []
        self.insert_one_calls: list[dict] = []
        self.find_one_calls: list[dict] = []
        self.find_calls: list[tuple[dict, dict]] = []
        self.find_one_and_update_calls: list[tuple[dict, dict, dict]] = []
        self.count_calls: list[dict] = []
        self.create_index_calls: list[tuple[list[tuple[str, int]], dict]] = []

    async def update_one(self, query: dict, update: dict | list, **kwargs):
        if self.side_effect is not None:
            raise self.side_effect
        self.update_one_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(modified_count=1)

    async def insert_one(self, document: dict):
        if self.side_effect is not None:
            raise self.side_effect
        self.insert_one_calls.append(deepcopy(document))
        return SimpleNamespace(inserted_id=document.get("_id", "inserted-1"))

    async def find_one(self, query: dict):
        if self.side_effect is not None:
            raise self.side_effect
        self.find_one_calls.append(deepcopy(query))
        if self.results:
            return self.results.pop(0)
        return None

    async def find(self, query: dict, **kwargs):
        if self.side_effect is not None:
            raise self.side_effect
        self.find_calls.append((deepcopy(query), deepcopy(kwargs)))
        if self.results:
            return self.results.pop(0)
        return []

    async def find_one_and_update(self, query: dict, update: dict, **kwargs):
        if self.side_effect is not None:
            raise self.side_effect
        self.find_one_and_update_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        if self.results:
            return self.results.pop(0)
        return None

    async def count(self, query: dict):
        if self.side_effect is not None:
            raise self.side_effect
        self.count_calls.append(deepcopy(query))
        if self.results:
            return self.results.pop(0)
        return 0

    async def create_index(self, keys: list[tuple[str, int]], **kwargs):
        if self.side_effect is not None:
            raise self.side_effect
        self.create_index_calls.append((deepcopy(keys), deepcopy(kwargs)))
        return "index-name"


def _result(modified_count: int):
    return SimpleNamespace(modified_count=modified_count)


def _store(
    collection: RecordingCollection | None = None,
    *,
    hitl_collection: RecordingCollection | None = None,
) -> RuntimeRepositoryStore:
    return RuntimeRepositoryStore(
        mongo=FakeMongo(
            {
                "room_agent_messages": collection or RecordingCollection(),
                "hitl_requests": hitl_collection or RecordingCollection(),
            }
        ),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )


def _set_stage(update_doc: dict | list) -> dict:
    if isinstance(update_doc, list):
        return update_doc[0]["$set"]
    return update_doc["$set"]


def _assert_terminal_state_filter(query: dict) -> None:
    state_filter = query["message_content.message_task.status.state"]
    assert set(state_filter["$nin"]) == {"completed", "canceled", "failed", "rejected"}


@pytest.mark.asyncio
async def test_check_task_limits_honors_compatibility_store_overrides():
    class CountingMessageRepository:
        async def count_agent_messages(self, query: dict) -> int:
            return 1

    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=object(),
        message_repository=CountingMessageRepository(),
        agent_repository=object(),
    )
    store.MAX_TASKS_PER_USER = 1
    store.MAX_TASKS_PER_ROOM = 1

    with pytest.raises(ValueError, match="User has too many pending tasks"):
        await store.check_task_limits("user-1", "room-1", ["working"])


def test_webhook_token_helpers_do_not_require_repository_attributes():
    store = object.__new__(RuntimeRepositoryStore)

    token = store.generate_webhook_token()

    assert isinstance(token, str)
    assert len(token) > 0


def test_webhook_token_helpers_live_in_shared_module():
    from dal.runtime_store.parts import webhook_tokens

    assert callable(webhook_tokens.generate_webhook_token)
    assert callable(webhook_tokens.hash_webhook_token)
    assert callable(webhook_tokens.verify_webhook_token)


@pytest.mark.asyncio
async def test_check_task_limits_passes_facade_limits_without_mutating_part():
    class CountingMessageRepository:
        async def count_agent_messages(self, query: dict) -> int:
            return 1

    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=object(),
        message_repository=CountingMessageRepository(),
        agent_repository=object(),
    )
    store.MAX_TASKS_PER_USER = 2
    store.MAX_TASKS_PER_ROOM = 2

    await store.check_task_limits("user-1", "room-1", ["working"])

    assert "MAX_TASKS_PER_USER" not in store.tasks.__dict__
    assert "MAX_TASKS_PER_ROOM" not in store.tasks.__dict__


class TestRepositoryStoreAccumulateArtifact:
    @pytest.mark.asyncio
    async def test_missing_artifact_id_pushes_new_artifact(self):
        collection = RecordingCollection([_result(1)])
        store = _store(collection)

        result = await store.accumulate_artifact_on_message(
            "msg-1",
            {"parts": [{"kind": "text", "text": "hello"}]},
        )

        assert result is True
        query, update_doc, _ = collection.update_one_calls[0]
        assert query["message_id"] == "msg-1"
        _assert_terminal_state_filter(query)
        assert update_doc["$push"]["message_content.message_task.artifacts"] == {
            "parts": [{"kind": "text", "text": "hello"}]
        }
        assert _set_stage(update_doc)["message_content.message_text"] == "hello"

    @pytest.mark.asyncio
    async def test_append_false_replaces_existing_artifact_atomically(self):
        collection = RecordingCollection([_result(1)])
        store = _store(collection)

        result = await store.accumulate_artifact_on_message(
            "msg-1",
            {"artifactId": "art-1", "parts": [{"kind": "text", "text": "new"}]},
            append=False,
        )

        assert result is True
        query, update_doc, _ = collection.update_one_calls[0]
        assert query["message_content.message_task.artifacts"] == {
            "$elemMatch": {"$or": [{"artifactId": "art-1"}, {"artifact_id": "art-1"}]}
        }
        set_stage = _set_stage(update_doc)
        assert "$map" in set_stage["message_content.message_task.artifacts"]
        assert set_stage["message_content.message_text"] == "new"
        assert set_stage["message_content.message_task.status.state"] == "working"

    @pytest.mark.asyncio
    async def test_append_false_inserts_when_artifact_id_not_found(self):
        collection = RecordingCollection([_result(0), _result(1)])
        store = _store(collection)

        result = await store.accumulate_artifact_on_message(
            "msg-1",
            {"artifactId": "art-new", "parts": [{"kind": "text", "text": "content"}]},
            append=False,
        )

        assert result is True
        assert len(collection.update_one_calls) == 2
        _, insert_update, _ = collection.update_one_calls[1]
        assert insert_update["$push"]["message_content.message_task.artifacts"] == {
            "artifactId": "art-new",
            "parts": [{"kind": "text", "text": "content"}],
        }
        assert insert_update["$set"]["message_content.message_text"] == "content"

    @pytest.mark.asyncio
    async def test_append_true_extends_parts_and_concats_text_atomically(self):
        collection = RecordingCollection([_result(1)])
        store = _store(collection)

        result = await store.accumulate_artifact_on_message(
            "msg-1",
            {"artifactId": "art-1", "parts": [{"kind": "text", "text": " more"}]},
            append=True,
        )

        assert result is True
        query, update_doc, _ = collection.update_one_calls[0]
        assert "$elemMatch" in query["message_content.message_task.artifacts"]
        set_stage = _set_stage(update_doc)
        assert "$map" in set_stage["message_content.message_task.artifacts"]
        assert set_stage["message_content.message_text"] == {
            "$concat": [{"$ifNull": ["$message_content.message_text", ""]}, " more"]
        }

    @pytest.mark.asyncio
    async def test_append_true_inserts_when_artifact_id_not_found(self):
        collection = RecordingCollection([_result(0), _result(1)])
        store = _store(collection)

        result = await store.accumulate_artifact_on_message(
            "msg-1",
            {
                "artifactId": "art-new",
                "parts": [{"kind": "text", "text": "first chunk"}],
            },
            append=True,
        )

        assert result is True
        assert len(collection.update_one_calls) == 2
        _, insert_update, _ = collection.update_one_calls[1]
        assert "$push" in insert_update
        assert insert_update["$set"]["message_content.message_text"] == "first chunk"

    @pytest.mark.asyncio
    async def test_append_true_returns_false_when_sanitizer_drops_all_parts(self):
        collection = RecordingCollection()
        store = _store(collection)

        result = await store.accumulate_artifact_on_message(
            "msg-1",
            {"artifactId": "art-1", "parts": [{"kind": "text"}]},
            append=True,
        )

        assert result is False


class TestRepositoryStoreHITL:
    @pytest.mark.asyncio
    async def test_creates_and_reads_pending_hitl_requests_by_shape(self):
        hitl_requests = RecordingCollection([[{"request_id": "h1"}]])
        store = _store(hitl_collection=hitl_requests)

        assert await store.create_hitl_request({"request_id": "h1"})
        pending = await store.get_pending_hitl_requests_for_message("u1")

        assert pending == [{"request_id": "h1"}]
        assert hitl_requests.insert_one_calls == [{"request_id": "h1"}]
        assert hitl_requests.find_calls == [
            ({"user_message_id": "u1", "status": "pending"}, {"limit": 50})
        ]

    @pytest.mark.asyncio
    async def test_cas_and_fenced_updates_preserve_concurrency_guards(self):
        hitl_requests = RecordingCollection([_result(1), _result(1)])
        store = _store(hitl_collection=hitl_requests)

        assert await store.cas_update_hitl_request(
            "h1",
            expected_status="processing",
            status="responded",
        )
        assert await store.fenced_update_hitl_request(
            "h1",
            claim_id="claim-1",
            status="responded",
        )

        assert hitl_requests.update_one_calls[0] == (
            {"request_id": "h1", "status": "processing"},
            {"$set": {"status": "responded"}},
            {},
        )
        assert hitl_requests.update_one_calls[1] == (
            {"request_id": "h1", "claim_id": "claim-1"},
            {"$set": {"status": "responded"}},
            {},
        )

    @pytest.mark.asyncio
    async def test_group_routing_claim_release_and_count_shapes(self):
        hitl_requests = RecordingCollection([_result(1), _result(1), 2])
        store = _store(hitl_collection=hitl_requests)

        assert await store.claim_hitl_group_routing("group-1", "claim-1")
        assert await store.release_hitl_group_routing("group-1", "claim-1")
        count = await store.count_pending_in_hitl_group("group-1")

        assert count == 2
        assert hitl_requests.update_one_calls[0][0] == {
            "group_id": "group-1",
            "group_index": 0,
            "group_routing_claim_id": {"$exists": False},
        }
        assert (
            hitl_requests.update_one_calls[0][1]["$set"]["group_routing_claim_id"]
            == "claim-1"
        )
        assert hitl_requests.update_one_calls[1] == (
            {"group_id": "group-1", "group_routing_claim_id": "claim-1"},
            {
                "$unset": {
                    "group_routing_claim_id": "",
                    "group_routing_claimed_at": "",
                }
            },
            {},
        )
        assert hitl_requests.count_calls == [
            {"group_id": "group-1", "status": {"$in": ["pending", "processing"]}}
        ]

    @pytest.mark.asyncio
    async def test_stale_processing_iterator_and_indexes_use_hitl_collection(self):
        docs = [{"request_id": "h1"}]
        hitl_requests = RecordingCollection([docs])
        store = _store(hitl_collection=hitl_requests)

        result = [
            doc
            async for doc in store.iter_stale_processing_hitl_requests("cutoff-time")
        ]
        await store.ensure_hitl_indexes()

        assert result == docs
        assert hitl_requests.find_calls == [
            (
                {"status": "processing", "responded_at": {"$lt": "cutoff-time"}},
                {},
            )
        ]
        assert hitl_requests.create_index_calls == [
            ([("request_id", 1)], {"unique": True}),
            ([("room_id", 1), ("status", 1)], {}),
            ([("expires_at", 1), ("status", 1)], {}),
            ([("user_message_id", 1), ("status", 1)], {}),
            ([("continuation_message_id", 1)], {}),
            (
                [("room_id", 1), ("display_message_id", 1)],
                {
                    "unique": True,
                    "name": "uq_pending_hitl_display_message",
                    "partialFilterExpression": {
                        "status": "pending",
                        "source": "agent",
                        "display_message_id": {"$type": "string"},
                    },
                },
            ),
            (
                [("room_id", 1), ("continuation_message_id", 1)],
                {
                    "unique": True,
                    "name": "uq_pending_hitl_continuation_message",
                    "partialFilterExpression": {
                        "status": "pending",
                        "source": "agent",
                        "continuation_message_id": {"$type": "string"},
                    },
                },
            ),
        ]

    @pytest.mark.asyncio
    async def test_terminal_state_filter_applies_to_replace_and_insert_paths(self):
        collection = RecordingCollection([_result(0), _result(1)])
        store = _store(collection)

        await store.accumulate_artifact_on_message(
            "msg-1",
            {"artifactId": "art-1", "parts": [{"kind": "text", "text": "x"}]},
            append=False,
        )

        for query, _, _ in collection.update_one_calls:
            _assert_terminal_state_filter(query)

    @pytest.mark.asyncio
    async def test_handles_artifact_id_snake_case(self):
        collection = RecordingCollection([_result(1)])
        store = _store(collection)

        result = await store.accumulate_artifact_on_message(
            "msg-1",
            {"artifact_id": "art-snake", "parts": [{"kind": "text", "text": "x"}]},
        )

        assert result is True
        query, _, _ = collection.update_one_calls[0]
        elem_match = query["message_content.message_task.artifacts"]["$elemMatch"]
        assert {"artifact_id": "art-snake"} in elem_match["$or"]

    @pytest.mark.asyncio
    async def test_extracts_text_from_nested_root_text(self):
        collection = RecordingCollection([_result(1)])
        store = _store(collection)

        await store.accumulate_artifact_on_message(
            "msg-1",
            {
                "artifactId": "art-1",
                "parts": [{"root": {"kind": "text", "text": "nested text"}}],
            },
            append=False,
        )

        _, update_doc, _ = collection.update_one_calls[0]
        assert _set_stage(update_doc)["message_content.message_text"] == "nested text"

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        collection = RecordingCollection(side_effect=RuntimeError("connection lost"))
        store = _store(collection)

        result = await store.accumulate_artifact_on_message(
            "msg-1",
            {"artifactId": "art-1", "parts": [{"kind": "text", "text": "x"}]},
        )

        assert result is False
