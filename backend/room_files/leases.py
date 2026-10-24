from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from common.observability import traced_create_task
from common.utils.time import utcnow
from room_files.errors import FileConflictError
from room_files.ports import RoomLifecycleStore


class RoomWriteLeases:
    """Durable write fencing stored by Conversation's room lifecycle store."""

    def __init__(
        self,
        store: RoomLifecycleStore,
        *,
        now: Callable[[], datetime] = utcnow,
        ttl_seconds: int = 60,
    ) -> None:
        self._store = store
        self._now = now
        self._ttl = timedelta(seconds=max(1, ttl_seconds))

    async def acquire(self, conversation_id: str, owner: str) -> str:
        now = self._now()
        lease_id = uuid4().hex
        acquired = await self._store.acquire_write_lease(
            conversation_id,
            lease_id=lease_id,
            owner=owner,
            now=now,
            expires_at=now + self._ttl,
        )
        if not acquired:
            raise FileConflictError("room is deleting or unavailable")
        return lease_id

    async def renew(self, conversation_id: str, lease_id: str) -> bool:
        return await self._store.renew_write_lease(
            conversation_id, lease_id, expires_at=self._now() + self._ttl
        )

    async def release(self, conversation_id: str, lease_id: str) -> None:
        await self._store.release_write_lease(conversation_id, lease_id)

    async def assert_valid(self, conversation_id: str, lease_id: str) -> None:
        if not await self._store.has_valid_write_lease(
            conversation_id, lease_id, now=self._now()
        ):
            raise FileConflictError("room write lease was lost")

    @asynccontextmanager
    async def hold(self, conversation_id: str, owner: str) -> AsyncIterator[str]:
        lease_id = await self.acquire(conversation_id, owner)
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
                        renewed = await self.renew(conversation_id, lease_id)
                    except Exception:
                        renewed = False
                    if not renewed:
                        stopped.set()
                        if owner_task is not None:
                            owner_task.cancel()
                        return

        maintainer = traced_create_task(
            maintain(),
            name=f"room-file-lease-{conversation_id}",
        )
        try:
            yield lease_id
            if stopped.is_set():
                raise FileConflictError("room write lease was lost")
            await self.assert_valid(conversation_id, lease_id)
        except asyncio.CancelledError:
            if stopped.is_set():
                raise FileConflictError("room write lease was lost") from None
            raise
        finally:
            stopped.set()
            maintainer.cancel()
            await asyncio.gather(maintainer, return_exceptions=True)
            await self.release(conversation_id, lease_id)

    async def begin_deletion(self, conversation_id: str, owner_id: str) -> str | None:
        return await self._store.begin_deletion(
            conversation_id,
            owner_id,
            deletion_id=uuid4().hex,
            started_at=self._now(),
        )

    async def wait_until_drained(
        self, conversation_id: str, *, timeout_seconds: float = 65
    ) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            if not await self._store.has_live_write_leases(
                conversation_id, now=self._now()
            ):
                return True
            await asyncio.sleep(0.05)
        return False
