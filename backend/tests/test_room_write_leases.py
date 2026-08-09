from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from room_files import FileConflictError, RoomWriteLeases


class Rooms:
    def __init__(self, doc):
        self.doc = deepcopy(doc)

    async def update_one(self, query, update, **kwargs):
        del kwargs
        if query.get("room_id") != self.doc.get("room_id"):
            return False
        if query.get("room_owner_id") not in (None, self.doc.get("room_owner_id")):
            return False
        lifecycle = self.doc.get("lifecycle_state")
        if "lifecycle_state" in query and query["lifecycle_state"] != lifecycle:
            return False
        if "$or" in query and lifecycle not in (None, "active"):
            return False
        lease_id = query.get("write_leases.lease_id")
        if lease_id and not any(
            lease["lease_id"] == lease_id for lease in self.doc.get("write_leases", [])
        ):
            return False
        pull = update.get("$pull", {}).get("write_leases")
        if pull:
            if "lease_id" in pull:
                self.doc["write_leases"] = [
                    lease
                    for lease in self.doc.get("write_leases", [])
                    if lease["lease_id"] != pull["lease_id"]
                ]
            elif "expires_at" in pull:
                cutoff = pull["expires_at"]["$lte"]
                self.doc["write_leases"] = [
                    lease
                    for lease in self.doc.get("write_leases", [])
                    if lease["expires_at"] > cutoff
                ]
        self.doc.update(update.get("$set", {}))
        pushed = update.get("$push", {}).get("write_leases")
        if pushed:
            self.doc.setdefault("write_leases", []).append(deepcopy(pushed))
        return True

    async def find_one_and_update(self, query, update, **kwargs):
        projection = kwargs.get("projection")
        changed = await self.update_one(query, update)
        if not changed:
            return None
        if projection:
            return {
                key: deepcopy(self.doc.get(key))
                for key in projection
                if key in self.doc
            }
        return deepcopy(self.doc)

    async def find_one(self, query, *, projection=None):
        del projection
        if query.get("room_id") != self.doc.get("room_id"):
            return None
        if query.get("lifecycle_state") not in (
            None,
            self.doc.get("lifecycle_state"),
        ):
            return None
        expected_deletion = query.get("deletion_id")
        if expected_deletion and expected_deletion != self.doc.get("deletion_id"):
            return None
        elem = query.get("write_leases", {}).get("$elemMatch")
        if elem and not any(
            lease["lease_id"] == elem["lease_id"]
            and lease["expires_at"] > elem["expires_at"]["$gt"]
            for lease in self.doc.get("write_leases", [])
        ):
            return None
        return deepcopy(self.doc)


async def test_active_room_lease_is_durable_and_released():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    rooms = Rooms(
        {
            "room_id": "room-1",
            "room_owner_id": "user-1",
            "lifecycle_state": "active",
            "write_leases": [],
        }
    )
    leases = RoomWriteLeases(rooms, now=lambda: now)

    async with leases.hold("room-1", "upload") as lease_id:
        await leases.assert_valid("room-1", lease_id)
        assert rooms.doc["write_leases"][0]["owner"] == "upload"

    assert rooms.doc["write_leases"] == []


async def test_deleting_room_rejects_new_lease_and_reuses_deletion_id():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    rooms = Rooms(
        {
            "room_id": "room-1",
            "room_owner_id": "user-1",
            "lifecycle_state": "active",
            "write_leases": [],
        }
    )
    leases = RoomWriteLeases(rooms, now=lambda: now)

    deletion_id = await leases.begin_deletion("room-1", "user-1")
    assert deletion_id
    assert await leases.begin_deletion("room-1", "user-1") == deletion_id
    with pytest.raises(FileConflictError):
        await leases.acquire("room-1", "late-writer")


async def test_wait_until_drained_reclaims_expired_lease():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    rooms = Rooms(
        {
            "room_id": "room-1",
            "room_owner_id": "user-1",
            "lifecycle_state": "deleting",
            "write_leases": [
                {
                    "lease_id": "stale",
                    "owner": "worker",
                    "acquired_at": now - timedelta(minutes=2),
                    "expires_at": now - timedelta(minutes=1),
                }
            ],
        }
    )
    leases = RoomWriteLeases(rooms, now=lambda: now)

    assert await leases.wait_until_drained("room-1", timeout_seconds=0.1)
