from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any


class RoomLockManager:
    def __init__(
        self,
        *,
        distributed_lock_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._local_locks: dict[str, asyncio.Lock] = {}
        self._distributed_lock_factory = distributed_lock_factory

    def local_lock(self, room_id: str) -> asyncio.Lock:
        return self._local_locks.setdefault(room_id, asyncio.Lock())

    @asynccontextmanager
    async def lock_room(self, room_id: str):
        async with self.local_lock(room_id):
            distributed_lock = (
                self._distributed_lock_factory(room_id)
                if self._distributed_lock_factory is not None
                else None
            )
            if distributed_lock is None:
                yield
                return
            async with distributed_lock:
                yield


__all__ = ["RoomLockManager"]
