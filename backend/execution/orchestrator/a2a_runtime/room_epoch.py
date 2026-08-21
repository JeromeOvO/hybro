"""Room incarnation fence helpers; production lifecycle binding is deferred."""

from __future__ import annotations

from .in_memory import InMemoryRoomEpochStore
from .models import RoomEpoch


class RoomEpochGone(PermissionError):
    pass


async def require_active(store, room_id: str, room_epoch: int) -> None:
    if not await store.verify_active(room_id, room_epoch):
        raise RoomEpochGone(f"Room {room_id!r} epoch {room_epoch} is inactive")


async def require_cleanup(
    store, room_id: str, room_epoch: int, deletion_id: str
) -> None:
    if not await store.verify_cleanup_epoch(room_id, room_epoch, deletion_id):
        raise RoomEpochGone("deletion cleanup identity does not match tombstoned epoch")


__all__ = [
    "InMemoryRoomEpochStore",
    "RoomEpoch",
    "RoomEpochGone",
    "require_active",
    "require_cleanup",
]
