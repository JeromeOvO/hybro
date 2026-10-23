from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from common.dto import (
    RoomMessageInfo,
)


@runtime_checkable
class RoomHistoryReader(Protocol):
    async def get_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[RoomMessageInfo]: ...

    async def get_messages_by_ids(
        self, message_ids: list[str]
    ) -> list[RoomMessageInfo]: ...
    async def get_message_thread(
        self, parent_message_id: str
    ) -> list[RoomMessageInfo]: ...


@runtime_checkable
class RoomOwnershipReader(Protocol):
    async def get_room_owner(self, room_id: str) -> str | None: ...
    async def verify_room_agent_membership(
        self, room_id: str, agent_id: str
    ) -> bool: ...


__all__ = [
    "RoomHistoryReader",
    "RoomOwnershipReader",
]
