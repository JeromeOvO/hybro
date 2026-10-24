"""RoomWriteLeases drives write fencing through Conversation's lifecycle store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from conversation.repository import RoomMongoRepository
from room_files import FileConflictError, RoomWriteLeases
from tests.test_room_lifecycle_store import FakeMongo, LifecycleRooms


def _leases(doc, now):
    rooms = LifecycleRooms(doc)
    return RoomWriteLeases(
        RoomMongoRepository(FakeMongo(rooms)), now=lambda: now
    ), rooms


async def test_active_room_lease_is_durable_and_released():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    leases, rooms = _leases(
        {
            "room_id": "room-1",
            "room_owner_id": "user-1",
            "lifecycle_state": "active",
            "write_leases": [],
        },
        now,
    )

    async with leases.hold("room-1", "upload") as lease_id:
        await leases.assert_valid("room-1", lease_id)
        assert rooms.docs[0]["write_leases"][0]["owner"] == "upload"
        assert rooms.docs[0]["write_leases"][0]["expires_at"] == now + timedelta(
            seconds=60
        )

    assert rooms.docs[0]["write_leases"] == []


async def test_deleting_room_rejects_new_lease_and_reuses_deletion_id():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    leases, rooms = _leases(
        {
            "room_id": "room-1",
            "room_owner_id": "user-1",
            "lifecycle_state": "active",
            "write_leases": [],
        },
        now,
    )

    deletion_id = await leases.begin_deletion("room-1", "user-1")
    assert deletion_id
    assert rooms.docs[0]["deletion_started_at"] == now
    assert await leases.begin_deletion("room-1", "user-1") == deletion_id
    with pytest.raises(FileConflictError):
        await leases.acquire("room-1", "late-writer")


async def test_lost_lease_fails_validation():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    leases, rooms = _leases(
        {"room_id": "room-1", "lifecycle_state": "active", "write_leases": []}, now
    )

    lease_id = await leases.acquire("room-1", "upload")
    await leases.release("room-1", lease_id)

    assert not await leases.renew("room-1", lease_id)
    with pytest.raises(FileConflictError, match="lease was lost"):
        await leases.assert_valid("room-1", lease_id)


async def test_wait_until_drained_reclaims_expired_lease():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    leases, rooms = _leases(
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
        },
        now,
    )

    assert await leases.wait_until_drained("room-1", timeout_seconds=0.1)
    assert rooms.docs[0]["write_leases"] == []


async def test_wait_until_drained_times_out_on_live_lease():
    now = datetime(2026, 7, 23, tzinfo=UTC)
    leases, _ = _leases(
        {
            "room_id": "room-1",
            "lifecycle_state": "deleting",
            "write_leases": [
                {
                    "lease_id": "live",
                    "owner": "worker",
                    "acquired_at": now,
                    "expires_at": now + timedelta(minutes=1),
                }
            ],
        },
        now,
    )

    assert not await leases.wait_until_drained("room-1", timeout_seconds=0.1)
