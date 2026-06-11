from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from app_shell.repository_store import AppShellRepositoryStore


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

    async def update_one(self, query: dict, update: dict | list, **kwargs):
        if self.side_effect is not None:
            raise self.side_effect
        self.update_one_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(modified_count=1)


def _result(modified_count: int):
    return SimpleNamespace(modified_count=modified_count)


def _store(collection: RecordingCollection) -> AppShellRepositoryStore:
    return AppShellRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": collection}),
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
        assert collection.update_one_calls == []

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
