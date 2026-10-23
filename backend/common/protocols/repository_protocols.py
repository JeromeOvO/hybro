from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class RoomRepository(Protocol):
    async def get_by_id(self, room_id: str) -> dict | None: ...
    async def get_by_owner(self, owner_id: str) -> list[dict]: ...
    async def get_history_by_owner(
        self, owner_id: str, *, limit: int = 100
    ) -> list[dict]: ...
    async def create(self, room: dict) -> str: ...
    async def update(self, room_id: str, updates: dict) -> bool: ...
    async def update_fields(self, room_id: str, updates: dict) -> dict | None: ...
    async def touch_activity(self, room_id: str, activity_at: datetime) -> bool: ...
    async def set_membership(
        self,
        room_id: str,
        *,
        agent_set: dict[str, str],
        membership_origin: str,
        membership_origin_status: str,
        source_group_id: str | None = None,
        source_group_name: str | None = None,
    ) -> dict | None: ...
    async def delete(self, room_id: str) -> bool: ...


__all__ = [
    "RoomRepository",
]
