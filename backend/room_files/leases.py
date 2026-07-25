from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from common.utils.time import utcnow
from room_files.errors import FileConflictError


class RoomWriteLeases:
    """Durable write fencing embedded in room documents."""

    def __init__(
        self,
        rooms: Any,
        *,
        now: Callable[[], datetime] = utcnow,
        ttl_seconds: int = 60,
    ) -> None:
        self._rooms = rooms
        self._now = now
        self._ttl = timedelta(seconds=max(1, ttl_seconds))

    async def acquire(self, room_id: str, owner: str) -> str:
        now = self._now()
        await self._rooms.update_one(
            {"room_id": room_id},
            {"$pull": {"write_leases": {"expires_at": {"$lte": now}}}},
        )
        lease_id = uuid4().hex
        result = await self._rooms.update_one(
            {
                "room_id": room_id,
                "$or": [
                    {"lifecycle_state": "active"},
                    {"lifecycle_state": {"$exists": False}},
                ],
            },
            {
                "$set": {"lifecycle_state": "active"},
                "$push": {
                    "write_leases": {
                        "lease_id": lease_id,
                        "owner": owner,
                        "acquired_at": now,
                        "expires_at": now + self._ttl,
                    }
                },
            },
        )
        if not _changed(result):
            raise FileConflictError("room is deleting or unavailable")
        return lease_id

    async def renew(self, room_id: str, lease_id: str) -> bool:
        result = await self._rooms.update_one(
            {
                "room_id": room_id,
                "lifecycle_state": "active",
                "write_leases.lease_id": lease_id,
            },
            {"$set": {"write_leases.$[lease].expires_at": self._now() + self._ttl}},
            array_filters=[{"lease.lease_id": lease_id}],
        )
        return _changed(result)

    async def release(self, room_id: str, lease_id: str) -> None:
        await self._rooms.update_one(
            {"room_id": room_id},
            {"$pull": {"write_leases": {"lease_id": lease_id}}},
        )

    async def assert_valid(self, room_id: str, lease_id: str) -> None:
        room = await self._rooms.find_one(
            {
                "room_id": room_id,
                "lifecycle_state": "active",
                "write_leases": {
                    "$elemMatch": {
                        "lease_id": lease_id,
                        "expires_at": {"$gt": self._now()},
                    }
                },
            },
            {"_id": 1},
        )
        if room is None:
            raise FileConflictError("room write lease was lost")

    @asynccontextmanager
    async def hold(self, room_id: str, owner: str) -> AsyncIterator[str]:
        lease_id = await self.acquire(room_id, owner)
        stopped = asyncio.Event()
        owner_task = asyncio.current_task()

        async def maintain() -> None:
            interval = max(0.1, self._ttl.total_seconds() / 3)
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=interval)
                    return
                except TimeoutError:
                    try:
                        renewed = await self.renew(room_id, lease_id)
                    except Exception:
                        renewed = False
                    if not renewed:
                        stopped.set()
                        if owner_task is not None:
                            owner_task.cancel()
                        return

        maintainer = asyncio.create_task(maintain())
        try:
            yield lease_id
            if stopped.is_set():
                raise FileConflictError("room write lease was lost")
            await self.assert_valid(room_id, lease_id)
        except asyncio.CancelledError:
            if stopped.is_set():
                raise FileConflictError("room write lease was lost") from None
            raise
        finally:
            stopped.set()
            maintainer.cancel()
            await asyncio.gather(maintainer, return_exceptions=True)
            await self.release(room_id, lease_id)

    async def begin_deletion(self, room_id: str, owner_id: str) -> str | None:
        deletion_id = uuid4().hex
        result = await self._rooms.update_one(
            {
                "room_id": room_id,
                "room_owner_id": owner_id,
                "$or": [
                    {"lifecycle_state": "active"},
                    {"lifecycle_state": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "lifecycle_state": "deleting",
                    "deletion_id": deletion_id,
                    "deletion_started_at": self._now(),
                    "deletion_phase": "fencing",
                }
            },
        )
        if _changed(result):
            return deletion_id
        room = await self._rooms.find_one(
            {
                "room_id": room_id,
                "room_owner_id": owner_id,
                "lifecycle_state": "deleting",
            }
        )
        return str(room["deletion_id"]) if room and room.get("deletion_id") else None

    async def wait_until_drained(
        self, room_id: str, *, timeout_seconds: float = 65
    ) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            now = self._now()
            await self._rooms.update_one(
                {"room_id": room_id},
                {"$pull": {"write_leases": {"expires_at": {"$lte": now}}}},
            )
            room = await self._rooms.find_one({"room_id": room_id}, {"write_leases": 1})
            if room is None or not room.get("write_leases"):
                return True
            await asyncio.sleep(0.05)
        return False


def _changed(result: Any) -> bool:
    if isinstance(result, bool):
        return result
    return bool(getattr(result, "modified_count", 0))
