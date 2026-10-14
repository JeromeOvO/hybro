"""Chat product operations on rooms: create, list, rename, members, delete."""

import uuid

from common.dto import (
    CreateRoomRequest,
    MembershipSeed,
    RoomInfo,
)
from common.utils.time import utcnow
from conversation import RoomDeletionService
from models.request import RoomCenterRoomSettingRequest
from models.response import RoomCenterRoomSettingResponse
from models.room import MembershipOriginStatus, Room


class ChatService:
    def __init__(self) -> None:
        self._facade = None
        self._bound = False
        self._room_deletion: RoomDeletionService | None = None

    def reset_bindings(self) -> None:
        """Clear process-global composition state before a new lifespan starts."""
        self.__init__()

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def bind_room_deletion(self, service: RoomDeletionService) -> None:
        self._room_deletion = service

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
        )
        return [name for name, is_bound in checks if not is_bound]

    def _require_facade(self):
        if not getattr(self, "_bound", False) or getattr(self, "_facade", None) is None:
            raise RuntimeError(
                "ChatService.bind_facade() not called - startup incomplete"
            )
        return self._facade

    @staticmethod
    def _legacy_room_from_info(info: RoomInfo) -> Room:
        return Room(
            room_id=info.room_id,
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
            membership_origin_status=ChatService._legacy_membership_origin_status(
                info.membership_origin_status
            ),
            source_group_id=info.source_group_id,
            source_group_name=info.source_group_name,
            extend_info=info.extend_info,
            processing_message_id=info.processing_message_id,
        )

    @staticmethod
    def _legacy_membership_origin_status(status: str | None) -> str:
        if status == "active":
            return MembershipOriginStatus.MANUAL.value
        return status or MembershipOriginStatus.MANUAL.value

    def _room_setting_response_from_info(
        self,
        info: RoomInfo,
        *,
        active_runs: list[dict] | None = None,
    ) -> RoomCenterRoomSettingResponse:
        room = self._legacy_room_from_info(info)
        return RoomCenterRoomSettingResponse(
            room_id=room.room_id,
            room=room,
            active_runs=active_runs,
            success=True,
            error=None,
            status_code=200,
        )

    def _membership_seed_from_request(
        self,
        request: RoomCenterRoomSettingRequest,
    ) -> MembershipSeed:
        requesting_user_id = request.requesting_user_id or request.room_owner_id
        if request.membership_seed_input is not None:
            return MembershipSeed(
                mode=request.membership_seed_input,
                agent_ids=request.room_agent_ids,
                group_id=request.seed_group_id,
                requesting_user_id=requesting_user_id,
            )
        if request.seed_all_current_agents:
            return MembershipSeed(
                mode="all_current_agents",
                requesting_user_id=requesting_user_id,
            )
        if request.applied_from_group:
            return MembershipSeed(
                mode="saved_group",
                group_id=request.applied_from_group,
                requesting_user_id=requesting_user_id,
            )
        agent_ids = None
        if request.room_agent_set is not None:
            agent_ids = list(self._normalize_room_agent_set(request.room_agent_set))
        return MembershipSeed(
            mode="manual",
            agent_ids=agent_ids,
            requesting_user_id=requesting_user_id,
        )

    @staticmethod
    def _room_error_response(
        *,
        room_id: str | None = None,
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
            room_id=room_id,
            room=None,
            success=False,
            error=error,
            status_code=status_code,
        )

    # === room_agent_set normalization helpers ===
    @staticmethod
    def _looks_like_agent_id(value: str) -> bool:
        """
        Heuristic check to determine if a string looks like an agent_id (UUID style).
        """
        if not isinstance(value, str):
            return False
        try:
            uuid.UUID(value)
            return True
        except Exception:
            return False

    def _normalize_room_agent_set(self, room_agent_set: dict | None) -> dict[str, str]:
        """
        Normalize room_agent_set to the canonical shape: {agent_id: agent_name}.

        Historically some data used {agent_name: agent_id}. This method detects
        the dominant pattern and returns a mapping keyed by agent_id so that:
        - Multiple agents with the same name are supported
        - Backend logic can rely on agent_id keys.
        """
        if not room_agent_set:
            return {}

        # Count how many keys/values look like IDs
        keys_look_like_ids = sum(
            1 for k in room_agent_set.keys() if self._looks_like_agent_id(k)
        )
        values_look_like_ids = sum(
            1 for v in room_agent_set.values() if self._looks_like_agent_id(v)
        )

        # If keys already look like IDs (or it's ambiguous), assume correct shape
        if keys_look_like_ids >= values_look_like_ids:
            # Cast to concrete type for callers
            return dict(room_agent_set)

        # Otherwise we likely have {agent_name: agent_id} and should flip it
        normalized: dict[str, str] = {}
        for agent_name, agent_id in room_agent_set.items():
            if not isinstance(agent_id, str):
                # Skip malformed entries
                continue
            normalized[agent_id] = str(agent_name)

        return normalized

    # room setting management
    async def create_new_room(
        self, room_create_request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        if room_create_request.room_name is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                success=False,
                error="Room name is required",
                status_code=400,
            )
        if room_create_request.room_owner_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                success=False,
                error="Room owner id is required",
                status_code=400,
            )
        if room_create_request.room_owner_name is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                success=False,
                error="Room owner name is required",
                status_code=400,
            )

        try:
            info = await facade.create_room(
                CreateRoomRequest(
                    owner_id=room_create_request.room_owner_id,
                    owner_name=room_create_request.room_owner_name,
                    room_name=room_create_request.room_name,
                    membership_seed=self._membership_seed_from_request(
                        room_create_request
                    ),
                    extend_info=room_create_request.extend_info or None,
                )
            )
        except ValueError as exc:
            return self._room_error_response(error=str(exc))
        return self._room_setting_response_from_info(info)

    async def inquiry_room_setting(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        info = await facade.get_room(room_id)
        if info is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )
        return self._room_setting_response_from_info(info)

    async def inquiry_rooms_by_room_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        if request.room_owner_id is None:
            return RoomCenterRoomSettingResponse(
                room_list=None,
                success=False,
                error="Room owner id is required",
                status_code=400,
            )

        room_owner_id = request.room_owner_id
        infos = await facade.list_rooms_for_owner(room_owner_id)
        return RoomCenterRoomSettingResponse(
            room_list=[self._legacy_room_from_info(info) for info in infos],
            success=True,
            error=None,
            status_code=200,
        )

    async def inquiry_room_history_by_owner_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        if request.room_owner_id is None:
            return RoomCenterRoomSettingResponse(
                room_list=None,
                success=False,
                error="Room owner id is required",
                status_code=400,
            )

        infos = await facade.list_room_history_for_owner(
            request.room_owner_id,
            limit=100,
        )
        return RoomCenterRoomSettingResponse(
            room_list=[self._legacy_room_from_info(info) for info in infos],
            success=True,
            error=None,
            status_code=200,
        )

    async def update_room_agent_set(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        has_membership_input = (
            request.membership_seed_input is not None
            or request.room_agent_set is not None
            or request.seed_all_current_agents
            or request.applied_from_group is not None
        )
        if not has_membership_input:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room agent set or canonical membership input is required",
                status_code=400,
            )
        try:
            info = await facade.replace_membership(
                room_id,
                self._membership_seed_from_request(request),
                requesting_user_id=request.requesting_user_id,
            )
        except ValueError as exc:
            return self._room_error_response(room_id=room_id, error=str(exc))
        return self._room_setting_response_from_info(info)

    async def update_room_name(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        if request.room_name is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room name is required",
                status_code=400,
            )
        try:
            info = await facade.update_room(room_id, {"room_name": request.room_name})
        except ValueError as exc:
            return self._room_error_response(room_id=room_id, error=str(exc))
        if info is None:
            return self._room_error_response(
                room_id=None,
                error="Room not found",
                status_code=404,
            )
        return self._room_setting_response_from_info(info)

    async def update_room_history_fields(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        facade = self._require_facade()
        if request.room_id is None:
            return self._room_error_response(
                room_id=None, error="Room id is required", status_code=400
            )
        updates: dict = {}
        if request.is_pinned is not None:
            updates["is_pinned"] = request.is_pinned
            if not request.is_pinned:
                updates["pin_order"] = None
        if request.pin_order is not None:
            updates["pin_order"] = request.pin_order
        if not updates:
            return self._room_error_response(
                room_id=request.room_id,
                error="No history fields supplied",
                status_code=400,
            )
        try:
            info = await facade.update_room(request.room_id, updates)
        except ValueError as exc:
            return self._room_error_response(room_id=request.room_id, error=str(exc))
        if info is None:
            return self._room_error_response(
                room_id=request.room_id, error="Room not found", status_code=404
            )
        return self._room_setting_response_from_info(info)

    async def update_room_default_mode(
        self,
        room_id: str,
        *,
        use_supervisor: bool,
    ) -> bool:
        return bool(
            await self._require_facade().update_room_default_mode(
                room_id,
                use_supervisor=use_supervisor,
            )
        )

    async def delete_room_by_room_id(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterRoomSettingResponse:
        return await self._require_room_deletion().delete_room_by_room_id(request)


# Singleton export
chat_service = ChatService()


__all__ = ["ChatService", "chat_service"]
