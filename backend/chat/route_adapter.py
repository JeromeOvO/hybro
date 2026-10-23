from __future__ import annotations

from models.request import (
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
)
from models.response import (
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
)


class ChatRouteAdapter:
    """Serve the chat routes from the chat service and conversation reads."""

    def __init__(self, *, chat_service, conversation_runtime) -> None:
        self._chat_service = chat_service
        self._conversation_runtime = conversation_runtime

    async def create_new_room(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._chat_service.create_new_room(request)

    async def inquiry_room_setting(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._chat_service.inquiry_room_setting(request)

    async def delete_room_by_room_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._chat_service.delete_room_by_room_id(request)

    async def inquiry_rooms_by_room_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._chat_service.inquiry_rooms_by_room_owner_id(request)

    async def inquiry_room_history_by_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._chat_service.inquiry_room_history_by_owner_id(request)

    async def update_room_name(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._chat_service.update_room_name(request)

    async def update_room_history_fields(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._chat_service.update_room_history_fields(request)

    async def update_room_default_mode(
        self,
        room_id: str,
        *,
        use_supervisor: bool,
    ) -> bool:
        return await self._chat_service.update_room_default_mode(
            room_id,
            use_supervisor=use_supervisor,
        )

    async def inquiry_room_messages_by_room_id(
        self, request: RoomCenterRoomMessageRequest
    ) -> RoomCenterRoomMessageResponse:
        return await self._conversation_runtime.inquiry_room_messages_by_room_id(
            request
        )


__all__ = ["ChatRouteAdapter"]
