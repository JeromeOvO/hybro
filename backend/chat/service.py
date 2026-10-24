"""Chat product operations on rooms: create, list, rename, delete, read."""

from collections.abc import Awaitable, Callable

from common.dto import (
    CreateRoomRequest,
    MembershipSeed,
    RoomInfo,
)
from common.json_types import JsonValue
from common.utils.time import utcnow
from conversation import RoomDeletionService
from models.request import RoomCenterRoomMessageRequest
from models.response import (
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
)
from models.room import Room

RoomMessagePageReader = Callable[
    [RoomCenterRoomMessageRequest], Awaitable[RoomCenterRoomMessageResponse]
]


class ChatService:
    def __init__(self) -> None:
        self._facade = None
        self._bound = False
        self._room_deletion: RoomDeletionService | None = None
        self._message_reader: RoomMessagePageReader | None = None

    def reset_bindings(self) -> None:
        """Clear process-global composition state before a new lifespan starts."""
        self.__init__()

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def bind_room_deletion(self, service: RoomDeletionService) -> None:
        self._room_deletion = service

    def bind_message_reader(self, reader: RoomMessagePageReader) -> None:
        """Bind the conversation timeline page reader that serves room messages."""
        self._message_reader = reader

    def _require_message_reader(self) -> RoomMessagePageReader:
        reader = getattr(self, "_message_reader", None)
        if reader is None:
            raise RuntimeError("ChatService message reader has not been bound")
        return reader

    def _require_room_deletion(self) -> RoomDeletionService:
        service = getattr(self, "_room_deletion", None)
        if service is None:
            raise RuntimeError("ChatService room deletion service has not been bound")
        return service

    def missing_required_bindings(self) -> list[str]:
        checks = (
            ("facade", getattr(self, "_facade", None) is not None),
            (
                "room_deletion",
                getattr(self, "_room_deletion", None) is not None,
            ),
            (
                "message_reader",
                getattr(self, "_message_reader", None) is not None,
            ),
        )
        return [name for name, is_bound in checks if not is_bound]

    def _require_facade(self):
        if not getattr(self, "_bound", False) or getattr(self, "_facade", None) is None:
            raise RuntimeError(
                "ChatService.bind_facade() not called - startup incomplete"
            )
        return self._facade

    @staticmethod
    def _room_from_info(info: RoomInfo) -> Room:
        return Room(
            conversation_id=info.conversation_id,
            room_name=info.room_name,
            room_owner_id=info.owner_id,
            room_owner_name=info.owner_name or "",
            room_agent_set=dict(info.agent_set)
            if info.agent_set
            else {agent_id: agent_id for agent_id in info.agent_ids},
            room_created_at=info.created_at or utcnow(),
            last_activity_at=info.last_activity_at or info.created_at or utcnow(),
            is_pinned=info.is_pinned,
            pin_order=info.pin_order,
            membership_origin=info.membership_origin,
            membership_origin_status=info.membership_origin_status,
            source_group_id=info.source_group_id,
            source_group_name=info.source_group_name,
            extend_info=info.extend_info,
        )

    def _room_setting_response_from_info(
        self,
        info: RoomInfo,
        *,
        active_runs: list[dict] | None = None,
    ) -> RoomCenterRoomSettingResponse:
        room = self._room_from_info(info)
        return RoomCenterRoomSettingResponse(
            conversation_id=room.conversation_id,
            room=room,
            active_runs=active_runs,
            success=True,
            error=None,
            status_code=200,
        )

    @staticmethod
    def _membership_seed(
        *,
        owner_id: str,
        membership_seed_input: str | None,
        room_agent_ids: list[str] | None,
        seed_group_id: str | None,
        seed_all_current_agents: bool | None,
    ) -> MembershipSeed:
        if membership_seed_input is not None:
            return MembershipSeed(
                mode=membership_seed_input,
                agent_ids=room_agent_ids,
                group_id=seed_group_id,
                requesting_user_id=owner_id,
            )
        if seed_all_current_agents:
            return MembershipSeed(
                mode="all_current_agents",
                requesting_user_id=owner_id,
            )
        return MembershipSeed(mode="manual", requesting_user_id=owner_id)

    def _room_list_response(
        self, infos: list[RoomInfo]
    ) -> RoomCenterRoomSettingResponse:
        return RoomCenterRoomSettingResponse(
            room_list=[self._room_from_info(info) for info in infos],
            success=True,
            error=None,
            status_code=200,
        )

    @staticmethod
    def _room_error_response(
        *,
        conversation_id: str | None = None,
        error: str,
        status_code: int | None = None,
    ) -> RoomCenterRoomSettingResponse:
        if status_code is None:
            lower = error.lower()
            if "permission" in lower or "access denied" in lower:
                status_code = 403
            elif "not found" in lower:
                status_code = 404
            else:
                status_code = 400
        return RoomCenterRoomSettingResponse(
            conversation_id=conversation_id,
            room=None,
            success=False,
            error=error,
            status_code=status_code,
        )

    # room setting management
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
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        if room_name is None:
            return RoomCenterRoomSettingResponse(
                conversation_id=None,
                success=False,
                error="Room name is required",
                status_code=400,
            )
        if owner_name is None:
            return RoomCenterRoomSettingResponse(
                conversation_id=None,
                success=False,
                error="Room owner name is required",
                status_code=400,
            )

        try:
            info = await facade.create_room(
                CreateRoomRequest(
                    owner_id=owner_id,
                    owner_name=owner_name,
                    room_name=room_name,
                    membership_seed=self._membership_seed(
                        owner_id=owner_id,
                        membership_seed_input=membership_seed_input,
                        room_agent_ids=room_agent_ids,
                        seed_group_id=seed_group_id,
                        seed_all_current_agents=seed_all_current_agents,
                    ),
                    extend_info=extend_info or None,
                )
            )
        except ValueError as exc:
            return self._room_error_response(error=str(exc))
        return self._room_setting_response_from_info(info)

    async def get_room(self, conversation_id: str) -> RoomCenterRoomSettingResponse:
        info = await self._require_facade().get_room(conversation_id)
        if info is None:
            return RoomCenterRoomSettingResponse(
                conversation_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )
        return self._room_setting_response_from_info(info)

    async def list_rooms_for_owner(
        self, owner_id: str
    ) -> RoomCenterRoomSettingResponse:
        infos = await self._require_facade().list_rooms_for_owner(owner_id)
        return self._room_list_response(infos)

    async def list_room_history(self, owner_id: str) -> RoomCenterRoomSettingResponse:
        infos = await self._require_facade().list_room_history_for_owner(
            owner_id,
            limit=100,
        )
        return self._room_list_response(infos)

    async def rename_room(
        self, conversation_id: str, room_name: str
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        try:
            info = await facade.update_room(conversation_id, {"room_name": room_name})
        except ValueError as exc:
            return self._room_error_response(
                conversation_id=conversation_id, error=str(exc)
            )
        if info is None:
            return self._room_error_response(
                conversation_id=None,
                error="Room not found",
                status_code=404,
            )
        return self._room_setting_response_from_info(info)

    async def update_history_fields(
        self,
        conversation_id: str,
        *,
        is_pinned: bool | None,
        pin_order: float | None,
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        updates: dict = {}
        if is_pinned is not None:
            updates["is_pinned"] = is_pinned
            if not is_pinned:
                updates["pin_order"] = None
        if pin_order is not None:
            updates["pin_order"] = pin_order
        if not updates:
            return self._room_error_response(
                conversation_id=conversation_id,
                error="No history fields supplied",
                status_code=400,
            )
        try:
            info = await facade.update_room(conversation_id, updates)
        except ValueError as exc:
            return self._room_error_response(
                conversation_id=conversation_id, error=str(exc)
            )
        if info is None:
            return self._room_error_response(
                conversation_id=conversation_id, error="Room not found", status_code=404
            )
        return self._room_setting_response_from_info(info)

    async def update_room_default_mode(
        self,
        conversation_id: str,
        *,
        use_supervisor: bool,
    ) -> bool:
        return bool(
            await self._require_facade().update_room_default_mode(
                conversation_id,
                use_supervisor=use_supervisor,
            )
        )

    async def delete_room(
        self, conversation_id: str, *, requesting_user_id: str
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_deletion().delete_room_by_room_id(
            conversation_id, requesting_user_id=requesting_user_id
        )

    async def read_messages(
        self,
        conversation_id: str,
        *,
        limit: int | None,
        cursor: str | None,
    ) -> RoomCenterRoomMessageResponse:
        return await self._require_message_reader()(
            RoomCenterRoomMessageRequest(
                conversation_id=conversation_id, limit=limit, cursor=cursor
            )
        )


# Singleton export
chat_service = ChatService()


__all__ = ["ChatService", "chat_service"]
