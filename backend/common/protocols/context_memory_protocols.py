from typing import Protocol, runtime_checkable


@runtime_checkable
class RoomMemoryCleanupPort(Protocol):
    async def delete_room_memory(self, room_id: str) -> bool: ...


__all__ = [
    "RoomMemoryCleanupPort",
]
