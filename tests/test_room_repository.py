from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest

from room.repository import MessageMongoRepository, RoomMongoRepository


class FakeMongo:
    def __init__(self, collections: dict[str, "FakeCollection"] | None = None) -> None:
        self.collections = collections or {}
        self.collection_calls: list[str] = []

    def collection(self, name: str) -> "FakeCollection":
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
        limit = kwargs.get("limit")
        return matches[:limit] if limit else matches

    async def insert_one(self, document: dict) -> str:
        self.insert_one_calls.append(deepcopy(document))
        self.docs.append(deepcopy(document))
        return str(document.get("_id") or f"inserted-{len(self.docs)}")

    async def update_one(self, query: dict, update: dict, **kwargs) -> bool:
        self.update_one_calls.append((deepcopy(query), deepcopy(update), deepcopy(kwargs)))
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update)
                return True
        return False

    async def find_one_and_update(self, query: dict, update: dict, **kwargs) -> dict | None:
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
    assert rooms.find_calls == [({"room_owner_id": "u1"}, {})]


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
        {"room_id": "r1"},
        {"$set": {"room_name": "Changed"}},
        {},
    )
    assert rooms.find_one_and_update_calls[-1][0] == {"room_id": "r1"}
    assert rooms.delete_one_calls == [{"room_id": "r2"}]
    assert all(isinstance(doc, dict) for doc in rooms.docs)


@pytest.mark.asyncio
async def test_message_repository_uses_room_message_collections_and_saves_raw_dicts():
    repo, mongo, user_messages, agent_messages = _message_repo()

    user_id = await repo.save_user_message({"message_id": "u1", "room_id": "r1"})
    agent_id = await repo.save_agent_message({"message_id": "a1", "room_id": "r1"})

    assert mongo.collection_calls == ["room_user_messages", "room_agent_messages"]
    assert user_id == "u1"
    assert agent_id == "a1"
    assert user_messages.insert_one_calls == [{"message_id": "u1", "room_id": "r1"}]
    assert agent_messages.insert_one_calls == [{"message_id": "a1", "room_id": "r1"}]


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
async def test_message_repository_combines_history_sorted_with_before_filter():
    older = datetime(2026, 5, 10, tzinfo=timezone.utc)
    newer = datetime(2026, 5, 11, tzinfo=timezone.utc)
    repo, _, user_messages, agent_messages = _message_repo(
        user_docs=[
            {"message_id": "u2", "room_id": "r1", "message_created_at": newer},
            {"message_id": "u1", "room_id": "r1", "message_created_at": older},
        ],
        agent_docs=[
            {
                "message_id": "a1",
                "room_id": "r1",
                "message_created_at": older,
                "step_number": 2,
            },
            {
                "message_id": "a0",
                "room_id": "r1",
                "message_created_at": older,
                "step_number": 1,
            },
        ],
    )

    history = await repo.get_for_room("r1", limit=3, before=newer)

    assert [doc["message_id"] for doc in history] == ["u1", "a0", "a1"]
    assert [doc["message_type"] for doc in history] == ["user", "agent", "agent"]
    assert user_messages.find_calls[-1] == (
        {"room_id": "r1", "message_created_at": {"$lt": newer}},
        {},
    )
    assert agent_messages.find_calls[-1] == (
        {"room_id": "r1", "message_created_at": {"$lt": newer}},
        {},
    )


@pytest.mark.asyncio
async def test_message_repository_get_thread_walks_descendants_and_stops_cycles():
    repo, _, _, _ = _message_repo(
        agent_docs=[
            {"message_id": "a1", "related_message_id": "u1"},
            {"message_id": "a2", "parent_message_id": "a1"},
            {"message_id": "cycle", "related_message_id": "a2", "parent_message_id": "cycle"},
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
                "message_content": {"message_task": {"status": {"state": "working"}}},
            }
        ]
    )

    assert await repo.update_status("a1", "completed", task_updated_at="now") is True

    assert agent_messages.docs[0]["message_content"]["message_task"]["status"][
        "state"
    ] == "completed"
    assert agent_messages.docs[0]["task_updated_at"] == "now"
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


def _matches(doc: dict, query: dict) -> bool:
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
            if "$lt" in expected and not (actual is not None and actual < expected["$lt"]):
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
