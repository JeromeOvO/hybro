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
from services.room_services import room_services


class RoomCenter:
    def __init__(self, room_services=None):
        self.room_services = room_services

    def bind_facade(self, facade) -> None:
        room_services.bind_facade(facade)
        self.room_services = room_services

    def bind_room_services(self, bound_room_services) -> None:
        self.room_services = bound_room_services

    def _require_room_services(self):
        if self.room_services is None or not getattr(
            self.room_services, "_bound", False
        ):
            raise RuntimeError(
                "RoomCenter.bind_facade() not called - startup incomplete"
            )
        return self.room_services

    def create_new_room(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return self._require_room_services().create_new_room(request)

    def inquiry_room_setting(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return self._require_room_services().inquiry_room_setting(request)

    def inquiry_active_runs(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterActiveRunsResponse:
        return self._require_room_services().inquiry_active_runs(request)

    def delete_room_by_room_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return self._require_room_services().delete_room_by_room_id(request)

    def inquiry_rooms_by_room_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return self._require_room_services().inquiry_rooms_by_room_owner_id(request)

    def update_room_agent_set(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return self._require_room_services().update_room_agent_set(request)

    def update_room_name(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return self._require_room_services().update_room_name(request)

    def update_room_extend_info(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return self._require_room_services().update_room_extend_info(request)

    def create_and_parse_user_message(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse:
        return self._require_room_services().create_and_parse_user_message(request)

    def inquiry_room_messages_by_room_id(
        self, request: RoomCenterRoomMessageRequest
    ) -> RoomCenterRoomMessageResponse:
        return self._require_room_services().inquiry_room_messages_by_room_id(request)

    def inquiry_agent_messages_by_related_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        return self._require_room_services().inquiry_agent_messages_by_related_message_id(request)

    def send_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: str = "room_team",
        mentioned_agent_ids: list[str] | None = None,
    ) -> RoomCenterUserMessageResponse:
        return self._require_room_services().send_message_to_room(
            request, target_group, mentioned_agent_ids
        )
