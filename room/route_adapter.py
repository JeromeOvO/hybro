from __future__ import annotations

from models.request import (
    RoomCenterAgentMessageRequest,
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
)
from models.response import (
    RoomCenterActiveRunsResponse,
    RoomCenterAgentMessageResponse,
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
    RoomCenterUserMessageResponse,
)


class RoomRouteAdapter:
    def __init__(
        self,
        bound_room_runtime=None,
        room_services=None,
        bound_room_services=None,
    ):
        self.room_runtime = bound_room_runtime
        if self.room_runtime is None:
            self.room_runtime = room_services
        if self.room_runtime is None:
            self.room_runtime = bound_room_services

    def bind_facade(self, facade) -> None:
        from room.compat.runtime import room_runtime

        room_runtime.bind_facade(facade)
        self.room_runtime = room_runtime

    def bind_room_runtime(self, bound_room_runtime) -> None:
        self.room_runtime = bound_room_runtime

    bind_room_services = bind_room_runtime

    def _require_room_services(self):
        if self.room_runtime is None or not getattr(
            self.room_runtime, "_bound", False
        ):
            raise RuntimeError(
                "RoomCenter.bind_facade() not called - startup incomplete"
            )
        return self.room_runtime

    async def create_new_room(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_services().create_new_room(request)

    async def inquiry_room_setting(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_services().inquiry_room_setting(request)

    async def inquiry_active_runs(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterActiveRunsResponse:
        return await self._require_room_services().inquiry_active_runs(request)

    async def delete_room_by_room_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_services().delete_room_by_room_id(request)

    async def inquiry_rooms_by_room_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_services().inquiry_rooms_by_room_owner_id(request)

    async def update_room_agent_set(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_services().update_room_agent_set(request)

    async def update_room_name(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_services().update_room_name(request)

    async def update_room_extend_info(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_services().update_room_extend_info(request)

    async def inquiry_room_messages_by_room_id(
        self, request: RoomCenterRoomMessageRequest
    ) -> RoomCenterRoomMessageResponse:
        return await self._require_room_services().inquiry_room_messages_by_room_id(
            request
        )

    async def inquiry_agent_messages_by_related_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        return await self._require_room_services().inquiry_agent_messages_by_related_message_id(
            request
        )

    async def send_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: str = "room_team",
        mentioned_agent_ids: list[str] | None = None,
    ) -> RoomCenterUserMessageResponse:
        return await self._require_room_services().send_message_to_room(
            request, target_group, mentioned_agent_ids
        )

    async def persist_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: str = "room_team",
        mentioned_agent_ids: list[str] | None = None,
    ):
        return await self._require_room_services().persist_message_to_room(
            request, target_group, mentioned_agent_ids
        )

    async def run_message_preflight_to_room(self, context):
        return await self._require_room_services().run_message_preflight_to_room(
            context
        )


__all__ = ["RoomRouteAdapter"]
