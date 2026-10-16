from __future__ import annotations

from typing import Protocol, runtime_checkable

from models.request import (
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
)
from models.response import (
    RoomCenterActiveRunsResponse,
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
)


@runtime_checkable
class RoomCenterCompatibility(Protocol):
    async def create_new_room(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def inquiry_rooms_by_room_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def inquiry_room_history_by_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def inquiry_room_messages_by_room_id(
        self, request: RoomCenterRoomMessageRequest
    ) -> RoomCenterRoomMessageResponse: ...
    async def inquiry_room_setting(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def inquiry_active_runs(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterActiveRunsResponse: ...
    async def delete_room_by_room_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def update_room_agent_set(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def update_room_name(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def update_room_history_fields(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse: ...
    async def update_room_default_mode(
        self,
        room_id: str,
        *,
        use_supervisor: bool,
    ) -> bool: ...


__all__ = ["RoomCenterCompatibility"]
