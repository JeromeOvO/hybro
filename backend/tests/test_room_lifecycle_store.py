"""Conversation's ``rooms`` repository implements room_files' lifecycle port."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from conversation.repository import RoomMongoRepository
from room_files import RoomLifecycleStore

NOW = datetime(2026, 7, 23, tzinfo=UTC)
TTL = timedelta(seconds=60)
ACTIVE = [{"lifecycle_state": "active"}, {"lifecycle_state": {"$exists": False}}]


class LifecycleRooms:
    """In-memory ``rooms`` collection with the operators lease writes use."""

    def __init__(self, *docs: dict[str, Any]) -> None:
        self.docs = [deepcopy(doc) for doc in docs]
        self.calls: list[tuple[str, tuple, dict]] = []

    async def find_one(self, query, **kwargs):
        self.calls.append(("find_one", (query,), kwargs))
        for doc in self.docs:
            if _matches(doc, query):
                return deepcopy(doc)
        return None

    async def find(self, query, **kwargs):
        self.calls.append(("find", (query,), kwargs))
        return [deepcopy(doc) for doc in self.docs if _matches(doc, query)]

    async def update_one(self, query, update, **kwargs):
        self.calls.append(("update_one", (query, update), kwargs))
        return self._update(query, update, kwargs.get("array_filters")) is not None

    async def find_one_and_update(self, query, update, **kwargs):
        self.calls.append(("find_one_and_update", (query, update), kwargs))
        doc = self._update(query, update, None)
        if doc is None:
            return None
        projection = kwargs.get("projection")
        if projection:
            return {key: deepcopy(doc[key]) for key in projection if key in doc}
        return deepcopy(doc)

    async def delete_one(self, query):
        self.calls.append(("delete_one", (query,), {}))
        for index, doc in enumerate(self.docs):
            if _matches(doc, query):
                del self.docs[index]
                return True
        return False

    def _update(self, query, update, array_filters):
        for doc in self.docs:
            if _matches(doc, query):
                _apply(doc, update, array_filters)
                return doc
        return None


class FakeMongo:
    def __init__(self, rooms: LifecycleRooms) -> None:
        self.rooms = rooms

    def collection(self, name: str) -> LifecycleRooms:
        assert name == "rooms"
        return self.rooms


def _matches(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(doc, item) for item in expected):
                return False
        elif "." in key:
            field, sub = key.split(".", 1)
            if not any(item.get(sub) == expected for item in doc.get(field, [])):
                return False
        elif not _value_matches(doc.get(key), expected, key in doc):
            return False
    return True


def _value_matches(actual: Any, expected: Any, present: bool) -> bool:
    if not isinstance(expected, dict):
        return actual == expected
    checks = {
        "$exists": lambda value: present == value,
        "$lt": lambda value: actual is not None and actual < value,
        "$lte": lambda value: actual is not None and actual <= value,
        "$gt": lambda value: actual is not None and actual > value,
        "$elemMatch": lambda value: any(_matches(item, value) for item in actual or []),
    }
    return all(checks[op](value) for op, value in expected.items())


def _apply(doc: dict, update: dict, array_filters: list | None) -> None:
    for key, value in update.get("$pull", {}).items():
        if key in doc:
            doc[key] = [item for item in doc[key] if not _matches(item, value)]
    for key, value in update.get("$set", {}).items():
        if "$[lease]" in key:
            wanted = (array_filters or [{}])[0]["lease.lease_id"]
            field = key.rsplit(".", 1)[1]
            for lease in doc.get("write_leases", []):
                if lease["lease_id"] == wanted:
                    lease[field] = value
        else:
            doc[key] = value
    for key, value in update.get("$push", {}).items():
        doc.setdefault(key, []).append(deepcopy(value))


def _lease(lease_id: str, expires_at: datetime) -> dict:
    return {
        "lease_id": lease_id,
        "owner": "worker",
        "acquired_at": expires_at - TTL,
        "expires_at": expires_at,
    }


def _store(*docs: dict) -> tuple[RoomMongoRepository, LifecycleRooms]:
    rooms = LifecycleRooms(*docs)
    return RoomMongoRepository(FakeMongo(rooms)), rooms


def test_room_repository_implements_the_room_files_lifecycle_port():
    store, _ = _store()

    assert isinstance(store, RoomLifecycleStore)


@pytest.mark.asyncio
async def test_acquire_prunes_expired_leases_then_pushes_atomically():
    store, rooms = _store(
        {
            "room_id": "r1",
            "write_leases": [_lease("stale", NOW), _lease("live", NOW + TTL)],
        }
    )

    assert await store.acquire_write_lease(
        "r1", lease_id="new", owner="upload", now=NOW, expires_at=NOW + TTL
    )

    assert rooms.calls[0] == (
        "update_one",
        (
            {"room_id": "r1"},
            {"$pull": {"write_leases": {"expires_at": {"$lte": NOW}}}},
        ),
        {},
    )
    assert rooms.calls[1] == (
        "find_one_and_update",
        (
            {"room_id": "r1", "$or": ACTIVE},
            {
                "$set": {"lifecycle_state": "active"},
                "$push": {
                    "write_leases": {
                        "lease_id": "new",
                        "owner": "upload",
                        "acquired_at": NOW,
                        "expires_at": NOW + TTL,
                    }
                },
            },
        ),
        {"return_document": True, "projection": {"write_leases": 1}},
    )
    assert rooms.docs[0]["lifecycle_state"] == "active"
    assert [lease["lease_id"] for lease in rooms.docs[0]["write_leases"]] == [
        "live",
        "new",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "room",
    [
        {"room_id": "r1", "lifecycle_state": "deleting"},
        {"room_id": "other", "lifecycle_state": "active"},
    ],
)
async def test_acquire_refuses_deleting_or_missing_room(room):
    store, rooms = _store(room)

    assert not await store.acquire_write_lease(
        "r1", lease_id="new", owner="upload", now=NOW, expires_at=NOW + TTL
    )
    assert "write_leases" not in rooms.docs[0]


@pytest.mark.asyncio
async def test_renew_extends_only_the_named_lease_of_an_active_room():
    store, rooms = _store(
        {
            "room_id": "r1",
            "lifecycle_state": "active",
            "write_leases": [_lease("a", NOW), _lease("b", NOW)],
        }
    )
    later = NOW + TTL

    assert await store.renew_write_lease("r1", "a", expires_at=later)
    assert not await store.renew_write_lease("r1", "missing", expires_at=later)

    assert rooms.calls[0] == (
        "update_one",
        (
            {
                "room_id": "r1",
                "lifecycle_state": "active",
                "write_leases.lease_id": "a",
            },
            {"$set": {"write_leases.$[lease].expires_at": later}},
        ),
        {"array_filters": [{"lease.lease_id": "a"}]},
    )
    assert [lease["expires_at"] for lease in rooms.docs[0]["write_leases"]] == [
        later,
        NOW,
    ]


@pytest.mark.asyncio
async def test_renew_fails_once_the_room_is_deleting():
    store, _ = _store(
        {
            "room_id": "r1",
            "lifecycle_state": "deleting",
            "write_leases": [_lease("a", NOW)],
        }
    )

    assert not await store.renew_write_lease("r1", "a", expires_at=NOW + TTL)


@pytest.mark.asyncio
async def test_release_pulls_only_the_named_lease():
    store, rooms = _store(
        {"room_id": "r1", "write_leases": [_lease("a", NOW), _lease("b", NOW)]}
    )

    await store.release_write_lease("r1", "a")

    assert rooms.calls == [
        (
            "update_one",
            ({"room_id": "r1"}, {"$pull": {"write_leases": {"lease_id": "a"}}}),
            {},
        )
    ]
    assert [lease["lease_id"] for lease in rooms.docs[0]["write_leases"]] == ["b"]


@pytest.mark.asyncio
async def test_valid_lease_requires_active_room_and_unexpired_lease():
    store, rooms = _store(
        {
            "room_id": "r1",
            "lifecycle_state": "active",
            "write_leases": [_lease("live", NOW + TTL), _lease("expired", NOW)],
        }
    )

    assert await store.has_valid_write_lease("r1", "live", now=NOW)
    assert not await store.has_valid_write_lease("r1", "expired", now=NOW)
    assert rooms.calls[0] == (
        "find_one",
        (
            {
                "room_id": "r1",
                "lifecycle_state": "active",
                "write_leases": {
                    "$elemMatch": {"lease_id": "live", "expires_at": {"$gt": NOW}}
                },
            },
        ),
        {"projection": {"_id": 1}},
    )

    rooms.docs[0]["lifecycle_state"] = "deleting"
    assert not await store.has_valid_write_lease("r1", "live", now=NOW)


@pytest.mark.asyncio
async def test_live_lease_check_prunes_expired_leases_first():
    store, rooms = _store(
        {"room_id": "r1", "write_leases": [_lease("stale", NOW - TTL)]}
    )

    assert not await store.has_live_write_leases("r1", now=NOW)
    assert rooms.docs[0]["write_leases"] == []
    assert [call[0] for call in rooms.calls] == ["update_one", "find_one"]
    assert rooms.calls[1][2] == {"projection": {"write_leases": 1}}

    rooms.docs[0]["write_leases"] = [_lease("live", NOW + TTL)]
    assert await store.has_live_write_leases("r1", now=NOW)
    assert not await store.has_live_write_leases("missing", now=NOW)


@pytest.mark.asyncio
async def test_begin_deletion_fences_owner_room_and_reuses_deletion_id():
    store, rooms = _store({"room_id": "r1", "room_owner_id": "u1"})

    assert (
        await store.begin_deletion("r1", "u1", deletion_id="d1", started_at=NOW) == "d1"
    )
    assert rooms.calls[0] == (
        "update_one",
        (
            {"room_id": "r1", "room_owner_id": "u1", "$or": ACTIVE},
            {
                "$set": {
                    "lifecycle_state": "deleting",
                    "deletion_id": "d1",
                    "deletion_started_at": NOW,
                    "deletion_phase": "fencing",
                }
            },
        ),
        {},
    )
    assert (
        await store.begin_deletion("r1", "u1", deletion_id="d2", started_at=NOW) == "d1"
    )
    assert rooms.calls[-1] == (
        "find_one",
        ({"room_id": "r1", "room_owner_id": "u1", "lifecycle_state": "deleting"},),
        {},
    )
    assert (
        await store.begin_deletion("r1", "intruder", deletion_id="d3", started_at=NOW)
        is None
    )
    assert rooms.docs[0]["deletion_id"] == "d1"


@pytest.mark.asyncio
async def test_deletion_phase_is_set_only_for_the_current_deletion():
    store, rooms = _store(
        {"room_id": "r1", "lifecycle_state": "deleting", "deletion_id": "d1"}
    )

    assert await store.set_deletion_phase("r1", "d1", "files")
    assert not await store.set_deletion_phase("r1", "other", "state")

    assert rooms.calls[0] == (
        "update_one",
        (
            {"room_id": "r1", "lifecycle_state": "deleting", "deletion_id": "d1"},
            {"$set": {"deletion_phase": "files"}},
        ),
        {},
    )
    assert rooms.docs[0]["deletion_phase"] == "files"


@pytest.mark.asyncio
async def test_stale_deletions_are_listed_by_start_time():
    cutoff = NOW - timedelta(minutes=5)
    store, rooms = _store(
        {
            "room_id": "old",
            "lifecycle_state": "deleting",
            "deletion_id": "d1",
            "deletion_started_at": cutoff - timedelta(seconds=1),
        },
        {
            "room_id": "unfenced",
            "lifecycle_state": "deleting",
            "deletion_started_at": cutoff - timedelta(seconds=1),
        },
        {
            "room_id": "recent",
            "lifecycle_state": "deleting",
            "deletion_id": "d2",
            "deletion_started_at": cutoff,
        },
        {"room_id": "active", "lifecycle_state": "active"},
    )

    assert await store.list_stale_deletions(started_before=cutoff) == [
        ("old", "d1"),
        ("unfenced", ""),
    ]
    assert rooms.calls == [
        (
            "find",
            (
                {
                    "lifecycle_state": "deleting",
                    "deletion_started_at": {"$lt": cutoff},
                },
            ),
            {},
        )
    ]


@pytest.mark.asyncio
async def test_room_is_deleted_only_while_in_the_given_deletion():
    store, rooms = _store(
        {"room_id": "r1", "lifecycle_state": "deleting", "deletion_id": "d1"}
    )

    assert not await store.delete_room_in_deletion("r1", "other")
    assert await store.delete_room_in_deletion("r1", "d1")

    assert rooms.calls[-1] == (
        "delete_one",
        ({"room_id": "r1", "lifecycle_state": "deleting", "deletion_id": "d1"},),
        {},
    )
    assert rooms.docs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("room", "owner"),
    [
        ({"room_id": "r1", "room_owner_id": "u1"}, "u1"),
        ({"room_id": "r1", "owner_id": "u2", "lifecycle_state": "active"}, "u2"),
        ({"room_id": "r1", "room_owner_id": "u1", "lifecycle_state": "deleting"}, None),
        ({"room_id": "r1"}, None),
        ({"room_id": "other", "room_owner_id": "u1"}, None),
    ],
)
async def test_active_room_owner(room, owner):
    store, rooms = _store(room)

    assert await store.get_active_room_owner("r1") == owner
    assert rooms.calls == [("find_one", ({"room_id": "r1"},), {})]
