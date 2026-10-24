from __future__ import annotations

from typing import Protocol, runtime_checkable

from common.json_types import JsonValue
from models.response import (
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
)


@runtime_checkable
class ChatRooms(Protocol):
    async def create_room(
        self,
        *,
        owner_id: str,
        owner_name: str | None,
        room_name: str | None,
        extend_info: dict[str, JsonValue] | None,
        membership_seed_input: str | None,
        room_agent_ids: list[str] | None,
        seed_group_id: str | None,
        seed_all_current_agents: bool | None,
    ) -> RoomCenterRoomSettingResponse: ...
    async def get_room(self, conversation_id: str) -> RoomCenterRoomSettingResponse: ...
    async def list_rooms_for_owner(
        self, owner_id: str
    ) -> RoomCenterRoomSettingResponse: ...
    async def list_room_history(
        self, owner_id: str
    ) -> RoomCenterRoomSettingResponse: ...
    async def rename_room(
        self, conversation_id: str, room_name: str
    ) -> RoomCenterRoomSettingResponse: ...
    async def update_history_fields(
        self,
        conversation_id: str,
        *,
        is_pinned: bool | None,
        pin_order: float | None,
    ) -> RoomCenterRoomSettingResponse: ...
    async def update_room_default_mode(
        self,
        conversation_id: str,
        *,
        use_supervisor: bool,
    ) -> bool: ...
    async def delete_room(
        self, conversation_id: str, *, requesting_user_id: str
    ) -> RoomCenterRoomSettingResponse: ...
    async def read_messages(
        self,
        conversation_id: str,
        *,
        limit: int | None,
        cursor: str | None,
    ) -> RoomCenterRoomMessageResponse: ...


__all__ = ["ChatRooms"]
