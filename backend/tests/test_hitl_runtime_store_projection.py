from __future__ import annotations

import json
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
        documents: list[dict[str, Any]] | None = None,
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
        self.documents = [deepcopy(document) for document in documents or []]

    async def insert_one(self, document: dict[str, Any]):
        self.insert_one_calls.append(deepcopy(document))
        if self.insert_one_side_effect is not None:
            raise self.insert_one_side_effect
        return SimpleNamespace(inserted_id=document.get("_id", "inserted-1"))

    async def find_one(self, query: dict[str, Any]):
        recorded_query = deepcopy(query)
        # Keep legacy identity-shape assertions readable while the dedicated
        # deadline tests assert the additional authoritative predicate.
        clauses = recorded_query.get("$and")
        if (
            isinstance(clauses, list)
            and len(clauses) == 2
            and isinstance(clauses[0], dict)
            and "status" in clauses[0]
        ):
            identity = dict(clauses[0])
            room_id = identity.pop("room_id", None)
            status = identity.pop("status", None)
            source = identity.pop("public_source", None)
            recorded_query = {
                "room_id": room_id,
                "status": status,
                "public_source": source,
                "$or": [identity],
            }
        self.find_one_calls.append(recorded_query)
        if self.find_one_results:
            return self.find_one_results.pop(0)
        return None

    async def find_one_and_update(self, query: dict[str, Any], update: dict, **kwargs):
        self.find_one_and_update_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        if self.find_one_and_update_results:
            return self.find_one_and_update_results.pop(0)
        for document in self.documents:
            if _matches_query(document, query):
                before = deepcopy(document)
                _apply_update(document, update)
                if kwargs.get("return_document") == ReturnDocument.AFTER:
                    return deepcopy(document)
                return before
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


_MISSING = object()


def _get_dotted(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _set_dotted(document: dict[str, Any], path: str, value: Any) -> None:
    current = document
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = deepcopy(value)


def _unset_dotted(document: dict[str, Any], path: str) -> None:
    current = document
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            return
        current = child
    current.pop(parts[-1], None)


def _matches_query(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for path, expected in query.items():
        actual = _get_dotted(document, path)
        if isinstance(expected, dict) and "$nin" in expected:
            if actual in expected["$nin"]:
                return False
        elif actual != expected:
            return False
    return True


def _apply_update(document: dict[str, Any], update: dict[str, Any]) -> None:
    for path, value in update.get("$set", {}).items():
        _set_dotted(document, path, value)
    for path in update.get("$unset", {}):
        _unset_dotted(document, path)


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
            "public_source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "public_source": "agent",
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
            "public_source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "public_source": "agent",
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
            "public_source": "agent",
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
            "public_source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "public_source": "agent",
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
            "public_source": "agent",
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
            "public_source": "agent",
            **expected_query,
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "public_source": "agent",
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
            "public_source": "agent",
            "display_message_id": "display-msg-1",
            "continuation_message_id": "cont-1",
        }
    )

    assert result is None
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "public_source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "public_source": "agent",
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
            "public_source": "agent",
            "display_message_id": "display-msg-1",
            "continuation_message_id": "cont-1",
        }
    )

    assert result is None
    assert hitl_requests.find_one_calls == [
        {
            "room_id": "room-1",
            "status": "pending",
            "public_source": "agent",
            "$or": [{"display_message_id": "display-msg-1"}],
        },
        {
            "room_id": "room-1",
            "status": "pending",
            "public_source": "agent",
            "$or": [{"continuation_message_id": "cont-1"}],
        },
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
        interaction_id="interaction-1",
        question_count=1,
        question_index=0,
    )

    assert result is True
    assert room_agent_messages.update_one_calls == []
    assert len(room_agent_messages.find_one_and_update_calls) == 1
    query, update, kwargs = room_agent_messages.find_one_and_update_calls[0]
    assert query["message_id"] == "agent-msg-1"
    assert "completed" in query["message_content.message_task.status.state"]["$nin"]
    assert kwargs == {"return_document": ReturnDocument.AFTER}

    sets = update["$set"]
    assert sets["message_content.message_task.status.state"] == "input-required"
    assert sets["message_content.message_task.metadata"] == {
        "hitl_request_id": "req-1",
        "hitl_prompt": "Need policy effective date",
        "hitl_prompt_type": "text",
        "user_answer": None,
    }
    assert "task_updated_at" in sets
    assert all(".metadata." not in key for key in sets)
    assert "$unset" not in update
    assert all("hitl_status" not in key for key in sets)


@pytest.mark.asyncio
async def test_persist_pending_hitl_replaces_stale_metadata_projection():
    private_sentinel = "PRIVATE_SENTINEL_remote_hitl"
    stale_document = {
        "message_id": "agent-msg-1",
        "message_content": {
            "message_task": {
                "status": {"state": "working"},
                "metadata": {
                    "hitl_request_id": "remote-req",
                    "hitl_prompt": private_sentinel,
                    "hitl_prompt_type": "remote-choice",
                    "hitl_choices": [private_sentinel],
                    "hitl_status": private_sentinel,
                    "hitl_resource_id": private_sentinel,
                    "remote_metadata": {"secret": private_sentinel},
                    "user_answer": private_sentinel,
                    "untrusted_extra": private_sentinel,
                },
            }
        },
    }
    room_agent_messages = _FakeCollection(documents=[stale_document])
    store = _store(room_agent_messages=room_agent_messages)

    result = await store.persist_pending_hitl_on_agent_message(
        "agent-msg-1",
        request_id="req-1",
        prompt="Choose the approved option",
        prompt_type=HITLPromptType.CHOICE,
        choices=["Approve", "Reject"],
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
        interaction_id="group-1",
        question_count=2,
        question_index=0,
    )

    assert result is True
    assert room_agent_messages.update_one_calls == []
    assert len(room_agent_messages.find_one_and_update_calls) == 1
    query, update, kwargs = room_agent_messages.find_one_and_update_calls[0]
    assert query["message_id"] == "agent-msg-1"
    assert "completed" in query["message_content.message_task.status.state"]["$nin"]
    assert kwargs == {"return_document": ReturnDocument.AFTER}

    expected_metadata = {
        "hitl_request_id": "req-1",
        "hitl_prompt": "Choose the approved option",
        "hitl_prompt_type": "choice",
        "hitl_choices": ["Approve", "Reject"],
        "user_answer": None,
        "hitl_a2a_task_id": "task-1",
        "hitl_a2a_context_id": "ctx-1",
        "hitl_group_id": "group-1",
        "hitl_group_total": 2,
        "hitl_group_index": 0,
    }
    sets = update["$set"]
    assert sets["message_content.message_task.status.state"] == "input-required"
    assert sets["message_content.message_task.metadata"] == expected_metadata
    assert "task_updated_at" in sets
    assert all(".metadata." not in path for path in sets)
    assert "$unset" not in update

    projected = room_agent_messages.documents[0]
    assert (
        projected["message_content"]["message_task"]["status"]["state"]
        == "input-required"
    )
    assert projected["message_content"]["message_task"]["metadata"] == expected_metadata
    assert private_sentinel not in json.dumps(projected, default=str, sort_keys=True)


@pytest.mark.asyncio
async def test_persist_pending_hitl_cannot_reopen_terminal_projection_winner():
    terminal = {
        "message_id": "agent-msg-1",
        "terminal_projection_event_id": "evt-completed",
        "message_content": {
            "message_task": {
                "status": {"state": "completed"},
                "metadata": {"durable": True},
            }
        },
    }
    room_agent_messages = _FakeCollection(documents=[terminal])
    store = _store(room_agent_messages=room_agent_messages)

    result = await store.persist_pending_hitl_on_agent_message(
        "agent-msg-1",
        request_id="req-1",
        prompt="late prompt",
        prompt_type="text",
        choices=None,
        a2a_task_id=None,
        a2a_context_id=None,
        interaction_id="interaction-1",
        question_count=1,
        question_index=0,
    )

    assert result is False
    assert room_agent_messages.documents[0] == terminal


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
        interaction_id="group-1",
        question_count=2,
        question_index=0,
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
