from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from dal.runtime_store.parts.hitl_store import HITLRuntimeStorePart
from models.hitl import HITLPromptType


class _FakeCollection:
    def __init__(
        self,
        *,
        insert_one_side_effect: Exception | None = None,
        find_one_results: list[Any] | None = None,
        find_one_and_update_results: list[Any] | None = None,
        update_one_results: list[Any] | None = None,
        create_index_side_effect_by_name: dict[str | None, Exception] | None = None,
    ) -> None:
        self.insert_one_side_effect = insert_one_side_effect
        self.find_one_results = list(find_one_results or [])
        self.find_one_and_update_results = list(find_one_and_update_results or [])
        self.update_one_results = list(update_one_results or [])
        self.create_index_side_effect_by_name = dict(
            create_index_side_effect_by_name or {}
        )
        self.insert_one_calls: list[dict[str, Any]] = []
        self.find_one_calls: list[dict[str, Any]] = []
        self.find_one_and_update_calls: list[tuple[dict[str, Any], dict, dict]] = []
        self.update_one_calls: list[tuple[dict[str, Any], dict, dict]] = []
        self.create_index_calls: list[tuple[list[tuple[str, int]], dict]] = []

    async def insert_one(self, document: dict[str, Any]):
        self.insert_one_calls.append(deepcopy(document))
        if self.insert_one_side_effect is not None:
            raise self.insert_one_side_effect
        return SimpleNamespace(inserted_id=document.get("_id", "inserted-1"))

    async def find_one(self, query: dict[str, Any]):
        self.find_one_calls.append(deepcopy(query))
        if self.find_one_results:
            return self.find_one_results.pop(0)
        return None

    async def find_one_and_update(self, query: dict[str, Any], update: dict, **kwargs):
        self.find_one_and_update_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        if self.find_one_and_update_results:
            return self.find_one_and_update_results.pop(0)
        return None

    async def update_one(self, query: dict[str, Any], update: dict, **kwargs):
        self.update_one_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        if self.update_one_results:
            return self.update_one_results.pop(0)
        return SimpleNamespace(modified_count=1)

    async def create_index(self, keys: list[tuple[str, int]], **kwargs):
        self.create_index_calls.append((deepcopy(keys), deepcopy(kwargs)))
        name = kwargs.get("name")
        error = self.create_index_side_effect_by_name.get(name)
        if error is not None:
            raise error
        return name or "index-name"


def _store(
    *,
    hitl_requests: _FakeCollection | None = None,
    room_agent_messages: _FakeCollection | None = None,
) -> HITLRuntimeStorePart:
    return HITLRuntimeStorePart(
        hitl_requests=hitl_requests or _FakeCollection(),
        room_agent_messages=room_agent_messages or _FakeCollection(),
        room_user_messages=_FakeCollection(),
    )


@pytest.mark.asyncio
async def test_scoped_pending_query_uses_agent_message_identity_only():
    existing = {"request_id": "req-1"}
    hitl_requests = _FakeCollection(find_one_results=[existing, existing])
    store = _store(hitl_requests=hitl_requests)

    result = await store.find_pending_hitl_request_for_agent_message(
        room_id="room-1",
        display_message_id="display-msg-1",
        continuation_message_id="continuation-msg-1",
        agent_id="agent-1",
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
    )

    assert result == existing
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"continuation_message_id": "continuation-msg-1"}],
        },
    ]
    for query in hitl_requests.find_one_calls:
        assert "agent_id" not in query
        assert "a2a_task_id" not in query
        assert "a2a_context_id" not in query


@pytest.mark.asyncio
async def test_public_pending_query_returns_none_for_ambiguous_identities():
    hitl_requests = _FakeCollection(
        find_one_results=[
            {"request_id": "req-display", "display_message_id": "display-msg-1"},
            {
                "request_id": "req-continuation",
                "continuation_message_id": "continuation-msg-1",
            },
        ]
    )
    store = _store(hitl_requests=hitl_requests)

    result = await store.find_pending_hitl_request_for_agent_message(
        room_id="room-1",
        display_message_id="display-msg-1",
        continuation_message_id="continuation-msg-1",
        agent_id="agent-1",
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
    )

    assert result is None
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"continuation_message_id": "continuation-msg-1"}],
        },
    ]


@pytest.mark.asyncio
async def test_pending_query_without_display_or_continuation_identity_returns_none():
    hitl_requests = _FakeCollection()
    store = _store(hitl_requests=hitl_requests)

    result = await store.find_pending_hitl_request_for_agent_message(
        room_id="room-1",
        display_message_id=None,
        continuation_message_id=None,
        agent_id="agent-1",
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
    )

    assert result is None
    assert hitl_requests.find_one_calls == []


@pytest.mark.asyncio
async def test_legacy_continuation_only_doc_matches_pending_query():
    legacy = {"request_id": "legacy-req", "continuation_message_id": "cont-1"}
    hitl_requests = _FakeCollection(find_one_results=[legacy])
    store = _store(hitl_requests=hitl_requests)

    result = await store.find_pending_hitl_request_for_agent_message(
        room_id="room-1",
        display_message_id=None,
        continuation_message_id="cont-1",
        agent_id=None,
        a2a_task_id=None,
        a2a_context_id=None,
    )

    assert result == legacy
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"continuation_message_id": "cont-1"}],
        }
    ]


@pytest.mark.asyncio
async def test_duplicate_insert_reads_existing_doc_without_a2a_metadata_filter():
    existing = {"request_id": "req-existing", "display_message_id": "display-msg-1"}
    hitl_requests = _FakeCollection(
        insert_one_side_effect=DuplicateKeyError("duplicate display"),
        find_one_results=[existing],
    )
    store = _store(hitl_requests=hitl_requests)

    result = await store.create_or_reuse_pending_hitl_request(
        {
            "request_id": "req-new",
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "display_message_id": "display-msg-1",
            "continuation_message_id": "cont-1",
            "agent_id": "agent-1",
            "a2a_task_id": "task-1",
            "a2a_context_id": "ctx-1",
        }
    )

    assert result == (existing, False)
    assert hitl_requests.insert_one_calls[0]["request_id"] == "req-new"
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"continuation_message_id": "cont-1"}],
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("duplicate_error", "expected_query"),
    [
        (
            DuplicateKeyError(
                "E11000 duplicate key error index: uq_pending_hitl_display_message"
            ),
            {"$or": [{"display_message_id": "display-msg-1"}]},
        ),
        (
            DuplicateKeyError(
                "duplicate key",
                details={"indexName": "uq_pending_hitl_continuation_message"},
            ),
            {"$or": [{"continuation_message_id": "cont-1"}]},
        ),
    ],
)
async def test_index_specific_duplicate_readback_validates_both_identities(
    duplicate_error,
    expected_query,
):
    existing = {"request_id": "req-existing"}
    hitl_requests = _FakeCollection(
        insert_one_side_effect=duplicate_error,
        find_one_results=[existing, existing],
    )
    store = _store(hitl_requests=hitl_requests)

    result = await store.create_or_reuse_pending_hitl_request(
        {
            "request_id": "req-new",
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "display_message_id": "display-msg-1",
            "continuation_message_id": "cont-1",
        }
    )

    assert result == (existing, False)
    other_query = (
        {"$or": [{"continuation_message_id": "cont-1"}]}
        if expected_query == {"$or": [{"display_message_id": "display-msg-1"}]}
        else {"$or": [{"display_message_id": "display-msg-1"}]}
    )
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            **expected_query,
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            **other_query,
        },
    ]


@pytest.mark.asyncio
async def test_named_duplicate_readback_returns_none_for_ambiguous_identities():
    hitl_requests = _FakeCollection(
        insert_one_side_effect=DuplicateKeyError(
            "E11000 duplicate key error index: uq_pending_hitl_display_message"
        ),
        find_one_results=[
            {"request_id": "req-display", "display_message_id": "display-msg-1"},
            {"request_id": "req-cont", "continuation_message_id": "cont-1"},
        ],
    )
    store = _store(hitl_requests=hitl_requests)

    result = await store.create_or_reuse_pending_hitl_request(
        {
            "request_id": "req-new",
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "display_message_id": "display-msg-1",
            "continuation_message_id": "cont-1",
        }
    )

    assert result is None
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"continuation_message_id": "cont-1"}],
        },
    ]


@pytest.mark.asyncio
async def test_ambiguous_unidentified_duplicate_readback_returns_none():
    hitl_requests = _FakeCollection(
        insert_one_side_effect=DuplicateKeyError("E11000 duplicate key error"),
        find_one_results=[
            {"request_id": "req-display", "display_message_id": "display-msg-1"},
            {"request_id": "req-cont", "continuation_message_id": "cont-1"},
        ],
    )
    store = _store(hitl_requests=hitl_requests)

    result = await store.create_or_reuse_pending_hitl_request(
        {
            "request_id": "req-new",
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "display_message_id": "display-msg-1",
            "continuation_message_id": "cont-1",
        }
    )

    assert result is None
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"continuation_message_id": "cont-1"}],
        },
    ]


@pytest.mark.asyncio
async def test_duplicate_insert_reads_legacy_continuation_doc():
    legacy = {"request_id": "legacy-req", "continuation_message_id": "cont-1"}
    hitl_requests = _FakeCollection(
        insert_one_side_effect=DuplicateKeyError("duplicate continuation"),
        find_one_results=[legacy],
    )
    store = _store(hitl_requests=hitl_requests)

    result = await store.create_or_reuse_pending_hitl_request(
        {
            "request_id": "req-new",
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "continuation_message_id": "cont-1",
        }
    )

    assert result == (legacy, False)
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "source": "agent",
            "$or": [{"continuation_message_id": "cont-1"}],
        }
    ]


@pytest.mark.asyncio
async def test_persist_pending_hitl_on_agent_message_projects_metadata_noop_success():
    projected_doc = {
        "message_id": "agent-msg-1",
        "message_content": {
            "message_task": {
                "status": {"state": "input-required"},
                "metadata": {"hitl_request_id": "req-1"},
            }
        },
    }
    room_agent_messages = _FakeCollection(
        find_one_and_update_results=[projected_doc],
    )
    store = _store(room_agent_messages=room_agent_messages)

    result = await store.persist_pending_hitl_on_agent_message(
        "agent-msg-1",
        request_id="req-1",
        prompt="Need policy effective date",
        prompt_type=HITLPromptType.TEXT,
        choices=None,
        a2a_task_id=None,
        a2a_context_id=None,
        group_id=None,
        group_total=None,
        group_index=None,
    )

    assert result is True
    assert room_agent_messages.update_one_calls == [
        (
            {
                "message_id": "agent-msg-1",
                "message_content.message_task.metadata": None,
            },
            {"$set": {"message_content.message_task.metadata": {}}},
            {},
        )
    ]
    assert len(room_agent_messages.find_one_and_update_calls) == 1
    query, update, kwargs = room_agent_messages.find_one_and_update_calls[0]
    assert query == {"message_id": "agent-msg-1"}
    assert kwargs == {"return_document": ReturnDocument.AFTER}

    sets = update["$set"]
    unsets = update["$unset"]
    assert sets["message_content.message_task.status.state"] == "input-required"
    assert sets["message_content.message_task.metadata.hitl_request_id"] == "req-1"
    assert (
        sets["message_content.message_task.metadata.hitl_prompt"]
        == "Need policy effective date"
    )
    assert sets["message_content.message_task.metadata.hitl_prompt_type"] == "text"
    assert sets["message_content.message_task.metadata.hitl_choices"] is None
    assert sets["message_content.message_task.metadata.user_answer"] is None
    assert "task_updated_at" in sets
    assert unsets == {
        "message_content.message_task.metadata.hitl_a2a_task_id": "",
        "message_content.message_task.metadata.hitl_a2a_context_id": "",
        "message_content.message_task.metadata.hitl_group_id": "",
        "message_content.message_task.metadata.hitl_group_total": "",
        "message_content.message_task.metadata.hitl_group_index": "",
    }
    assert all("hitl_status" not in key for key in sets)
    assert all("hitl_status" not in key for key in unsets)


@pytest.mark.asyncio
async def test_persist_pending_hitl_on_missing_agent_message_returns_false():
    room_agent_messages = _FakeCollection(find_one_and_update_results=[None])
    store = _store(room_agent_messages=room_agent_messages)

    result = await store.persist_pending_hitl_on_agent_message(
        "missing-msg",
        request_id="req-1",
        prompt="Need policy effective date",
        prompt_type="text",
        choices=None,
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
        group_id="group-1",
        group_total=2,
        group_index=0,
    )

    assert result is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "index_name",
    [
        "uq_pending_hitl_display_message",
        "uq_pending_hitl_continuation_message",
    ],
)
async def test_ensure_hitl_indexes_raises_when_critical_unique_index_fails(
    index_name,
):
    error = RuntimeError(f"{index_name} failed")
    hitl_requests = _FakeCollection(
        create_index_side_effect_by_name={index_name: error}
    )
    store = _store(hitl_requests=hitl_requests)

    with pytest.raises(RuntimeError, match=f"{index_name} failed"):
        await store.ensure_hitl_indexes()

    assert any(
        kwargs.get("name") == index_name
        for _keys, kwargs in hitl_requests.create_index_calls
    )
