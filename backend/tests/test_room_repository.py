from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from common.dto import TimelinePosition
from room.idempotency import (
    IdempotencyConflictError,
    UnexpectedUserMessageDuplicateError,
)
from room.repository import MessageMongoRepository, RoomMongoRepository


class FakeMongo:
    def __init__(self, collections: dict[str, FakeCollection] | None = None) -> None:
        self.collections = collections or {}
        self.collection_calls: list[str] = []

    def collection(self, name: str) -> FakeCollection:
        self.collection_calls.append(name)
        self.collections.setdefault(name, FakeCollection())
        return self.collections[name]


class FakeCollection:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs = [deepcopy(doc) for doc in docs or []]
        self.find_one_calls: list[dict] = []
        self.find_calls: list[tuple[dict, dict]] = []
        self.insert_one_calls: list[dict] = []
        self.update_one_calls: list[tuple[dict, dict, dict]] = []
        self.replace_one_calls: list[tuple[dict, dict, dict]] = []
        self.find_one_and_update_calls: list[tuple[dict, dict, dict]] = []
        self.delete_one_calls: list[dict] = []
        self.delete_many_calls: list[dict] = []

    async def find_one(self, query: dict, **kwargs) -> dict | None:
        self.find_one_calls.append(deepcopy(query))
        for doc in self.docs:
            if _matches(doc, query):
                return deepcopy(doc)
        return None

    async def find(self, query: dict, **kwargs) -> list[dict]:
        self.find_calls.append((deepcopy(query), deepcopy(kwargs)))
        matches = [deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        sort = kwargs.get("sort")
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda doc: _get_dotted(doc, field),
                    reverse=direction < 0,
                )
        limit = kwargs.get("limit")
        return matches[:limit] if limit else matches

    async def insert_one(self, document: dict) -> str:
        self.insert_one_calls.append(deepcopy(document))
        self.docs.append(deepcopy(document))
        return str(document.get("_id") or f"inserted-{len(self.docs)}")

    async def update_one(self, query: dict, update: dict, **kwargs) -> bool:
        self.update_one_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update)
                return True
        return False

    async def replace_one(self, query: dict, replacement: dict, **kwargs) -> bool:
        self.replace_one_calls.append(
            (deepcopy(query), deepcopy(replacement), deepcopy(kwargs))
        )
        for index, doc in enumerate(self.docs):
            if _matches(doc, query):
                self.docs[index] = deepcopy(replacement)
                return True
        if kwargs.get("upsert"):
            self.docs.append(deepcopy(replacement))
            return True
        return False

    async def update_many(self, query: dict, update: dict) -> int:
        count = 0
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update)
                count += 1
        return count

    async def find_one_and_update(
        self, query: dict, update: dict, **kwargs
    ) -> dict | None:
        self.find_one_and_update_calls.append(
            (deepcopy(query), deepcopy(update), deepcopy(kwargs))
        )
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update)
                return deepcopy(doc)
        return None

    async def delete_one(self, query: dict) -> bool:
        self.delete_one_calls.append(deepcopy(query))
        for index, doc in enumerate(self.docs):
            if _matches(doc, query):
                self.docs.pop(index)
                return True
        return False

    async def delete_many(self, query: dict) -> int:
        self.delete_many_calls.append(deepcopy(query))
        kept = []
        count = 0
        for doc in self.docs:
            if _matches(doc, query):
                count += 1
            else:
                kept.append(doc)
        self.docs = kept
        return count


class AtomicUniqueUserMessageCollection(FakeCollection):
    """Fake Mongo collection that atomically enforces both user-message indexes."""

    def __init__(self, docs: list[dict] | None = None) -> None:
        super().__init__(docs)
        self._insert_lock = asyncio.Lock()

    async def insert_one(self, document: dict) -> str:
        async with self._insert_lock:
            await asyncio.sleep(0)
            message_id = document.get("message_id")
            room_id = document.get("room_id")
            client_request_id = document.get("client_request_id")
            if any(doc.get("message_id") == message_id for doc in self.docs):
                raise DuplicateKeyError("duplicate message_id")
            if (
                isinstance(room_id, str)
                and isinstance(client_request_id, str)
                and any(
                    doc.get("room_id") == room_id
                    and doc.get("client_request_id") == client_request_id
                    for doc in self.docs
                )
            ):
                raise DuplicateKeyError("duplicate room/client_request_id")
            return await super().insert_one(document)


def _room_repo(docs: list[dict] | None = None):
    rooms = FakeCollection(docs)
    mongo = FakeMongo({"rooms": rooms})
    return RoomMongoRepository(mongo=mongo), mongo, rooms


def _message_repo(
    user_docs: list[dict] | None = None,
    agent_docs: list[dict] | None = None,
):
    user_messages = FakeCollection(user_docs)
    agent_messages = FakeCollection(agent_docs)
    mongo = FakeMongo(
        {
            "room_user_messages": user_messages,
            "room_agent_messages": agent_messages,
        }
    )
    return MessageMongoRepository(mongo=mongo), mongo, user_messages, agent_messages


@pytest.mark.asyncio
async def test_room_repository_uses_rooms_collection_and_query_shapes():
    repo, mongo, rooms = _room_repo(
        [{"room_id": "r1", "room_owner_id": "u1", "room_name": "Room"}]
    )

    assert await repo.get_by_id("r1") == {
        "room_id": "r1",
        "room_owner_id": "u1",
        "room_name": "Room",
    }
    assert await repo.get_by_owner("u1") == [
        {"room_id": "r1", "room_owner_id": "u1", "room_name": "Room"}
    ]

    assert mongo.collection_calls == ["rooms"]
    assert rooms.find_one_calls == [{"room_id": "r1"}]
    assert rooms.find_calls == [
        (
            {
                "room_owner_id": "u1",
                "$or": [
                    {"lifecycle_state": "active"},
                    {"lifecycle_state": {"$exists": False}},
                ],
            },
            {},
        )
    ]


@pytest.mark.asyncio
async def test_room_repository_create_update_update_fields_set_membership_and_delete():
    repo, _, rooms = _room_repo([{"room_id": "r1", "room_name": "Old"}])

    created_id = await repo.create({"room_id": "r2", "room_name": "New"})
    updated = await repo.update("r1", {"room_name": "Changed"})
    updated_doc = await repo.update_fields("r1", {"processing_message_id": "m1"})
    membership_doc = await repo.set_membership(
        "r1",
        agent_set={"a1": "Agent One"},
        membership_origin="saved_group",
        membership_origin_status="seeded_never_edited",
        source_group_id="g1",
        source_group_name="Group One",
    )
    deleted = await repo.delete("r2")

    assert created_id == "r2"
    assert updated is True
    assert updated_doc["processing_message_id"] == "m1"
    assert membership_doc["room_agent_set"] == {"a1": "Agent One"}
    assert membership_doc["membership_origin"] == "saved_group"
    assert membership_doc["membership_origin_status"] == "seeded_never_edited"
    assert membership_doc["source_group_id"] == "g1"
    assert membership_doc["source_group_name"] == "Group One"
    assert deleted is True
    assert rooms.insert_one_calls == [{"room_id": "r2", "room_name": "New"}]
    assert rooms.update_one_calls[0] == (
        {
            "room_id": "r1",
            "$or": [
                {"lifecycle_state": "active"},
                {"lifecycle_state": {"$exists": False}},
            ],
        },
        {"$set": {"room_name": "Changed"}},
        {},
    )
    assert rooms.find_one_and_update_calls[-1][0] == {
        "room_id": "r1",
        "$or": [
            {"lifecycle_state": "active"},
            {"lifecycle_state": {"$exists": False}},
        ],
    }
    assert rooms.delete_one_calls == [{"room_id": "r2"}]
    assert all(isinstance(doc, dict) for doc in rooms.docs)


@pytest.mark.asyncio
async def test_message_repository_uses_room_message_collections_and_saves_raw_dicts():
    repo, mongo, user_messages, agent_messages = _message_repo()

    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    user_id = await repo.save_user_message(
        {"message_id": "u1", "room_id": "r1", "message_created_at": created_at}
    )
    agent_id = await repo.save_agent_message(
        {"message_id": "a1", "room_id": "r1", "message_created_at": created_at}
    )

    assert mongo.collection_calls == ["room_user_messages", "room_agent_messages"]
    assert user_id == "u1"
    assert agent_id == "a1"
    assert user_messages.insert_one_calls == [
        {
            "message_id": "u1",
            "room_id": "r1",
            "message_created_at": created_at,
            "timeline_sort_us": 1767225600000000,
        }
    ]
    assert agent_messages.insert_one_calls == [
        {
            "message_id": "a1",
            "room_id": "r1",
            "message_created_at": created_at,
            "timeline_sort_us": 1767225600000000,
        }
    ]


@pytest.mark.asyncio
async def test_legacy_user_message_save_normalizes_request_key_without_mutating_input():
    repo, _, user_messages, _ = _message_repo()
    message = {
        "message_id": "message-1",
        "room_id": "room-1",
        "client_request_id": "  request-1  ",
        "message_created_at": "2026-01-01T00:00:00Z",
    }

    message_id = await repo.save_user_message(message)

    assert message_id == "message-1"
    assert message["client_request_id"] == "  request-1  "
    assert user_messages.insert_one_calls == [
        {
            "message_id": "message-1",
            "room_id": "room-1",
            "client_request_id": "request-1",
            "message_created_at": "2026-01-01T00:00:00Z",
            "timeline_sort_us": 1767225600000000,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {"room_id": "room-1"},
        {"room_id": "room-1", "message_id": " "},
        {"room_id": " ", "message_id": "message-1"},
        {
            "room_id": "room-1",
            "message_id": "message-1",
            "client_request_id": " ",
        },
        {
            "room_id": "room-1",
            "message_id": "message-1",
            "client_request_id": 123,
        },
        {
            "room_id": "room-1",
            "message_id": "message-1",
            "client_request_id": "x" * 129,
        },
    ],
)
async def test_legacy_user_message_save_rejects_invalid_identity(message):
    repo, _, user_messages, _ = _message_repo()

    with pytest.raises(ValueError):
        await repo.save_user_message(message)

    assert user_messages.insert_one_calls == []


def _idempotent_message(
    *,
    room_id: str = "r1",
    client_request_id: str = "request-1",
    message_id: str = "message-1",
    fingerprint: str = "fingerprint-1",
) -> dict:
    return {
        "room_id": room_id,
        "message_id": message_id,
        "client_request_id": client_request_id,
        "idempotency_fingerprint": fingerprint,
        "idempotency_fingerprint_version": 1,
        "message_type": "user",
        "message_content": {"message_text": "hello"},
        "message_created_at": "2026-01-01T00:00:00Z",
    }


def _idempotent_message_repo(
    docs: list[dict] | None = None,
) -> tuple[MessageMongoRepository, AtomicUniqueUserMessageCollection]:
    user_messages = AtomicUniqueUserMessageCollection(docs)
    mongo = FakeMongo(
        {
            "room_user_messages": user_messages,
            "room_agent_messages": FakeCollection(),
        }
    )
    return MessageMongoRepository(mongo=mongo), user_messages


@pytest.mark.asyncio
async def test_legacy_save_and_canonical_insert_share_normalized_unique_key():
    repo, user_messages = _idempotent_message_repo()
    legacy = _idempotent_message(client_request_id=" request-1 ")
    legacy.pop("idempotency_fingerprint")
    legacy.pop("idempotency_fingerprint_version")

    await repo.save_user_message(legacy)
    replayed = await repo.insert_user_message_idempotently(
        _idempotent_message(message_id="message-2")
    )

    assert replayed.created is False
    assert replayed.message_id == "message-1"
    assert len(user_messages.docs) == 1
    assert user_messages.docs[0]["client_request_id"] == "request-1"


@pytest.mark.asyncio
async def test_idempotent_user_message_insert_creates_then_replays_stable_winner():
    repo, user_messages = _idempotent_message_repo()

    created = await repo.insert_user_message_idempotently(_idempotent_message())
    replayed = await repo.insert_user_message_idempotently(
        _idempotent_message(message_id="message-loser")
    )

    assert created.created is True
    assert created.message_id == "message-1"
    assert replayed.created is False
    assert replayed.message_id == "message-1"
    assert replayed.document["message_id"] == "message-1"
    assert len(user_messages.docs) == 1
    assert user_messages.find_one_calls[-1] == {
        "room_id": "r1",
        "client_request_id": "request-1",
    }


@pytest.mark.asyncio
async def test_idempotent_user_message_insert_rejects_payload_conflict():
    repo, user_messages = _idempotent_message_repo([_idempotent_message()])

    with pytest.raises(IdempotencyConflictError):
        await repo.insert_user_message_idempotently(
            _idempotent_message(
                message_id="message-2",
                fingerprint="different-fingerprint",
            )
        )

    assert len(user_messages.docs) == 1


@pytest.mark.asyncio
async def test_message_id_unique_collision_is_not_misclassified_as_replay():
    repo, user_messages = _idempotent_message_repo([_idempotent_message()])

    with pytest.raises(
        UnexpectedUserMessageDuplicateError,
        match="Unexpected user-message unique-index collision",
    ):
        await repo.insert_user_message_idempotently(
            _idempotent_message(
                room_id="r2",
                client_request_id="request-2",
                message_id="message-1",
            )
        )

    assert user_messages.find_one_calls[-1] == {
        "room_id": "r2",
        "client_request_id": "request-2",
    }


@pytest.mark.asyncio
async def test_same_client_request_id_is_allowed_in_different_rooms():
    repo, user_messages = _idempotent_message_repo()

    first = await repo.insert_user_message_idempotently(_idempotent_message())
    second = await repo.insert_user_message_idempotently(
        _idempotent_message(room_id="r2", message_id="message-2")
    )

    assert first.created is True
    assert second.created is True
    assert len(user_messages.docs) == 2


@pytest.mark.asyncio
async def test_idempotent_repository_enforces_normalized_request_id_length_boundary():
    repo, user_messages = _idempotent_message_repo()

    accepted = await repo.insert_user_message_idempotently(
        _idempotent_message(client_request_id="x" * 128)
    )
    normalized = await repo.insert_user_message_idempotently(
        _idempotent_message(
            client_request_id=" request-with-padding ",
            message_id="message-2",
        )
    )
    with pytest.raises(ValueError, match="valid client_request_id"):
        await repo.insert_user_message_idempotently(
            _idempotent_message(
                client_request_id="y" * 129,
                message_id="message-3",
            )
        )

    assert accepted.created is True
    assert normalized.created is True
    assert user_messages.docs[1]["client_request_id"] == "request-with-padding"
    assert len(user_messages.docs) == 2


@pytest.mark.asyncio
async def test_idempotent_user_message_insert_requires_persisted_message_id():
    repo, user_messages = _idempotent_message_repo()
    message = _idempotent_message()
    del message["message_id"]

    with pytest.raises(ValueError, match="non-empty message_id"):
        await repo.insert_user_message_idempotently(message)

    assert user_messages.docs == []


@pytest.mark.asyncio
async def test_concurrent_duplicate_key_loser_reads_and_returns_winner_message_id():
    repo, user_messages = _idempotent_message_repo()

    first, second = await asyncio.gather(
        repo.insert_user_message_idempotently(
            _idempotent_message(message_id="message-a")
        ),
        repo.insert_user_message_idempotently(
            _idempotent_message(message_id="message-b")
        ),
    )

    assert sorted((first.created, second.created)) == [False, True]
    assert first.message_id == second.message_id
    assert first.message_id in {"message-a", "message-b"}
    assert len(user_messages.docs) == 1


@pytest.mark.asyncio
async def test_user_message_update_cannot_rewrite_persistent_identity_fields():
    repo, _, user_messages, _ = _message_repo(
        user_docs=[
            {
                "room_id": "room-1",
                "message_id": "message-1",
                "client_request_id": "request-1",
                "idempotency_fingerprint": "fingerprint-1",
                "idempotency_fingerprint_version": 1,
            }
        ]
    )

    updated = await repo.update_user_message(
        "message-1",
        {
            "room_id": "other-room",
            "message_id": "other-message",
            "client_request_id": " padded-request ",
            "idempotency_fingerprint": "other-fingerprint",
            "idempotency_fingerprint_version": 2,
            "message_content": {"message_text": "updated"},
        },
    )

    assert updated is True
    assert user_messages.update_one_calls == [
        (
            {"message_id": "message-1"},
            {"$set": {"message_content": {"message_text": "updated"}}},
            {},
        )
    ]
    assert user_messages.docs[0]["room_id"] == "room-1"
    assert user_messages.docs[0]["message_id"] == "message-1"
    assert user_messages.docs[0]["client_request_id"] == "request-1"
    assert user_messages.docs[0]["idempotency_fingerprint"] == "fingerprint-1"


@pytest.mark.asyncio
async def test_message_updates_strip_immutable_timeline_identity():
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    repo, _, users, agents = _message_repo(
        user_docs=[{"message_id": "u1", "room_id": "r1"}],
        agent_docs=[{"message_id": "a1", "room_id": "r1"}],
    )
    updates = {
        "message_id": "rewritten",
        "room_id": "other",
        "message_created_at": created_at,
        "timeline_sort_us": 1,
        "message_content": {"message_text": "allowed"},
    }

    assert await repo.update_user_message("u1", updates) is True
    assert await repo.update_agent_message("a1", updates) is True
    assert users.update_one_calls[-1][1]["$set"] == {
        "message_content": {"message_text": "allowed"}
    }
    assert agents.update_one_calls[-1][1]["$set"] == {
        "message_content": {"message_text": "allowed"}
    }


@pytest.mark.asyncio
async def test_message_repository_get_by_id_searches_user_first_then_agent():
    repo, _, user_messages, agent_messages = _message_repo(
        user_docs=[{"message_id": "u1", "message_type": "user"}],
        agent_docs=[{"message_id": "a1", "message_type": "agent"}],
    )

    assert await repo.get_by_id("u1") == {"message_id": "u1", "message_type": "user"}
    assert await repo.get_by_id("a1") == {"message_id": "a1", "message_type": "agent"}
    assert user_messages.find_one_calls == [
        {"message_id": "u1"},
        {"message_id": "a1"},
    ]
    assert agent_messages.find_one_calls == [{"message_id": "a1"}]


@pytest.mark.asyncio
async def test_message_repository_get_by_ids_combines_collections_and_preserves_order():
    repo, _, _, _ = _message_repo(
        user_docs=[{"message_id": "u1"}, {"message_id": "u2"}],
        agent_docs=[{"message_id": "a1"}],
    )

    assert await repo.get_by_ids(["a1", "missing", "u2", "u1"]) == [
        {"message_id": "a1"},
        {"message_id": "u2"},
        {"message_id": "u1"},
    ]


@pytest.mark.asyncio
async def test_message_repository_direct_room_history_methods_pass_limits():
    repo, _, user_messages, agent_messages = _message_repo(
        user_docs=[{"message_id": "u1", "room_id": "r1"}],
        agent_docs=[{"message_id": "a1", "room_id": "r1"}],
    )

    assert await repo.get_user_messages_for_room("r1", limit=3) == [
        {"message_id": "u1", "room_id": "r1"}
    ]
    assert await repo.get_agent_messages_for_room("r1", limit=4) == [
        {"message_id": "a1", "room_id": "r1"}
    ]
    expected_sort = [("timeline_sort_us", -1), ("message_id", -1)]
    assert user_messages.find_calls[-1] == (
        {"room_id": "r1"},
        {"sort": expected_sort, "limit": 3},
    )
    assert agent_messages.find_calls[-1] == (
        {"room_id": "r1"},
        {"sort": expected_sort, "limit": 4},
    )


@pytest.mark.asyncio
async def test_message_repository_task_room_query_filters_sorts_and_limits():
    older = datetime(2026, 5, 10, tzinfo=UTC)
    newer = datetime(2026, 5, 11, tzinfo=UTC)
    repo, _, _, agent_messages = _message_repo(
        agent_docs=[
            {
                "message_id": "tracked-old",
                "room_id": "r1",
                "has_task_tracking": True,
                "task_created_at": older,
            },
            {
                "message_id": "tracked-new",
                "room_id": "r1",
                "has_task_tracking": True,
                "task_created_at": newer,
            },
            {
                "message_id": "untracked",
                "room_id": "r1",
                "has_task_tracking": False,
                "task_created_at": newer,
            },
            {
                "message_id": "other-room",
                "room_id": "r2",
                "has_task_tracking": True,
                "task_created_at": newer,
            },
        ]
    )

    docs = await repo.get_task_messages_for_room("r1", limit=1)

    assert [doc["message_id"] for doc in docs] == ["tracked-new"]
    assert agent_messages.find_calls[-1] == (
        {"room_id": "r1", "has_task_tracking": True},
        {"sort": [("task_created_at", -1)], "limit": 1},
    )


@pytest.mark.asyncio
async def test_message_repository_pending_user_tasks_filters_and_sorts():
    older = datetime(2026, 5, 10, tzinfo=UTC)
    newer = datetime(2026, 5, 11, tzinfo=UTC)
    repo, _, _, agent_messages = _message_repo(
        agent_docs=[
            {
                "message_id": "old",
                "user_id": "u1",
                "has_task_tracking": True,
                "task_created_at": older,
                "message_content": {"message_task": {"status": {"state": "working"}}},
            },
            {
                "message_id": "new",
                "user_id": "u1",
                "has_task_tracking": True,
                "task_created_at": newer,
                "message_content": {"message_task": {"status": {"state": "working"}}},
            },
            {
                "message_id": "done",
                "user_id": "u1",
                "has_task_tracking": True,
                "task_created_at": newer,
                "message_content": {"message_task": {"status": {"state": "completed"}}},
            },
        ]
    )

    docs = await repo.get_pending_task_messages_for_user("u1", ["working"])

    assert [doc["message_id"] for doc in docs] == ["new", "old"]
    assert agent_messages.find_calls[-1] == (
        {
            "user_id": "u1",
            "has_task_tracking": True,
            "message_content.message_task.status.state": {"$in": ["working"]},
        },
        {"sort": [("task_created_at", -1)]},
    )


@pytest.mark.asyncio
async def test_runtime_store_updates_last_notified_state_atomically():
    from dal.runtime_store import RuntimeRepositoryStore

    agent_messages = FakeCollection(
        [{"message_id": "a1", "last_notified_state": "working"}]
    )
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    assert await store.update_last_notified_state("a1", "completed") is True
    assert agent_messages.docs[0]["last_notified_state"] == "completed"
    assert agent_messages.update_one_calls == [
        (
            {"message_id": "a1", "last_notified_state": {"$ne": "completed"}},
            {"$set": {"last_notified_state": "completed"}},
            {},
        )
    ]
    assert await store.update_last_notified_state("a1", "completed") is False


@pytest.mark.asyncio
async def test_runtime_store_accumulates_artifacts_with_atomic_collection_update():
    from dal.runtime_store import RuntimeRepositoryStore

    class RecordingSuccessCollection(FakeCollection):
        async def update_one(self, query: dict, update: dict, **kwargs) -> bool:
            self.update_one_calls.append(
                (deepcopy(query), deepcopy(update), deepcopy(kwargs))
            )
            return True

    agent_messages = RecordingSuccessCollection()
    mongo = FakeMongo({"room_agent_messages": agent_messages})
    store = RuntimeRepositoryStore(
        mongo=mongo,
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    assert await store.accumulate_artifact_on_message(
        "a1",
        {
            "artifactId": "artifact-1",
            "parts": [{"kind": "text", "text": " world"}],
        },
        append=True,
    )

    query, update, _ = agent_messages.update_one_calls[-1]
    assert query["message_content.message_task.status.state"]["$nin"]
    assert query["message_content.message_task.artifacts"]["$elemMatch"]
    assert isinstance(update, list)
    set_stage = update[0]["$set"]
    assert set_stage["message_content.message_task.status.state"] == "working"
    assert "$map" in set_stage["message_content.message_task.artifacts"]
    assert "$concat" in set_stage["message_content.message_text"]


@pytest.mark.asyncio
async def test_runtime_store_filters_malformed_related_agent_messages():
    from dal.runtime_store import RuntimeRepositoryStore

    class RelatedMessageRepository:
        async def get_agent_messages_by_related_message_id(self, related_message_id):
            return [
                {"message_id": "bad"},
                {
                    "message_id": "a1",
                    "room_id": "r1",
                    "message_type": "agent",
                    "agent_id": "agent-1",
                    "message_created_at": datetime(2026, 5, 11, tzinfo=UTC),
                    "related_message_id": related_message_id,
                    "message_content": {"message_text": "ok"},
                },
            ]

    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=object(),
        message_repository=RelatedMessageRepository(),
        agent_repository=object(),
    )

    messages = await store.get_room_agent_messages_by_related_message_id("u1")

    assert [message.message_id for message in messages] == ["a1"]


@pytest.mark.asyncio
async def test_runtime_store_full_agent_update_preserves_task_tracking_fields():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    class RecordingMessageRepository:
        def __init__(self) -> None:
            self.update_calls: list[tuple[str, dict]] = []

        async def update_agent_message(self, message_id, updates):
            self.update_calls.append((message_id, updates))
            return True

    message_repository = RecordingMessageRepository()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=object(),
        message_repository=message_repository,
        agent_repository=object(),
    )
    message = RuntimeRoomAgentMessage(
        room_id="r1",
        message_id="a1",
        agent_id="agent-1",
        message_created_at=datetime(2026, 5, 11, tzinfo=UTC),
        message_content=RuntimeMessageContent(message_text="updated"),
    )

    assert await store.update_room_agent_message_by_message_id("a1", message)

    _, updates = message_repository.update_calls[0]
    assert "webhook_token_hash" not in updates
    assert "pending_continuation" not in updates
    assert "last_notified_state" not in updates
    assert "agent_url" not in updates
    assert "task_created_at" not in updates
    assert "task_updated_at" not in updates
    assert "task_content" not in updates
    assert "has_task_tracking" not in updates
    assert "parent_message_id" not in updates
    assert "run_id" not in updates
    assert "client_request_id" not in updates
    assert "related_message_id" not in updates
    assert "step_number" not in updates
    assert "total_steps" not in updates
    assert "extend_info" not in updates
    assert "turn_id" not in updates


@pytest.mark.asyncio
async def test_runtime_store_generates_agent_message_id_when_empty():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    class RecordingMessageRepository:
        def __init__(self) -> None:
            self.saved_docs: list[dict] = []

        async def save_agent_message(self, doc):
            self.saved_docs.append(doc)
            return "saved"

    message_repository = RecordingMessageRepository()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=object(),
        message_repository=message_repository,
        agent_repository=object(),
    )
    message = RuntimeRoomAgentMessage(
        room_id="r1",
        message_id="",
        agent_id="agent-1",
        message_created_at=datetime(2026, 5, 11, tzinfo=UTC),
        message_content=RuntimeMessageContent(message_text="created"),
    )

    assert await store.add_room_agent_message(message)
    assert message.message_id == ""
    assert message_repository.saved_docs[0]["message_id"]


@pytest.mark.asyncio
async def test_runtime_store_task_tracking_writes_return_false_on_repository_error():
    from dal.runtime_store import RuntimeRepositoryStore

    class FailingMessageRepository:
        async def update_agent_message(self, *args, **kwargs):
            raise RuntimeError("database down")

        async def update_agent_message_if_not_terminal(self, *args, **kwargs):
            raise RuntimeError("database down")

    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=object(),
        message_repository=FailingMessageRepository(),
        agent_repository=object(),
    )

    assert (
        await store.enable_task_tracking_on_message(
            message_id="a1",
            webhook_token_hash="hash",
            agent_url="https://agent.example",
            task_created_at=datetime(2026, 5, 11, tzinfo=UTC),
            task_updated_at=datetime(2026, 5, 11, tzinfo=UTC),
            task_data={"id": "task-1"},
        )
        is False
    )
    assert await store.update_task_on_message("a1", {"id": "task-1"}) is False
    assert await store.update_webhook_token_hash_on_message("a1", "hash") is False


@pytest.mark.asyncio
async def test_runtime_store_task_tracking_noop_successes_by_readback():
    from dal.runtime_store import RuntimeRepositoryStore

    class NoopTrackedMessageRepository:
        async def update_agent_message(self, message_id, updates):
            return False

        async def get_agent_message_by_id(self, message_id):
            return {
                "message_id": message_id,
                "has_task_tracking": True,
                "webhook_token_hash": "hash",
                "agent_url": "https://agent.example",
                "message_content": {"message_task": {"id": "task-1"}},
            }

    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=object(),
        message_repository=NoopTrackedMessageRepository(),
        agent_repository=object(),
    )

    assert (
        await store.enable_task_tracking_on_message(
            message_id="a1",
            webhook_token_hash="hash",
            agent_url="https://agent.example",
            task_created_at=datetime(2026, 5, 11, tzinfo=UTC),
            task_updated_at=datetime(2026, 5, 11, tzinfo=UTC),
            task_data={"id": "task-1"},
        )
        is True
    )


@pytest.mark.asyncio
async def test_runtime_store_chat_context_mutations_succeed_on_no_exception():
    from common.dto import RuntimeChatContext
    from dal.runtime_store import RuntimeRepositoryStore

    class NoopCollection(FakeCollection):
        async def update_one(self, query: dict, update: dict, **kwargs) -> bool:
            self.update_one_calls.append(
                (deepcopy(query), deepcopy(update), deepcopy(kwargs))
            )
            return False

        async def delete_one(self, query: dict) -> bool:
            self.delete_one_calls.append(deepcopy(query))
            return False

    chat_contexts = NoopCollection()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"chat_contexts": chat_contexts}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )
    context = RuntimeChatContext(memory_id="m1", user_name="User", session_id="s1")

    assert await store.update_chat_context_by_session_id("s1", context) is True
    assert await store.delete_chat_context_by_session_id("s1") is True
    assert chat_contexts.update_one_calls
    assert chat_contexts.delete_one_calls == [{"session_id": "s1"}]


@pytest.mark.asyncio
async def test_runtime_store_memory_write_methods_use_expected_dependencies():
    from dal.runtime_store import RuntimeRepositoryStore

    class RecordingUpsertCollection(FakeCollection):
        async def update_one(self, query: dict, update: dict, **kwargs) -> bool:
            self.update_one_calls.append(
                (deepcopy(query), deepcopy(update), deepcopy(kwargs))
            )
            return True

    class RoomRepositoryWithTurnNotes:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict]] = []

        async def update_turn_notes(
            self,
            room_id: str,
            turn_id: str,
            turn_notes: dict,
        ) -> bool:
            self.calls.append((room_id, turn_id, deepcopy(turn_notes)))
            return True

    class FailingRoomRepository:
        async def update_turn_notes(self, *args, **kwargs):
            raise RuntimeError("database down")

    user_memories = RecordingUpsertCollection()
    agent_memories = RecordingUpsertCollection()
    room_repository = RoomRepositoryWithTurnNotes()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo(
            {
                "user_memories": user_memories,
                "agent_memories": agent_memories,
            }
        ),
        room_repository=room_repository,
        message_repository=object(),
        agent_repository=object(),
    )

    assert await store.increment_user_interactions("user-1") is True
    query, update, kwargs = user_memories.update_one_calls[0]
    assert query == {"user_id": "user-1"}
    assert update["$inc"] == {"total_interactions": 1}
    assert set(update["$set"]) == {"last_active_at"}
    assert update["$setOnInsert"]["user_id"] == "user-1"
    assert "created_at" in update["$setOnInsert"]
    assert kwargs == {"upsert": True}

    assert (
        await store.record_agent_call(
            agent_id="agent-1",
            success=True,
        )
        is True
    )
    query, update, kwargs = agent_memories.update_one_calls[0]
    assert query == {"agent_id": "agent-1"}
    assert update["$inc"] == {
        "total_calls": 1,
        "total_response_time_ms": 0.0,
        "successful_calls": 1,
    }
    assert set(update["$set"]) == {"last_called_at"}
    assert update["$setOnInsert"] == {"agent_id": "agent-1"}
    assert kwargs == {"upsert": True}

    assert (
        await store.record_agent_call(
            agent_id="agent-1",
            success=False,
            response_time_ms=12.5,
        )
        is True
    )
    _, failed_update, _ = agent_memories.update_one_calls[1]
    assert failed_update["$inc"] == {
        "total_calls": 1,
        "total_response_time_ms": 12.5,
    }

    turn_notes = {"summary": "note"}
    assert await store.update_turn_notes("room-1", "turn-1", turn_notes) is True
    assert room_repository.calls == [("room-1", "turn-1", turn_notes)]

    no_method_store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )
    assert await no_method_store.update_turn_notes("room-1", "turn-1", {}) is False

    failing_store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=FailingRoomRepository(),
        message_repository=object(),
        agent_repository=object(),
    )
    assert await failing_store.update_turn_notes("room-1", "turn-1", {}) is False


@pytest.mark.asyncio
async def test_runtime_store_generates_chat_context_memory_id_when_empty():
    from common.dto import RuntimeChatContext
    from dal.runtime_store import RuntimeRepositoryStore

    chat_contexts = FakeCollection()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"chat_contexts": chat_contexts}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )
    context = RuntimeChatContext(memory_id="", user_name="User", session_id="s1")

    assert await store.add_chat_context(context) is True
    assert context.memory_id == ""
    assert chat_contexts.insert_one_calls[0]["memory_id"]


@pytest.mark.asyncio
async def test_runtime_store_room_runtime_methods_use_repositories_and_dal():
    from common.dto import (
        RuntimeMessageContent,
        RuntimeRoomRecord,
        RuntimeRoomUserMessage,
    )
    from dal.runtime_store import RuntimeRepositoryStore

    room_repo, mongo, rooms = _room_repo(
        [
            {
                "room_id": "r1",
                "room_owner_id": "owner-1",
                "room_owner_name": "Owner",
                "room_name": "Room",
            }
        ]
    )
    message_repo = MessageMongoRepository(mongo=mongo)
    agents = FakeCollection(
        [
            {
                "agent_id": "agent-1",
                "agent_card": {
                    "name": "Agent",
                    "url": "https://agent.example/.well-known/agent.json",
                    "version": "1.0",
                    "capabilities": {},
                    "skills": [],
                },
                "is_public": True,
            }
        ]
    )
    room_memories = FakeCollection(
        [{"memory_id": "mem-1", "room_id": "r1", "room_memory": "summary"}]
    )
    runs = FakeCollection(
        [
            {
                "run_id": "old",
                "room_id": "r1",
                "state": "processing",
                "updated_at": 1,
            },
            {"run_id": "new", "room_id": "r1", "state": "queued", "updated_at": 2},
            {"run_id": "done", "room_id": "r1", "state": "completed", "updated_at": 3},
        ]
    )
    mongo.collections.update(
        {
            "agents": agents,
            "room_memories": room_memories,
            "runs": runs,
        }
    )
    store = RuntimeRepositoryStore(
        mongo=mongo,
        room_repository=room_repo,
        message_repository=message_repo,
        agent_repository=object(),
    )

    assert [
        room.room_id for room in await store.get_rooms_by_room_owner_id("owner-1")
    ] == ["r1"]
    room = RuntimeRoomRecord(
        room_id="r1",
        room_owner_id="owner-1",
        room_owner_name="Owner",
        room_name="Renamed",
    )
    assert await store.update_room_by_room_id("r1", room) is True
    assert rooms.docs[0]["room_name"] == "Renamed"
    user_message = RuntimeRoomUserMessage(
        room_id="r1",
        message_id="u1",
        user_id="owner-1",
        message_created_at=datetime(2026, 5, 11, tzinfo=UTC),
        message_content=RuntimeMessageContent(message_text="hello"),
    )

    assert await store.add_room_user_message(user_message) is True
    assert [
        msg.message_id for msg in await store.get_room_user_messages_by_room_id("r1")
    ] == ["u1"]
    updated_user_message = RuntimeRoomUserMessage(
        room_id="r1",
        message_id="u1",
        user_id="owner-1",
        message_created_at=datetime(2026, 5, 11, tzinfo=UTC),
        message_content=RuntimeMessageContent(message_text="updated"),
    )
    assert await store.update_room_user_message_by_message_id(
        "u1", updated_user_message
    )
    assert (
        mongo.collections["room_user_messages"].docs[0]["message_content"][
            "message_text"
        ]
        == "updated"
    )
    assert [agent.agent_id for agent in await store.get_agents_with_conditions()] == [
        "agent-1"
    ]
    assert (await store.get_room_memory_by_room_id("r1")).memory_id == "mem-1"
    assert [run["run_id"] for run in await store.get_active_runs_by_room_id("r1")] == [
        "new",
        "old",
    ]


@pytest.mark.asyncio
async def test_runtime_store_room_update_noop_succeeds_when_room_exists():
    from common.dto import RuntimeRoomRecord
    from dal.runtime_store import RuntimeRepositoryStore

    class NoopRoomRepository:
        async def update(self, room_id: str, updates: dict) -> bool:
            return False

        async def get_by_id(self, room_id: str) -> dict | None:
            return {
                "room_id": room_id,
                "room_owner_id": "owner-1",
                "room_owner_name": "Owner",
                "room_name": "Room",
            }

    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=NoopRoomRepository(),
        message_repository=object(),
        agent_repository=object(),
    )

    assert await store.update_room_by_room_id(
        "r1",
        RuntimeRoomRecord(
            room_id="r1",
            room_owner_id="owner-1",
            room_owner_name="Owner",
            room_name="Room",
        ),
    )


@pytest.mark.asyncio
async def test_runtime_store_sparse_room_update_preserves_membership():
    from common.dto import RuntimeRoomRecord
    from dal.runtime_store import RuntimeRepositoryStore

    room_repo, _, rooms = _room_repo(
        [
            {
                "room_id": "r1",
                "room_owner_id": "owner-1",
                "room_owner_name": "Owner",
                "room_name": "Old",
                "room_agent_set": {"agent-1": "Agent One"},
            }
        ]
    )
    store = RuntimeRepositoryStore(
        mongo=FakeMongo(),
        room_repository=room_repo,
        message_repository=object(),
        agent_repository=object(),
    )

    update = RuntimeRoomRecord(
        room_id="r1",
        room_owner_id="owner-1",
        room_owner_name="Owner",
        room_name="Renamed",
    )

    assert await store.update_room_by_room_id("r1", update) is True
    assert rooms.docs[0]["room_name"] == "Renamed"
    assert rooms.docs[0]["room_agent_set"] == {"agent-1": "Agent One"}

    _, update_doc, _ = rooms.update_one_calls[-1]
    assert "room_agent_set" not in update_doc["$set"]


@pytest.mark.asyncio
async def test_runtime_store_upsert_room_agent_message_preserves_timeline_identity():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    agent_messages = FakeCollection(
        [
            {
                "message_id": "summary-1",
                "room_id": "r1",
                "message_type": "agent",
                "agent_id": "agent-1",
                "message_created_at": datetime(2026, 5, 10, tzinfo=UTC),
                "timeline_sort_us": 1778371200000000,
                "message_content": {"message_text": "old"},
                "orphan_field": "must be removed",
            }
        ]
    )
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    await store.upsert_room_agent_message(
        RuntimeRoomAgentMessage(
            room_id="r1",
            message_id="summary-1",
            agent_id="agent-1",
            message_created_at=datetime(2026, 5, 11, tzinfo=UTC),
            message_content=RuntimeMessageContent(message_text="new"),
        )
    )

    assert agent_messages.update_one_calls == []
    assert len(agent_messages.replace_one_calls) == 1
    replace_filter, replacement, kwargs = agent_messages.replace_one_calls[0]
    assert replace_filter == {
        "room_id": "r1",
        "message_id": "summary-1",
        "message_created_at": datetime(2026, 5, 10, tzinfo=UTC),
        "timeline_sort_us": 1778371200000000,
    }
    assert kwargs == {"upsert": False}
    assert replacement["message_content"]["message_text"] == "new"
    assert "orphan_field" not in agent_messages.docs[0]
    assert agent_messages.docs[0]["message_created_at"] == datetime(
        2026, 5, 10, tzinfo=UTC
    )
    assert agent_messages.docs[0]["timeline_sort_us"] == 1778371200000000


@pytest.mark.asyncio
async def test_runtime_store_upsert_room_agent_message_new_insert_sets_timeline_key():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    agent_messages = FakeCollection()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    await store.upsert_room_agent_message(
        RuntimeRoomAgentMessage(
            room_id="r1",
            message_id="new-agent",
            agent_id="agent-1",
            message_created_at=datetime(1970, 1, 1, 0, 0, 1, 123456, tzinfo=UTC),
            message_content=RuntimeMessageContent(message_text="new"),
        )
    )

    assert agent_messages.replace_one_calls == []
    assert len(agent_messages.insert_one_calls) == 1
    assert agent_messages.insert_one_calls[0]["timeline_sort_us"] == 1_123_456
    assert agent_messages.docs[0]["timeline_sort_us"] == 1_123_456


@pytest.mark.asyncio
async def test_runtime_store_upsert_room_agent_message_propagates_insert_failure():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    class FailingInsertCollection(FakeCollection):
        async def insert_one(self, document: dict) -> str:
            raise RuntimeError("insert failed")

    agent_messages = FailingInsertCollection()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    with pytest.raises(RuntimeError, match="insert failed"):
        await store.upsert_room_agent_message(
            RuntimeRoomAgentMessage(
                room_id="r1",
                message_id="new-agent",
                agent_id="agent-1",
                message_created_at=datetime(1970, 1, 1, tzinfo=UTC),
                message_content=RuntimeMessageContent(message_text="new"),
            )
        )

    assert agent_messages.docs == []
    assert agent_messages.replace_one_calls == []


@pytest.mark.asyncio
async def test_runtime_store_upsert_room_agent_message_first_insert_race_preserves_winner_identity():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    winner = {
        "message_id": "raced-insert",
        "room_id": "r1",
        "message_type": "agent",
        "agent_id": "agent-winner",
        "message_created_at": "1970-01-01T00:00:01Z",
        "timeline_sort_us": 1_000_000,
        "message_content": {"message_text": "winner"},
        "stale_field": "remove",
    }

    class RacingInsertCollection(FakeCollection):
        async def insert_one(self, document: dict) -> str:
            self.insert_one_calls.append(deepcopy(document))
            self.docs.append(deepcopy(winner))
            raise DuplicateKeyError("concurrent winner")

    agent_messages = RacingInsertCollection()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    await store.upsert_room_agent_message(
        RuntimeRoomAgentMessage(
            room_id="r1",
            message_id="raced-insert",
            agent_id="agent-incoming",
            message_created_at=datetime(1970, 1, 2, tzinfo=UTC),
            message_content=RuntimeMessageContent(message_text="incoming"),
        )
    )

    assert len(agent_messages.insert_one_calls) == 1
    assert len(agent_messages.replace_one_calls) == 1
    assert agent_messages.docs[0]["room_id"] == "r1"
    assert agent_messages.docs[0]["message_id"] == "raced-insert"
    assert agent_messages.docs[0]["message_created_at"] == winner["message_created_at"]
    assert agent_messages.docs[0]["timeline_sort_us"] == winner["timeline_sort_us"]
    assert agent_messages.docs[0]["message_content"]["message_text"] == "incoming"
    assert "stale_field" not in agent_messages.docs[0]


@pytest.mark.asyncio
async def test_runtime_store_upsert_room_agent_message_first_insert_cross_room_race_is_safe():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    winner = {
        "message_id": "shared-raced-insert",
        "room_id": "room-a",
        "message_type": "agent",
        "agent_id": "agent-a",
        "message_created_at": "1970-01-01T00:00:00Z",
        "timeline_sort_us": 0,
        "message_content": {"message_text": "private-a"},
    }

    class CrossRoomRacingInsertCollection(FakeCollection):
        async def insert_one(self, document: dict) -> str:
            self.insert_one_calls.append(deepcopy(document))
            self.docs.append(deepcopy(winner))
            raise DuplicateKeyError("concurrent cross-room winner")

    agent_messages = CrossRoomRacingInsertCollection()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    with pytest.raises(ValueError, match="room_id mismatch"):
        await store.upsert_room_agent_message(
            RuntimeRoomAgentMessage(
                room_id="room-b",
                message_id="shared-raced-insert",
                agent_id="agent-b",
                message_created_at=datetime(1970, 1, 2, tzinfo=UTC),
                message_content=RuntimeMessageContent(message_text="private-b"),
            )
        )

    assert agent_messages.docs == [winner]
    assert len(agent_messages.insert_one_calls) == 1
    assert agent_messages.replace_one_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("room_id", "message_id"),
    [("", "message-1"), ("   ", "message-1"), ("room-1", "")],
)
async def test_runtime_store_upsert_room_agent_message_rejects_blank_identity(
    room_id: str,
    message_id: str,
):
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    agent_messages = FakeCollection()
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    with pytest.raises(ValueError, match="requires non-empty"):
        await store.upsert_room_agent_message(
            RuntimeRoomAgentMessage(
                room_id=room_id,
                message_id=message_id,
                agent_id="agent-1",
                message_created_at=datetime(1970, 1, 1, tzinfo=UTC),
                message_content=RuntimeMessageContent(message_text="invalid"),
            )
        )

    assert agent_messages.find_one_calls == []
    assert agent_messages.insert_one_calls == []
    assert agent_messages.replace_one_calls == []


@pytest.mark.asyncio
async def test_runtime_store_upsert_room_agent_message_rejects_cross_room_collision():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    original = {
        "message_id": "shared-id",
        "room_id": "room-a",
        "message_type": "agent",
        "agent_id": "agent-a",
        "message_created_at": "1970-01-01T00:00:00Z",
        "timeline_sort_us": 0,
        "message_content": {"message_text": "private-a"},
    }
    agent_messages = FakeCollection([original])
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    with pytest.raises(ValueError, match="room_id mismatch"):
        await store.upsert_room_agent_message(
            RuntimeRoomAgentMessage(
                room_id="room-b",
                message_id="shared-id",
                agent_id="agent-b",
                message_created_at=datetime(1970, 1, 2, tzinfo=UTC),
                message_content=RuntimeMessageContent(message_text="private-b"),
            )
        )

    assert agent_messages.docs == [original]
    assert agent_messages.replace_one_calls == []
    assert agent_messages.update_one_calls == []


@pytest.mark.asyncio
async def test_runtime_store_upsert_room_agent_message_propagates_false_cas():
    from common.dto import RuntimeMessageContent, RuntimeRoomAgentMessage
    from dal.runtime_store import RuntimeRepositoryStore

    class RacingCollection(FakeCollection):
        async def replace_one(self, query: dict, replacement: dict, **kwargs) -> bool:
            self.replace_one_calls.append(
                (deepcopy(query), deepcopy(replacement), deepcopy(kwargs))
            )
            self.docs[0]["message_created_at"] = "1970-01-01T00:00:01Z"
            self.docs[0]["timeline_sort_us"] = 1_000_000
            return False

    agent_messages = RacingCollection(
        [
            {
                "message_id": "raced",
                "room_id": "r1",
                "message_type": "agent",
                "agent_id": "agent-1",
                "message_created_at": "1970-01-01T00:00:00Z",
                "timeline_sort_us": 0,
                "message_content": {"message_text": "concurrent"},
            }
        ]
    )
    store = RuntimeRepositoryStore(
        mongo=FakeMongo({"room_agent_messages": agent_messages}),
        room_repository=object(),
        message_repository=object(),
        agent_repository=object(),
    )

    with pytest.raises(RuntimeError, match="immutable-identity race"):
        await store.upsert_room_agent_message(
            RuntimeRoomAgentMessage(
                room_id="r1",
                message_id="raced",
                agent_id="agent-1",
                message_created_at=datetime(1970, 1, 2, tzinfo=UTC),
                message_content=RuntimeMessageContent(message_text="incoming"),
            )
        )

    assert agent_messages.docs[0]["message_content"]["message_text"] == "concurrent"
    assert agent_messages.docs[0]["timeline_sort_us"] == 1_000_000


@pytest.mark.asyncio
async def test_runtime_store_room_orchestration_claim_cancel_and_continuation():
    from dal.runtime_store import RuntimeRepositoryStore

    user_messages = FakeCollection(
        [{"message_id": "u1", "room_id": "r1", "processing_claimed_at": None}]
    )
    agent_messages = FakeCollection(
        [
            {
                "message_id": "a1",
                "room_id": "r1",
                "related_message_id": "u1",
                "message_content": {"message_task": {"status": {"state": "working"}}},
            },
            {
                "message_id": "a2",
                "room_id": "r1",
                "related_message_id": "a1",
                "message_content": {"message_task": {"status": {"state": "submitted"}}},
            },
            {
                "message_id": "done",
                "room_id": "r1",
                "related_message_id": "u1",
                "message_content": {"message_task": {"status": {"state": "completed"}}},
            },
            {
                "message_id": "after-done",
                "room_id": "r1",
                "related_message_id": "done",
                "message_content": {"message_task": {"status": {"state": "working"}}},
            },
        ]
    )
    mongo = FakeMongo(
        {
            "room_user_messages": user_messages,
            "room_agent_messages": agent_messages,
        }
    )
    store = RuntimeRepositoryStore(
        mongo=mongo,
        room_repository=object(),
        message_repository=MessageMongoRepository(mongo=mongo),
        agent_repository=object(),
    )

    assert await store.claim_user_message_for_processing("u1") is True
    assert user_messages.docs[0]["processing_claimed_at"] is not None
    assert await store.refresh_processing_claim("u1") is True
    assert await store.unclaim_user_message("u1") is True
    assert user_messages.docs[0]["processing_claimed_at"] is None
    assert await store.save_continuation_on_message("a1", {"next": "step"}) is True
    assert agent_messages.docs[0]["pending_continuation"] == {"next": "step"}
    assert await store.turn_exists("r1", "missing") is False

    assert await store.cancel_descendants("u1") == 3
    states = {
        doc["message_id"]: doc["message_content"]["message_task"]["status"]["state"]
        for doc in agent_messages.docs
    }
    assert states == {
        "a1": "canceled",
        "a2": "canceled",
        "done": "completed",
        "after-done": "canceled",
    }
    assert await store.cancel_agent_messages_by_ids(["done"]) == 0


@pytest.mark.asyncio
async def test_descendant_terminal_projection_rebuilds_winner_tagged_child_ids():
    from dal.runtime_store import RuntimeRepositoryStore

    agent_messages = FakeCollection(
        [
            {
                "message_id": "a1",
                "room_id": "r1",
                "related_message_id": "u1",
                "message_content": {"message_task": {"status": {"state": "working"}}},
            },
            {
                "message_id": "a2",
                "room_id": "r1",
                "related_message_id": "a1",
                "message_content": {"message_task": {"status": {"state": "submitted"}}},
            },
            {
                "message_id": "sys-u1",
                "room_id": "r1",
                "related_message_id": "u1",
                "message_content": {"message_task": {"status": {"state": "working"}}},
            },
        ]
    )
    mongo = FakeMongo(
        {
            "room_user_messages": FakeCollection([]),
            "room_agent_messages": agent_messages,
        }
    )
    store = RuntimeRepositoryStore(
        mongo=mongo,
        room_repository=object(),
        message_repository=MessageMongoRepository(mongo=mongo),
        agent_repository=object(),
    )

    first = await store.project_descendant_terminal_state(
        "u1",
        event_id="evt-1",
        target_state="failed",
        exclude_message_ids=["sys-u1"],
    )
    replay = await store.project_descendant_terminal_state(
        "u1",
        event_id="evt-1",
        target_state="failed",
        exclude_message_ids=["sys-u1"],
    )

    assert first == replay == ["a1", "a2"]
    assert {
        doc["message_id"]: doc["message_content"]["message_task"]["status"]["state"]
        for doc in agent_messages.docs
    } == {"a1": "failed", "a2": "failed", "sys-u1": "working"}
    assert {
        doc["terminal_projection_event_id"]
        for doc in agent_messages.docs
        if doc["message_id"] != "sys-u1"
    } == {"evt-1"}
    assert "terminal_projection_event_id" not in agent_messages.docs[2]


@pytest.mark.asyncio
async def test_message_repository_combines_history_sorted_with_before_filter():
    older = datetime(2026, 5, 10, tzinfo=UTC)
    newer = datetime(2026, 5, 11, tzinfo=UTC)
    older_us = int(older.timestamp() * 1_000_000)
    newer_us = int(newer.timestamp() * 1_000_000)
    repo, _, user_messages, agent_messages = _message_repo(
        user_docs=[
            {
                "message_id": "u2",
                "room_id": "r1",
                "message_created_at": newer,
                "timeline_sort_us": newer_us,
            },
            {
                "message_id": "u1",
                "room_id": "r1",
                "message_created_at": older,
                "timeline_sort_us": older_us,
            },
        ],
        agent_docs=[
            {
                "message_id": "a1",
                "room_id": "r1",
                "message_created_at": older,
                "timeline_sort_us": older_us,
                "step_number": 2,
            },
            {
                "message_id": "a0",
                "room_id": "r1",
                "message_created_at": older,
                "timeline_sort_us": older_us,
                "step_number": 1,
            },
        ],
    )

    history = await repo.get_for_room("r1", limit=3, before=newer)

    assert [doc["message_id"] for doc in history] == ["u1", "a0", "a1"]
    assert [doc["message_type"] for doc in history] == ["user", "agent", "agent"]
    expected_query = {"room_id": "r1", "timeline_sort_us": {"$lt": newer_us}}
    expected_options = {
        "sort": [("timeline_sort_us", -1), ("message_id", -1)],
        "limit": 3,
    }
    assert user_messages.find_calls[-1] == (expected_query, expected_options)
    assert agent_messages.find_calls[-1] == (expected_query, expected_options)


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [1, 2, 5, 37])
async def test_timeline_cursor_traversal_is_complete_stable_and_bounded(page_size):
    same_time = 1785700000000000
    user_docs = [
        {"room_id": "r1", "message_id": f"u-{index:03d}", "timeline_sort_us": same_time}
        for index in range(126)
    ]
    agent_docs = [
        {"room_id": "r1", "message_id": f"a-{index:03d}", "timeline_sort_us": same_time}
        for index in range(125)
    ]
    # A message id is only collection-local; both typed messages must survive.
    user_docs[0]["message_id"] = "same-id"
    agent_docs[0]["message_id"] = "same-id"
    repo, _, user_messages, agent_messages = _message_repo(user_docs, agent_docs)

    before = None
    pages = []
    while True:
        page = await repo.get_timeline_page("r1", limit=page_size, before=before)
        pages.insert(
            0, [(entry.source, entry.message["message_id"]) for entry in page.entries]
        )
        if not page.has_more:
            assert page.next_position is None
            break
        assert page.next_position is not None
        before = page.next_position

    flattened = [item for page in pages for item in page]
    expected = sorted(
        [("user", row["message_id"]) for row in user_docs]
        + [("agent", row["message_id"]) for row in agent_docs],
        key=lambda item: (same_time, 0 if item[0] == "user" else 1, item[1]),
    )
    assert flattened == expected
    assert len(flattened) == len(set(flattened)) == 251
    expected_options = {
        "sort": [("timeline_sort_us", -1), ("message_id", -1)],
        "limit": page_size + 1,
    }
    assert all(options == expected_options for _, options in user_messages.find_calls)
    assert all(options == expected_options for _, options in agent_messages.find_calls)
    assert "skip" not in repr(user_messages.find_calls + agent_messages.find_calls)


@pytest.mark.asyncio
async def test_timeline_first_page_returns_latest_over_one_hundred_single_source():
    docs = [
        {"room_id": "r1", "message_id": f"u-{index:03d}", "timeline_sort_us": index}
        for index in range(251)
    ]
    repo, _, _, _ = _message_repo(user_docs=docs)

    page = await repo.get_timeline_page("r1", limit=200, before=None)

    assert [entry.message["timeline_sort_us"] for entry in page.entries] == list(
        range(51, 251)
    )
    assert page.has_more is True
    assert page.next_position == TimelinePosition(
        timeline_sort_us=51, source="user", message_id="u-051"
    )


@pytest.mark.asyncio
async def test_timeline_continuation_ignores_later_concurrent_append():
    docs = [
        {"room_id": "r1", "message_id": f"u-{index}", "timeline_sort_us": index}
        for index in range(5)
    ]
    repo, _, users, _ = _message_repo(user_docs=docs)
    first = await repo.get_timeline_page("r1", limit=2, before=None)
    users.docs.extend(
        [
            {"room_id": "r1", "message_id": "new-later", "timeline_sort_us": 10},
            {"room_id": "r1", "message_id": "new-backdated", "timeline_sort_us": 1},
        ]
    )

    older_ids = set()
    before = first.next_position
    while before is not None:
        page = await repo.get_timeline_page("r1", limit=2, before=before)
        older_ids.update(entry.message["message_id"] for entry in page.entries)
        before = page.next_position

    first_ids = {entry.message["message_id"] for entry in first.entries}
    assert first_ids.isdisjoint(older_ids)
    assert "new-later" not in older_ids
    # No cross-request snapshot is promised: a backdated insert can join an older page.
    assert "new-backdated" in older_ids


@pytest.mark.asyncio
async def test_message_repository_get_thread_walks_descendants_and_stops_cycles():
    repo, _, _, _ = _message_repo(
        agent_docs=[
            {"message_id": "a1", "related_message_id": "u1"},
            {"message_id": "a2", "parent_message_id": "a1"},
            {
                "message_id": "cycle",
                "related_message_id": "a2",
                "parent_message_id": "cycle",
            },
            {"message_id": "other", "related_message_id": "not-u1"},
        ]
    )

    thread = await repo.get_thread("u1")

    assert [doc["message_id"] for doc in thread] == ["a1", "a2", "cycle"]


@pytest.mark.asyncio
async def test_message_repository_update_status_sets_task_state_and_extra_fields():
    repo, _, _, agent_messages = _message_repo(
        agent_docs=[
            {
                "message_id": "a1",
                "room_id": "room-1",
                "message_created_at": "2026-01-01T00:00:00Z",
                "timeline_sort_us": 1767225600000000,
                "message_content": {"message_task": {"status": {"state": "working"}}},
            }
        ]
    )

    assert (
        await repo.update_status(
            "a1",
            "completed",
            task_updated_at="now",
            room_id="other-room",
            message_id="rewritten",
            message_created_at="2030-01-01T00:00:00Z",
            timeline_sort_us=1,
        )
        is True
    )

    assert (
        agent_messages.docs[0]["message_content"]["message_task"]["status"]["state"]
        == "completed"
    )
    assert agent_messages.docs[0]["task_updated_at"] == "now"
    assert agent_messages.docs[0]["room_id"] == "room-1"
    assert agent_messages.docs[0]["message_id"] == "a1"
    assert agent_messages.docs[0]["message_created_at"] == "2026-01-01T00:00:00Z"
    assert agent_messages.docs[0]["timeline_sort_us"] == 1767225600000000
    assert agent_messages.update_one_calls == [
        (
            {"message_id": "a1"},
            {
                "$set": {
                    "message_content.message_task.status.state": "completed",
                    "task_updated_at": "now",
                }
            },
            {},
        )
    ]


@pytest.mark.asyncio
async def test_message_repository_delete_for_room_deletes_both_collections():
    repo, _, user_messages, agent_messages = _message_repo(
        user_docs=[
            {"message_id": "u1", "room_id": "r1"},
            {"message_id": "u2", "room_id": "r1"},
            {"message_id": "u3", "room_id": "other"},
        ],
        agent_docs=[
            {"message_id": "a1", "room_id": "r1"},
            {"message_id": "a2", "room_id": "r1"},
            {"message_id": "a3", "room_id": "r1"},
        ],
    )

    assert await repo.delete_for_room("r1") == {
        "user_messages": 2,
        "agent_messages": 3,
    }
    assert user_messages.delete_many_calls == [{"room_id": "r1"}]
    assert agent_messages.delete_many_calls == [{"room_id": "r1"}]


def _matches(doc: dict, query: dict) -> bool:  # noqa: C901
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(doc, item) for item in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, item) for item in expected):
                return False
            continue

        actual = _get_dotted(doc, key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$nin" in expected and actual in expected["$nin"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$lt" in expected and not (
                actual is not None and actual < expected["$lt"]
            ):
                return False
            if "$lte" in expected and not (
                actual is not None and actual <= expected["$lte"]
            ):
                return False
        elif actual != expected:
            return False
    return True


def _apply_update(doc: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.get("$set", {}).items():
        _set_dotted(doc, key, deepcopy(value))
    for key in update.get("$unset", {}):
        _unset_dotted(doc, key)


def _get_dotted(doc: dict[str, Any], key: str) -> Any:
    current: Any = doc
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_dotted(doc: dict[str, Any], key: str, value: Any) -> None:
    current = doc
    parts = key.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _unset_dotted(doc: dict[str, Any], key: str) -> None:
    current = doc
    parts = key.split(".")
    for part in parts[:-1]:
        current = current.get(part)
        if not isinstance(current, dict):
            return
    current.pop(parts[-1], None)
