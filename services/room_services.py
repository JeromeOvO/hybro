import re
import uuid
from datetime import timedelta
from enum import Enum
from uuid import uuid4

from a2a.types import (
    FileWithUri,
    FilePart,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from common.utils.cancellation import CancellationToken
from common.utils.context_utils import (
    build_context_for_agent,
    build_minimal_context,
    migrate_legacy_memory,
)
from common.dto import CreateRoomRequest, MembershipSeed, RoomInfo
from common.utils.logger import get_logger
from common.utils.time import ensure_utc, utcnow
from dataclasses import dataclass
from models.agent import AgentStatus
from models.file_upload import MAX_ATTACHMENTS_PER_MESSAGE
from models.memory import MemoryContent
from models.request import (
    AgentCenterRequest,
    RoomCenterAgentMessageRequest,
    RoomCenterMemoryRequest,
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
    TaskCenterRequest,
)
from models.response import (
    RoomAgentRef,
    RoomCenterActiveRunsResponse,
    RoomCenterAgentMessageResponse,
    RoomCenterRoomMessageResponse,
    RoomCenterRoomSettingResponse,
    RoomCenterUserMessageResponse,
    ScopeResolutionError,
)
from models.room import (
    MAX_MESSAGE_LENGTH,
    CoordinatorAgentId,
    MembershipOrigin,
    MembershipOriginStatus,
    MessageContent,
    Room,
    RoomAgentMessage,
    RoomMessage,
    RoomUserMessage,
    UserAttachment,
)
from models.room_services_models import ParseResult
from services.a2a_constants import SSEProcessingStatus, is_terminal_state
from services.a2a_service import a2a_service
from services.agent_selection_service import agent_selection_service
from services.agent_service import agent_service
from services.database_service import db_service
from services.memory_service import room_memory_service
from services.openai_service import openai_service
from services.run_lifecycle_service import record_and_maybe_broadcast_run_event
from services.sse_services import sse_manager
from services.task_service import task_service

logger = get_logger(__name__)


class DispatchStrategy(str, Enum):
    """Dispatch strategy resolved after agent selection."""
    SINGLE = "single"
    SEQUENTIAL = "sequential"
    SEQUENTIAL_DEBATE = "sequential_debate"
    SUPERVISOR = "supervisor"


def resolve_strategy(
    use_supervisor: bool,
    is_debate_mode: bool,
    agent_count: int,
) -> DispatchStrategy:
    """Resolve dispatch strategy from room flags and agent count."""
    if use_supervisor:
        return DispatchStrategy.SUPERVISOR
    if is_debate_mode:
        return DispatchStrategy.SEQUENTIAL_DEBATE
    if agent_count > 1:
        return DispatchStrategy.SEQUENTIAL
    return DispatchStrategy.SINGLE


def _human_size(size_bytes: int) -> str:
    """Format bytes as human-readable string: 512B, 245KB, 1.2MB."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def build_turn_content(
    message_text: str, attachments: list[UserAttachment] | None
) -> str:
    """Build conversation turn content with optional attachment annotations."""
    content = message_text or ""
    if attachments:
        descriptions = []
        for att in attachments:
            size_str = _human_size(att.size_bytes) if att.size_bytes else "unknown size"
            descriptions.append(f"{att.file_name} ({att.mime_type}, {size_str})")
        content += f"\n[Attachments: {', '.join(descriptions)}]"
    return content


@dataclass
class _ResolvedAttachments:
    attachments: list[UserAttachment]
    content_summary: dict | None


class RoomServices:
    def __init__(self):
        self.database_service = db_service  # Use singleton
        self.agent_service = agent_service  # Use singleton
        self.openai_service = openai_service  # Use singleton
        self.a2a_service = a2a_service  # Use singleton
        # Note: room_memory_service will be set after it's defined below

        self.room_memory_service = room_memory_service  # Use singleton
        self.sse_manager = sse_manager  # Use singleton
        self.task_service = task_service  # Use singleton
        self._s3_service = None
        self._facade = None
        self._bound = False
        self._context_memory_manager = None

    @property
    def s3_service(self):
        if self._s3_service is None:
            from services.s3_service import s3_service

            self._s3_service = s3_service
        return self._s3_service

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def bind_context_memory(self, memory_manager) -> None:
        self._context_memory_manager = memory_manager

    def _require_facade(self):
        if not getattr(self, "_bound", False) or getattr(self, "_facade", None) is None:
            raise RuntimeError(
                "RoomServices.bind_facade() not called - startup incomplete"
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
            membership_origin=info.membership_origin,
            membership_origin_status=RoomServices._legacy_membership_origin_status(
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

    @staticmethod
    def _derive_required_input_modes(
        user_message: RoomUserMessage,
    ) -> list[str] | None:
        """Return resolved MIME types if the message has attachments, else None.

        The return value signals binary "has attachments" to AgentMatcher's I/O scoring.
        A non-None list (even empty after dedup) means attachments are present.
        """
        attachments = (
            user_message.message_content.attachments
            if user_message.message_content else None
        )
        if not attachments:
            return None
        return [att.mime_type for att in attachments]

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

    @staticmethod
    def _active_run_payloads_from_raw(active_runs_raw: list) -> list[dict]:
        """Normalize Mongo run documents to ActiveRunRef-compatible dicts."""
        return [
            {
                "run_id": r.get("run_id"),
                "state": r.get("state"),
                "trigger_message_id": r.get("trigger_message_id"),
                "agent_id": r.get("agent_id"),
                "seq": r.get("seq", 0),
                "updated_at": r.get("updated_at"),
            }
            for r in active_runs_raw
            if isinstance(r, dict) and r.get("run_id") and r.get("state")
        ]

    async def _resolve_membership_input(
        self,
        request: RoomCenterRoomSettingRequest,
        existing_room: Room | None = None,
    ) -> tuple[dict[str, str], MembershipOrigin, MembershipOriginStatus, str | None, str | None] | RoomCenterRoomSettingResponse:
        """Resolve canonical or legacy membership input into a concrete agent set + provenance.

        Returns either a 5-tuple (agent_set, origin, origin_status, source_group_id, source_group_name)
        or a RoomCenterRoomSettingResponse error to return early.

        Precedence: canonical fields win over legacy fields.
        """
        has_canonical = request.membership_seed_input is not None
        has_legacy = request.room_agent_set is not None
        requesting_user = request.requesting_user_id

        # Canonical path
        if has_canonical:
            seed = request.membership_seed_input

            if seed == "manual":
                agent_ids = request.room_agent_ids or []
                agent_set: dict[str, str] = {}
                unknown_ids: list[str] = []
                for aid in agent_ids:
                    agent = await self.database_service.get_agent_by_agent_id(aid)
                    if not agent:
                        unknown_ids.append(aid)
                        continue
                    agent_set[aid] = agent.agent_card.name
                if unknown_ids:
                    return RoomCenterRoomSettingResponse(
                        room_id=None, room=None, success=False,
                        error=f"Unknown or deleted agent IDs: {', '.join(unknown_ids)}",
                        status_code=400,
                    )
                return (agent_set, MembershipOrigin.MANUAL, MembershipOriginStatus.MANUAL, None, None)

            if seed == "saved_group":
                if not request.seed_group_id:
                    return RoomCenterRoomSettingResponse(
                        room_id=None, room=None, success=False,
                        error="seed_group_id is required for saved_group seed input",
                        status_code=400,
                    )
                group = await self.database_service.get_agent_group_by_id(request.seed_group_id)
                if not group:
                    return RoomCenterRoomSettingResponse(
                        room_id=None, room=None, success=False,
                        error=f"Saved group {request.seed_group_id} not found",
                        status_code=404,
                    )
                if group.type != "builtin" and group.owner_id != requesting_user:
                    return RoomCenterRoomSettingResponse(
                        room_id=None, room=None, success=False,
                        error="You do not have permission to use this saved group",
                        status_code=403,
                    )
                agent_set = {}
                for aid in group.agents:
                    agent = await self.database_service.get_agent_by_agent_id(aid)
                    if agent and agent.agent_status == AgentStatus.active:
                        agent_set[aid] = agent.agent_card.name
                return (agent_set, MembershipOrigin.SAVED_GROUP, MembershipOriginStatus.SEEDED_NEVER_EDITED, group.group_id, group.name)

            if seed == "all_current_agents":
                all_agents = await self.database_service.get_all_active_agents(
                    user_id=requesting_user,
                )
                agent_set = {
                    a.agent_id: a.agent_card.name
                    for a in (all_agents or [])
                }
                return (agent_set, MembershipOrigin.ALL_CURRENT_AGENTS, MembershipOriginStatus.SEEDED_NEVER_EDITED, None, None)

            return RoomCenterRoomSettingResponse(
                room_id=None, room=None, success=False,
                error=f"Invalid membership_seed_input: {seed}",
                status_code=400,
            )

        # Legacy path
        if has_legacy:
            normalized = self._normalize_room_agent_set(request.room_agent_set)
            if request.applied_from_group:
                group = await self.database_service.get_agent_group_by_id(request.applied_from_group)
                group_name = group.name if group else None
                return (normalized, MembershipOrigin.SAVED_GROUP, MembershipOriginStatus.SEEDED_NEVER_EDITED, request.applied_from_group, group_name)
            return (normalized, MembershipOrigin.MANUAL, MembershipOriginStatus.MANUAL, None, None)

        # No membership input at all
        if existing_room is not None:
            return (
                existing_room.room_agent_set or {},
                existing_room.membership_origin or MembershipOrigin.MANUAL,
                existing_room.membership_origin_status or MembershipOriginStatus.MANUAL,
                existing_room.source_group_id,
                existing_room.source_group_name,
            )

        # New room with no membership input: empty snapshot
        return ({}, MembershipOrigin.MANUAL, MembershipOriginStatus.MANUAL, None, None)

    async def _validate_agents_access(
        self, agent_ids: list[str], user_id: str
    ) -> list[str]:
        """
        Validate that the user has access to all specified agents.
        Private agents can only be added by their owner.
        
        Returns:
            List of inaccessible agent IDs (empty if all accessible)
        """
        if not agent_ids:
            return []
        
        # Fetch all agents in one query
        agents = await self.database_service.get_agents_with_conditions(
            {"agent_id": {"$in": agent_ids}}
        )
        
        return [agent.agent_id for agent in agents if not agent.is_public and agent.provider_id != user_id]

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
                )
            )
        except ValueError as exc:
            return self._room_error_response(error=str(exc))
        return self._room_setting_response_from_info(info)

        # Resolve membership input (canonical or legacy)
        result = await self._resolve_membership_input(room_create_request)
        if isinstance(result, RoomCenterRoomSettingResponse):
            return result
        agent_set, origin, origin_status, source_group_id, source_group_name = result

        # Validate access to agents
        requesting_user = room_create_request.requesting_user_id or room_create_request.room_owner_id
        if agent_set and requesting_user:
            inaccessible = await self._validate_agents_access(
                list(agent_set.keys()), requesting_user
            )
            if inaccessible:
                return RoomCenterRoomSettingResponse(
                    room_id=None, room=None, success=False,
                    error=f"Access denied to private agents: {', '.join(inaccessible)}",
                    status_code=403,
                )

        if room_create_request.room is not None:
            room = room_create_request.room
        else:
            room = Room(
                room_id=str(uuid4()),
                room_name=room_create_request.room_name,
                room_owner_id=room_create_request.room_owner_id,
                room_owner_name=room_create_request.room_owner_name,
                room_agent_set=agent_set,
                room_created_at=utcnow(),
                applied_from_group=room_create_request.applied_from_group,
                membership_origin=origin,
                membership_origin_status=origin_status,
                source_group_id=source_group_id,
                source_group_name=source_group_name,
                extend_info=room_create_request.extend_info or None,
            )

        success = await self.database_service.add_room(room)
        if success:
            resolved_agents, room_default_status = await self._resolve_room_agent_refs(
                room.room_agent_set, viewer_user_id=requesting_user
            )
            return RoomCenterRoomSettingResponse(
                room_id=room.room_id,
                room=room,
                resolved_agents=resolved_agents,
                room_default_status=room_default_status,
                success=True,
                error=None,
                status_code=200,
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Failed to create room",
                status_code=500,
            )

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
        active_runs_raw = await self.database_service.get_active_runs_by_room_id(
            room_id
        )
        return self._room_setting_response_from_info(
            info,
            active_runs=self._active_run_payloads_from_raw(active_runs_raw),
        )

        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )
        else:
            # Ensure room_agent_set is always returned in canonical {agent_id: agent_name} form
            normalized_agent_set = self._normalize_room_agent_set(room.room_agent_set)
            needs_write = normalized_agent_set != (room.room_agent_set or {})
            if needs_write:
                room.room_agent_set = normalized_agent_set

            # Backfill canonical provenance for legacy rooms
            if room.membership_origin is None:
                if room.applied_from_group:
                    room.membership_origin = MembershipOrigin.SAVED_GROUP
                    room.membership_origin_status = MembershipOriginStatus.SEEDED_NEVER_EDITED
                    room.source_group_id = room.applied_from_group
                elif room.room_agent_set:
                    room.membership_origin = MembershipOrigin.MANUAL
                    room.membership_origin_status = MembershipOriginStatus.MANUAL
                else:
                    room.membership_origin = MembershipOrigin.MANUAL
                    room.membership_origin_status = MembershipOriginStatus.MANUAL
                needs_write = True

            if needs_write:
                await self.database_service.update_room_by_room_id(room_id, room)

            resolved_agents, room_default_status = await self._resolve_room_agent_refs(
                room.room_agent_set, viewer_user_id=request.requesting_user_id
            )
            active_runs_raw = await self.database_service.get_active_runs_by_room_id(
                room.room_id
            )
            active_runs = self._active_run_payloads_from_raw(active_runs_raw)

            return RoomCenterRoomSettingResponse(
                room_id=room.room_id,
                room=room,
                resolved_agents=resolved_agents,
                room_default_status=room_default_status,
                active_runs=active_runs,
                success=True,
                error=None,
                status_code=200,
            )

    async def inquiry_active_runs(
        self, request: RoomCenterRoomSettingRequest
    ) -> RoomCenterActiveRunsResponse:
        """Return non-terminal runs for a room (same run shape as inquiry_room_setting)."""
        if request.room_id is None:
            return RoomCenterActiveRunsResponse(
                room_id=None,
                active_runs=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterActiveRunsResponse(
                room_id=None,
                active_runs=None,
                success=False,
                error="Room not found",
                status_code=404,
            )

        active_runs_raw = await self.database_service.get_active_runs_by_room_id(
            room.room_id
        )
        active_runs = self._active_run_payloads_from_raw(active_runs_raw)
        return RoomCenterActiveRunsResponse(
            room_id=room.room_id,
            active_runs=active_runs,
            success=True,
            error=None,
            status_code=200,
        )

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

        rooms = await self.database_service.get_rooms_by_room_owner_id(room_owner_id)
        return RoomCenterRoomSettingResponse(
            room_list=rooms, success=True, error=None, status_code=200
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
                room_id=None, room=None, success=False,
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

        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )

        has_membership_input = (
            request.membership_seed_input is not None
            or request.room_agent_set is not None
        )
        if not has_membership_input:
            return RoomCenterRoomSettingResponse(
                room_id=None, room=None, success=False,
                error="Room agent set or canonical membership input is required",
                status_code=400,
            )

        result = await self._resolve_membership_input(request, existing_room=room)
        if isinstance(result, RoomCenterRoomSettingResponse):
            return result
        new_agent_set, origin, origin_status, source_group_id, source_group_name = result

        # Validate access to NEW agents
        if request.requesting_user_id:
            existing_ids = set(room.room_agent_set.keys()) if room.room_agent_set else set()
            new_ids = set(new_agent_set.keys()) - existing_ids
            if new_ids:
                inaccessible = await self._validate_agents_access(
                    list(new_ids), request.requesting_user_id
                )
                if inaccessible:
                    return RoomCenterRoomSettingResponse(
                        room_id=room_id, room=None, success=False,
                        error=f"Access denied to private agents: {', '.join(inaccessible)}",
                        status_code=403,
                    )

        room.room_agent_set = new_agent_set

        # Update provenance from the resolved result.
        if request.membership_seed_input is not None or request.applied_from_group:
            room.membership_origin = origin
            room.membership_origin_status = origin_status
            room.source_group_id = source_group_id
            room.source_group_name = source_group_name
        else:
            # Legacy manual edit without applied_from_group
            if room.membership_origin in (MembershipOrigin.SAVED_GROUP, MembershipOrigin.ALL_CURRENT_AGENTS):
                room.membership_origin_status = MembershipOriginStatus.SEEDED_EDITED
            else:
                room.membership_origin = MembershipOrigin.MANUAL
                room.membership_origin_status = MembershipOriginStatus.MANUAL
        success = await self.database_service.update_room_by_room_id(room_id, room)
        if success:
            resolved_agents, room_default_status = await self._resolve_room_agent_refs(
                room.room_agent_set, viewer_user_id=request.requesting_user_id
            )
            return RoomCenterRoomSettingResponse(
                room_id=room_id, room=room,
                resolved_agents=resolved_agents,
                room_default_status=room_default_status,
                success=True, error=None, status_code=200,
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to update room agent set",
                status_code=500,
            )

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

        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )

        if request.room_name is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room name is required",
                status_code=400,
            )

        room.room_name = request.room_name
        success = await self.database_service.update_room_by_room_id(room_id, room)
        if success:
            return RoomCenterRoomSettingResponse(
                room_id=room_id, room=room, success=True, error=None, status_code=200
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to update room name",
                status_code=500,
            )

    async def update_room_extend_info(
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
        if request.extend_info is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Extend info is required",
                status_code=400,
            )
        try:
            info = await facade.update_room(room_id, {"extend_info": request.extend_info})
        except ValueError as exc:
            return self._room_error_response(room_id=room_id, error=str(exc))
        if info is None:
            return self._room_error_response(
                room_id=None,
                error="Room not found",
                status_code=404,
            )
        return self._room_setting_response_from_info(info)

        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Room not found",
                status_code=404,
            )

        if request.extend_info is None:
            return RoomCenterRoomSettingResponse(
                room_id=None,
                room=None,
                success=False,
                error="Extend info is required",
                status_code=400,
            )

        room.extend_info = request.extend_info
        success = await self.database_service.update_room_by_room_id(room_id, room)
        if success:
            return RoomCenterRoomSettingResponse(
                room_id=room_id, room=room, success=True, error=None, status_code=200
            )
        else:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to update room extend info",
                status_code=500,
            )

    async def delete_room_by_room_id(
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
        actual_owner_id = await facade.get_room_owner(room_id)
        if actual_owner_id is None:
            return self._room_error_response(
                room_id=room_id,
                error="Room not found",
                status_code=404,
            )
        requested_owner_id = (
            request.requesting_user_id
            or request.room_owner_id
            or actual_owner_id
        )
        if requested_owner_id != actual_owner_id:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Forbidden",
                status_code=403,
            )
        success = await facade.delete_room(room_id, actual_owner_id)
        if not success:
            return RoomCenterRoomSettingResponse(
                room_id=room_id,
                room=None,
                success=False,
                error="Failed to delete room",
                status_code=500,
            )
        cleanup_ok = await self._cleanup_context_memory_for_room(room_id)
        await self._delete_transitional_non_room_data(room_id)
        if not cleanup_ok:
            logger.warning(
                "Context & Memory cleanup failed after room deletion for room %s",
                room_id,
            )
        return RoomCenterRoomSettingResponse(
            room_id=room_id, room=None, success=True, error=None, status_code=200
        )

    async def _cleanup_context_memory_for_room(self, room_id: str) -> bool:
        # TODO(Phase 9): Move this transitional cleanup behind a repository/DAL
        # boundary when the remaining legacy room data paths are retired.
        if self._context_memory_manager is None:
            logger.warning(
                "Context & Memory cleanup skipped for room %s; manager not bound",
                room_id,
            )
            return False
        try:
            ok = await self._context_memory_manager.delete_room_memory(room_id)
            if not ok:
                logger.warning(
                    "Context & Memory cleanup reported failure for room %s",
                    room_id,
                )
            return bool(ok)
        except Exception:
            logger.warning(
                "Context & Memory cleanup failed for room %s",
                room_id,
                exc_info=True,
            )
            return False

    async def _delete_transitional_non_room_data(self, room_id: str) -> None:
        s3_cleanup_ok = True
        try:
            await self.s3_service.delete_prefix(f"uploads/{room_id}/")
            await self.s3_service.delete_prefix(f"artifacts/{room_id}/")
        except Exception:
            s3_cleanup_ok = False
            logger.warning(
                "S3 cleanup failed for room %s; orphan job will retry", room_id
            )

        if s3_cleanup_ok:
            try:
                from database.mongodb import mongodb

                await mongodb.file_uploads_collection.delete_many({"room_id": room_id})
            except Exception:
                logger.warning(
                    "Transitional file upload cleanup failed for room %s",
                    room_id,
                    exc_info=True,
                )

    # --- Attachment resolution helpers ---

    async def _resolve_attachments(
        self,
        file_ids: list[str],
        room_id: str,
    ) -> "_ResolvedAttachments | RoomCenterUserMessageResponse":
        """Resolve file_id list to server-authoritative UserAttachment objects."""
        from database.mongodb import mongodb

        if len(file_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=f"Maximum {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message",
                status_code=400,
            )

        attachments: list[UserAttachment] = []
        for file_id in file_ids:
            file_meta = await mongodb.file_uploads_collection.find_one(
                {"file_id": file_id, "room_id": room_id}
            )
            if not file_meta:
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error=f"File {file_id} not found",
                    status_code=404,
                )
            attachments.append(
                UserAttachment(
                    file_id=file_id,
                    s3_key=file_meta["s3_key"],
                    mime_type=file_meta["mime_type"],
                    file_name=file_meta["file_name"],
                    size_bytes=file_meta["size_bytes"],
                )
            )

        content_summary = None
        if attachments:
            mime_types = [a.mime_type for a in attachments]
            content_summary = {
                "has_images": any(m.startswith("image/") for m in mime_types),
                "has_files": any(not m.startswith("image/") for m in mime_types),
                "attachment_count": len(attachments),
                "mime_types": mime_types,
            }

        return _ResolvedAttachments(
            attachments=attachments, content_summary=content_summary
        )

    async def _resolve_and_apply_attachments(
        self,
        request: RoomCenterUserMessageRequest,
        user_message: RoomUserMessage,
    ) -> RoomCenterUserMessageResponse | None:
        """Collect file_ids from both sources, resolve, and apply to message.

        Returns an error response if validation fails, or None on success.
        """
        file_ids: list[str] = []
        seen: set[str] = set()

        if request.attachments:
            for att in request.attachments:
                if att.file_id not in seen:
                    file_ids.append(att.file_id)
                    seen.add(att.file_id)

        if request.inline_file_ids:
            for fid in request.inline_file_ids:
                if fid not in seen:
                    file_ids.append(fid)
                    seen.add(fid)

        if user_message.message_content:
            user_message.message_content.attachments = None
            user_message.message_content.content_summary = None

        if file_ids:
            resolved = await self._resolve_attachments(file_ids, request.room_id)
            if isinstance(resolved, RoomCenterUserMessageResponse):
                return resolved
            user_message.message_content.attachments = resolved.attachments
            user_message.message_content.content_summary = resolved.content_summary

        return None

    FILE_CAPABLE_EXACT = frozenset({"file", "*/*"})
    FILE_CAPABLE_PREFIXES = frozenset({"image/", "audio/", "video/"})
    FILE_CAPABLE_MIMES = frozenset(
        {
            "application/pdf",
            "application/octet-stream",
            "application/zip",
            "application/x-tar",
            "application/gzip",
        }
    )

    async def _build_message_parts(
        self,
        text: str,
        attachments: list[UserAttachment] | None,
        agent_card,
    ) -> list:
        """Build A2A message parts from text and optional attachments."""
        parts = [TextPart(text=text)]

        if not attachments:
            return parts

        agent_input_modes_raw = getattr(agent_card, "default_input_modes", None)
        if agent_input_modes_raw is None:
            agent_input_modes_raw = getattr(agent_card, "defaultInputModes", None)
        agent_input_modes = set(agent_input_modes_raw or ["text"])

        supports_files = bool(
            agent_input_modes & self.FILE_CAPABLE_EXACT
            or agent_input_modes & self.FILE_CAPABLE_MIMES
            or any(
                any(m.startswith(prefix) for prefix in self.FILE_CAPABLE_PREFIXES)
                for m in agent_input_modes
            )
        )

        if supports_files:
            for att in attachments:
                presigned_url = await self.s3_service.generate_presigned_url(att.s3_key)
                parts.append(
                    FilePart(
                        file=FileWithUri(
                            uri=presigned_url,
                            mime_type=att.mime_type,
                            name=att.file_name,
                        )
                    )
                )

        return parts

    # room user message management
    def parse_agent_mentions(
        self, message_text: str, room_agent_set: dict
    ) -> list[dict]:
        """
        Parse @agent mentions in format "<@agent-id|agentname>"

        Args:
            message_text: User input text with format "<@agent-id|agentname>"
            room_agent_set: Agent set in the room {agent_id: agent_name}

        Returns:
            list[dict]: Parsed mentions [{"agent_id": "xxx", "agent_name": "yyy", "mention_text": "<@xxx|yyy>"}]
        """
        mentions = []

        # pattern: <@agent_id|agent_name>
        slack_pattern = r"<@([^|]+)\|([^>]+)>"

        for match in re.finditer(slack_pattern, message_text):
            agent_id = match.group(1).strip()
            agent_name = match.group(2).strip()
            position = match.start()

            # Check if agent exists in room by agent_id
            if agent_id in room_agent_set:
                # Agent found in room
                room_agent_name = room_agent_set[agent_id]
                mentions.append(
                    {
                        "agent_id": agent_id,
                        "agent_name": room_agent_name,  # Use the name from room_agent_set
                        "mention_text": match.group(0),
                        "position": position,
                    }
                )
            else:
                logger.warning(
                    "Inline mention %s not in room agent set — ignored", agent_id
                )

        # Sort by position to maintain order
        mentions.sort(key=lambda x: x["position"])
        return mentions

    def extract_agent_message_content(
        self,
        message_text: str,
        target_agent_id: str,
        target_agent_name: str,  # kept for signature compatibility
        all_mentions: list,
    ) -> str:
        """
        Extract message content relevant to a specific agent
        Remove @mentions and return clean task content

        Args:
            message_text: Original message text
            target_agent_id: Target agent ID
            target_agent_name: Target agent name
            all_mentions: All parsed mentions from the message

        Returns:
            str: Clean message content relevant to the target agent
        """
        # Find all mentions for this specific agent
        agent_mentions = [m for m in all_mentions if m["agent_id"] == target_agent_id]

        if not agent_mentions:
            # No mentions found, remove all mentions and return clean content
            processed_text = message_text
            for mention in all_mentions:
                processed_text = processed_text.replace(mention["mention_text"], "")
            return re.sub(r"\s+", " ", processed_text).strip()

        # Strategy 1: Extract text around each mention of this agent
        relevant_parts = []

        for mention in agent_mentions:
            mention_pos = mention.get("position")
            mention_text = mention.get("mention_text", "")

            if mention_pos is None:
                context_clean = message_text
                for m in all_mentions:
                    context_clean = context_clean.replace(m.get("mention_text", ""), "")
                context_clean = re.sub(r"\s+", " ", context_clean).strip()
                if context_clean and context_clean not in relevant_parts:
                    relevant_parts.append(context_clean)
                continue

            start_pos = mention_pos
            end_pos = mention_pos + len(mention_text)

            # Extend backwards to find sentence start
            while start_pos > 0 and message_text[start_pos - 1] not in ".!?\n":
                start_pos -= 1

            # Extend forwards to find sentence end
            while end_pos < len(message_text) and message_text[end_pos] not in ".!?\n":
                end_pos += 1

            # Include the sentence ending punctuation
            if end_pos < len(message_text) and message_text[end_pos] in ".!?\n":
                end_pos += 1

            # Extract the relevant sentence/context
            context = message_text[start_pos:end_pos].strip()

            # Remove ALL mentions from this context, not just replace with @agent_name
            context_clean = context
            for m in all_mentions:
                context_clean = context_clean.replace(m["mention_text"], "")

            # Clean up whitespace
            context_clean = re.sub(r"\s+", " ", context_clean).strip()

            if context_clean and context_clean not in relevant_parts:
                relevant_parts.append(context_clean)

        # Join all relevant parts
        if relevant_parts:
            return " ".join(relevant_parts)
        else:
            # Fallback: return original message with all mentions removed
            processed_text = message_text
            for mention in all_mentions:
                processed_text = processed_text.replace(mention["mention_text"], "")
            return re.sub(r"\s+", " ", processed_text).strip()

    def group_mentions_by_context(self, message_text: str, mentions: list) -> dict:
        """
        Group mentions by their shared context/sentence and detect consecutive mentions

        Args:
            message_text: Original message text
            mentions: List of parsed mentions

        Returns:
            dict: {context_text: {"mentions": [mentions], "is_consecutive": bool}}
        """
        context_groups = {}

        for mention in mentions:
            mention_pos = mention.get("position")
            mention_text = mention.get("mention_text", "")

            if mention_pos is None:
                context = message_text.strip()
            else:
                start_pos = mention_pos
                end_pos = mention_pos + len(mention_text)

                while start_pos > 0 and message_text[start_pos - 1] not in ".!?\n":
                    start_pos -= 1

                while end_pos < len(message_text) and message_text[end_pos] not in ".!?\n":
                    end_pos += 1

                if end_pos < len(message_text) and message_text[end_pos] in ".!?\n":
                    end_pos += 1

                context = message_text[start_pos:end_pos].strip()

            # Group mentions by context
            if context not in context_groups:
                context_groups[context] = {"mentions": [], "is_consecutive": False}
            context_groups[context]["mentions"].append(mention)

        # Detect consecutive mentions within each context
        for context, group_info in context_groups.items():
            mentions_in_context = group_info["mentions"]
            if len(mentions_in_context) > 1:
                all_have_position = all(
                    "position" in m for m in mentions_in_context
                )
                if not all_have_position:
                    continue

                mentions_in_context.sort(key=lambda x: x["position"])

                is_consecutive = True
                for i in range(len(mentions_in_context) - 1):
                    current_mention = mentions_in_context[i]
                    next_mention = mentions_in_context[i + 1]

                    # Get text between mentions
                    between_start = current_mention["position"] + len(
                        current_mention["mention_text"]
                    )
                    between_end = next_mention["position"]
                    between_text = message_text[between_start:between_end].strip()

                    # If there's significant text between mentions (more than just spaces/commas),
                    # they're not consecutive
                    if len(between_text) > 10 or any(
                        word in between_text.lower()
                        for word in [
                            "and",
                            "then",
                            "also",
                            "but",
                            "however",
                            "meanwhile",
                        ]
                    ):
                        is_consecutive = False
                        break

                group_info["is_consecutive"] = is_consecutive

        return context_groups

    def create_shared_message_content(
        self, context_text: str, mentions_in_context: list
    ) -> str:
        """
        Create message content for multiple agents sharing the same context
        Remove all @mentions and return clean task content

        Args:
            context_text: The shared context/sentence
            mentions_in_context: List of mentions in this context

        Returns:
            str: Clean message content without @mentions
        """
        processed_text = context_text

        # Remove all mentions (both simple @agent and Slack-style <@id|name>)
        for mention in mentions_in_context:
            mention_text = mention["mention_text"]
            processed_text = processed_text.replace(mention_text, "")

        # Clean up extra spaces and normalize whitespace
        processed_text = re.sub(r"\s+", " ", processed_text).strip()

        return processed_text

    def create_task_for_agent(
        self,
        user_message: RoomUserMessage,
        agent_id: str,
        agent_name: str,
        all_mentions: list,
    ) -> Task:
        """
        Create a2a Task for specific agent with relevant message content only

        Args:
            user_message: User message
            agent_id: Target agent ID
            agent_name: Target agent name
            all_mentions: All parsed mentions from the message

        Returns:
            Task: a2a protocol Task object
        """
        # Extract relevant message content for this agent
        original_text = user_message.message_content.message_text
        agent_relevant_text = self.extract_agent_message_content(
            original_text, agent_id, agent_name, all_mentions
        )

        # Create Message
        message = Message(
            message_id=user_message.message_id,
            role=Role.user,
            parts=[TextPart(text=agent_relevant_text)],  # Use filtered content
            context_id=user_message.room_id,
            metadata={},
        )

        # Create Task status
        task_status = TaskStatus(
            state=TaskState.submitted, timestamp=utcnow().isoformat()
        )

        # Create Task
        task = Task(
            id=str(uuid4()),
            context_id=user_message.room_id,
            status=task_status,
            history=[message],
        )

        return task

    async def create_task_for_agents_group(
        self, user_message: RoomUserMessage, mentions_group: list, shared_content: str
    ) -> list:
        """
        Create a2a Tasks for a group of agents sharing the same message content

        Args:
            user_message: User message
            mentions_group: List of mentions sharing the same context
            shared_content: Shared message content

        Returns:
            list: List of Task objects for each agent
        """
        tasks = []

        for mention in mentions_group:
            agent_id = mention["agent_id"]
            agent_name = mention["agent_name"]

            # Create Message with shared content
            message = Message(
                message_id=f"{user_message.message_id}_{agent_id}",  # Unique ID per agent
                role=Role.user,
                parts=[TextPart(text=shared_content)],
                context_id=user_message.room_id,
                metadata={},
            )

            # Create Task status
            task_status = TaskStatus(
                state=TaskState.submitted, timestamp=utcnow().isoformat()
            )

            # Create Task
            task = Task(
                id=str(uuid4()),
                context_id=user_message.room_id,
                status=task_status,
                history=[message],
            )

            tasks.append({"task": task, "agent_id": agent_id, "agent_name": agent_name})

        return tasks

    def _generate_agent_message_content(self, content: str) -> MessageContent:
        """
        Generate agent message content based on content.
        """
        a2a_message = Message(
            message_id=str(uuid4()),
            role=Role.user,
            parts=[TextPart(text=content)],
            context_id=str(uuid4()),
            metadata={},
        )

        # Create Task status
        task_status = TaskStatus(
            state=TaskState.submitted, timestamp=utcnow().isoformat()
        )

        # Create a2a Task
        task = Task(
            id=str(uuid4()),
            context_id=str(uuid4()),
            status=task_status,
            history=[a2a_message],
        )

        # Store the task; message_text is left empty until the agent produces output
        # (streaming artifacts or terminal response will populate it).
        return MessageContent(message_task=task)

    def _generate_new_agent_message(
        self,
        room_id: str,
        related_message_id: str,
        agent_id: str,
        content: str,
        user_id: str | None = None,
        extend_info: dict | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        turn_id: str | None = None,
        client_request_id: str | None = None,
    ) -> RoomAgentMessage:
        """
        Generate a new agent message.

        Args:
            room_id: The room ID
            related_message_id: The related message ID (parent in dependency chain)
            agent_id: The agent ID (can be None for auto-assignment)
            content: The task content
            user_id: The user ID
            extend_info: Additional info
            step_number: Current step number in the workflow (1-indexed)
            total_steps: Total number of steps in the workflow
            task_content: The task description being processed
        """
        return RoomAgentMessage(
            room_id=room_id,
            related_message_id=related_message_id
            if related_message_id
            else str(uuid4()),
            agent_id=agent_id if agent_id else None,
            user_id=user_id,
            message_id=str(uuid4()),
            message_content=self._generate_agent_message_content(content),
            message_created_at=utcnow(),
            extend_info=extend_info if extend_info else None,
            step_number=step_number,
            total_steps=total_steps,
            task_content=task_content
            or content,  # Use task_content if provided, else content
            turn_id=turn_id,
            client_request_id=client_request_id,
        )

    def create_agent_message(
        self,
        room_id: str,
        related_message_id: str,
        agent_id: str,
        content: str,
        user_id: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        turn_id: str | None = None,
        client_request_id: str | None = None,
    ) -> RoomAgentMessage:
        """Public wrapper around ``_generate_new_agent_message`` for use by
        ``SupervisorExecutor`` and other external callers that need to create
        individual agent messages without accessing a private method."""
        return self._generate_new_agent_message(
            room_id=room_id,
            related_message_id=related_message_id,
            agent_id=agent_id,
            content=content,
            user_id=user_id,
            step_number=step_number,
            total_steps=total_steps,
            task_content=task_content,
            turn_id=turn_id,
            client_request_id=client_request_id,
        )

    async def _generate_agent_messages_based_on_parsed_result(
        self,
        parsed_result: dict,
        user_message_id: str,
        room_id: str,
        user_id: str | None = None,
        extend_info: dict | None = None,
        turn_id: str | None = None,
        client_request_id: str | None = None,
    ) -> list[RoomAgentMessage]:
        """
        Generate agent messages based on parsed result from LLM.
        All steps are converted to agent messages, even if agent_id is None.

        Args:
            parsed_result: Output from parse_user_message_by_llm()
                {
                    "message_type": str,
                    "original_text": str,
                    "task_steps": [
                        {
                            "step_id": str,
                            "agent_id": str | None,
                            "agent_name": str | None,
                            "task_content": str,
                            "dependencies": [step_id, ...]
                        }
                    ]
                }
            user_message_id: User message ID (root for dependency chain)
            room_id: Room ID

        Returns:
            list[RoomAgentMessage]: Generated agent messages (agent_id may be None)
        """

        agent_messages = []
        task_steps = parsed_result.get("task_steps", [])

        if not task_steps:
            logger.warning("No task steps in parsed result")
            return agent_messages

        # In direct chat the single step's task_content is the raw user message,
        # which shouldn't be echoed in the task status bubble.
        is_direct_chat = parsed_result.get("message_type") == "DIRECT_CHAT"

        original_text = parsed_result.get("original_text", "")

        # Calculate total steps for progress tracking
        total_steps = len(task_steps)

        # Map step_id to generated agent_message_id for dependency resolution
        step_to_message_id = {}

        for step_index, step in enumerate(task_steps, start=1):
            step_id = step.get("step_id")
            agent_id = step.get("agent_id")  # Can be None
            agent_name = step.get("agent_name")  # Can be None
            task_content = step.get("task_content", "")
            dependencies = step.get("dependencies", [])

            # Skip only if no task content
            if not task_content:
                logger.warning(f"Step {step_id} has no task content, skipping")
                continue

            # Resolve related_message_id based on dependencies
            if not dependencies:
                # No dependencies: relate directly to user message
                related_message_id = user_message_id
            else:
                # Has dependencies: relate to the last dependency's agent message
                last_dependency_step_id = dependencies[-1]
                related_message_id = step_to_message_id.get(
                    last_dependency_step_id,
                    user_message_id,  # Fallback if dependency not found
                )

                # Log if dependency not found
                if last_dependency_step_id not in step_to_message_id:
                    logger.warning(
                        f"Step {step_id} depends on {last_dependency_step_id}, "
                        f"but it's not found. Using user message as fallback."
                    )

            # Create a2a Message with step tracking info
            agent_message = self._generate_new_agent_message(
                room_id,
                related_message_id,
                agent_id,
                task_content,
                user_id=user_id,
                extend_info=extend_info,
                step_number=step_index,
                total_steps=total_steps,
                turn_id=turn_id,
                client_request_id=client_request_id,
            )

            # In direct chat the task_content equals the user's original message,
            # which would be redundantly echoed in the task status bubble.
            # Clear it so the frontend shows a generic "Working on your request…" instead.
            # Also clear in multi-agent rooms when the LLM simply passed through the
            # user's message verbatim (no meaningful decomposition).
            # BUT: never clear task_content in debate mode — debate_service needs it
            # to inject prior agent responses into the prompt.
            is_debate = parsed_result.get("message_type", "").startswith("DEBATE")
            if not is_debate and (
                is_direct_chat or task_content.strip() == original_text.strip()
            ):
                agent_message.task_content = None

            agent_messages.append(agent_message)

            # Store mapping for dependency resolution
            step_to_message_id[step_id] = agent_message.message_id

            # Save to database
            agent_message_success = await self.database_service.add_room_agent_message(
                agent_message
            )
            if not agent_message_success:
                logger.warning(
                    f"Failed to add agent message {agent_message.message_id}"
                )

            logger.info(
                f"Generated agent message {agent_message.message_id} for step {step_id} ({step_index}/{total_steps})"
            )

        return agent_messages

    @staticmethod
    def _build_agent_registry(
        agents: list | None,
        selected_agent_set: dict,
    ) -> list:
        """Build an ``AgentProfile`` list from resolved agents or the agent set."""
        from models.supervisor_v2 import AgentProfile

        registry: list[AgentProfile] = []
        if agents:
            for agent in agents:
                registry.append(AgentProfile.from_agent(agent))
        else:
            for agent_id, agent_name in selected_agent_set.items():
                registry.append(
                    AgentProfile(
                        agent_id=agent_id,
                        agent_name=agent_name,
                        description="",
                        is_healthy=False,
                    )
                )
        return registry

    async def _prepare_for_supervisor_v2(
        self,
        room: Room,
        user_message: RoomUserMessage,
        message_text: str,
        agents: list | None,
        selected_agent_set: dict,
        is_debate_mode: bool,
        room_memory: "RoomMemory | None",
        token: CancellationToken | None = None,
    ) -> ParseResult:
        """Prepare extend_info for supervisor execution.

        This method:
        - Does NOT call the supervisor LLM
        - Does NOT create any ``RoomAgentMessage`` records
        - ONLY stores the data needed for ``SupervisorExecutor.run()``
        - Builds budget-aware supervisor context via ContextAssemblyService (§11.1)

        Agent messages are created one at a time inside
        ``SupervisorExecutor._dispatch_targets``.
        """
        from models.supervisor_v2 import RoomConfig
        from services.context_assembly_service import context_assembly_service

        if token and token.is_cancelled:
            logger.info(
                "RoomServices: Message parsing cancelled (V2) for %s",
                user_message.message_id,
            )
            self.sse_manager.clear_cancellation(user_message.message_id)
            return ParseResult(success=False, canceled=True)

        agent_registry = self._build_agent_registry(agents, selected_agent_set)

        room_config = RoomConfig(
            is_debate_mode=is_debate_mode,
            room_agent_set=selected_agent_set,
        )

        # Build budget-aware context via ContextAssemblyService (§11.1)
        agent_dicts = [p.model_dump(mode="json") for p in agent_registry]
        conversation_context: str | None = None
        memory_search_results = None
        if room_memory:
            try:
                from services.memory_search_service import memory_search_service

                search_response = await memory_search_service.search(
                    query=message_text,
                    room_id=room.room_id,
                )
                if search_response.results:
                    memory_search_results = search_response.results
            except Exception as e:
                logger.debug(
                    "RoomServices: MemorySearch skipped: %s", e
                )
            try:
                result = context_assembly_service.build_supervisor_context(
                    room_memory=room_memory,
                    current_task=message_text,
                    agent_registry=agent_dicts,
                    max_turns=5,
                    memory_search_results=memory_search_results,
                )
                conversation_context = result.context
            except Exception as e:
                logger.warning(
                    "RoomServices: ContextAssemblyService failed, falling back: %s", e
                )

        if user_message.extend_info is None:
            user_message.extend_info = {}
        user_message.extend_info.update({
            "supervisor_v2": True,
            "agent_registry": agent_dicts,
            "room_config": room_config.model_dump(mode="json"),
            "conversation_context": conversation_context,
        })
        await self.database_service.update_room_user_message_by_message_id(
            user_message.message_id, user_message
        )

        logger.info(
            "RoomServices: V2 supervisor data prepared for message %s (%d agents)",
            user_message.message_id,
            len(agent_registry),
        )

        return ParseResult(success=True)

    # ------------------------------------------------------------------
    # V2 Supervisor clarify-resume preparation (Phase 4, §7.4)
    # ------------------------------------------------------------------

    CLARIFY_TTL_SECONDS: int = 3600  # 1 hour

    async def _prepare_clarify_resume_v2(
        self,
        room: Room,
        user_message: RoomUserMessage,
        message_text: str,
        pending_clarify_msg_id: str,
        agents: list | None,
        selected_agent_set: dict,
        is_debate_mode: bool,
        room_memory: "RoomMemory | None",
    ) -> bool:
        """Check whether a pending CLARIFY can be resumed and prepare extend_info.

        Returns ``True`` if the user message was prepared for clarify-resume
        (``extend_info`` updated with ``supervisor_v2_clarify_resume``).
        Returns ``False`` if the pending clarification is stale, missing, or
        otherwise invalid — the caller should fall through to a fresh V2 run.
        """
        from models.supervisor_v2 import RoomConfig, SupervisorTrajectory, TrajectoryStatus

        original_msg = (
            await self.database_service.get_room_user_message_by_message_id(
                pending_clarify_msg_id
            )
        )
        if not original_msg or not isinstance(original_msg.extend_info, dict):
            logger.warning(
                "RoomServices: clarify resume — original message %s not found "
                "or missing extend_info, clearing stale flag",
                pending_clarify_msg_id,
            )
            await self._clear_pending_clarification(room)
            return False

        traj_data = original_msg.extend_info.get("supervisor_trajectory")
        if not traj_data:
            logger.warning(
                "RoomServices: clarify resume — no trajectory on message %s, "
                "clearing stale flag",
                pending_clarify_msg_id,
            )
            await self._clear_pending_clarification(room)
            return False

        try:
            trajectory = SupervisorTrajectory(**traj_data)
        except Exception as e:
            logger.warning(
                "RoomServices: clarify resume — failed to deserialize trajectory: %s",
                e,
            )
            await self._clear_pending_clarification(room)
            return False

        if trajectory.status != "clarifying":
            logger.info(
                "RoomServices: clarify resume — trajectory status is %s (not 'clarifying'), "
                "treating as fresh request",
                trajectory.status,
            )
            await self._clear_pending_clarification(room)
            return False

        # TTL check: if the last entry's started_at is older than CLARIFY_TTL_SECONDS,
        # the clarification has gone stale.
        if not trajectory.entries:
            logger.warning(
                "RoomServices: clarify resume — trajectory has no entries for "
                "message %s, clearing stale flag",
                pending_clarify_msg_id,
            )
            await self._clear_pending_clarification(room)
            return False

        last_entry = trajectory.entries[-1]
        age = (utcnow() - ensure_utc(last_entry.started_at)).total_seconds()
        if age > self.CLARIFY_TTL_SECONDS:
            logger.info(
                "RoomServices: clarify resume — stale (%.0fs > %ds), "
                "treating as fresh request",
                age,
                self.CLARIFY_TTL_SECONDS,
            )
            await self._clear_pending_clarification(room)
            return False

        # All checks passed — prepare the user message for clarify-resume.
        # Set the user's reply on the trajectory so the supervisor sees it.
        trajectory.clarify_user_reply = message_text
        trajectory.status = TrajectoryStatus.RUNNING

        agent_registry = self._build_agent_registry(agents, selected_agent_set)

        room_config = RoomConfig(
            is_debate_mode=is_debate_mode,
            room_agent_set=selected_agent_set,
        )

        # Build budget-aware context via ContextAssemblyService (§11.1)
        conversation_context: str | None = None
        if room_memory:
            from services.context_assembly_service import context_assembly_service

            memory_search_results = None
            try:
                from services.memory_search_service import memory_search_service

                search_response = await memory_search_service.search(
                    query=message_text,
                    room_id=room.room_id,
                )
                if search_response.results:
                    memory_search_results = search_response.results
            except Exception as e:
                logger.debug(
                    "RoomServices: MemorySearch skipped in clarify-resume: %s", e
                )
            try:
                agent_dicts = [p.model_dump(mode="json") for p in agent_registry]
                ctx_result = context_assembly_service.build_supervisor_context(
                    room_memory=room_memory,
                    current_task=message_text,
                    agent_registry=agent_dicts,
                    max_turns=5,
                    memory_search_results=memory_search_results,
                )
                conversation_context = ctx_result.context
            except Exception as e:
                logger.warning(
                    "RoomServices: ContextAssemblyService failed in clarify-resume: %s", e
                )

        if user_message.extend_info is None:
            user_message.extend_info = {}
        user_message.extend_info.update({
            "supervisor_v2": True,
            "supervisor_v2_clarify_resume": True,
            "clarify_original_message_id": pending_clarify_msg_id,
            "resumed_trajectory": trajectory.model_dump(mode="json"),
            "agent_registry": [p.model_dump(mode="json") for p in agent_registry],
            "room_config": room_config.model_dump(mode="json"),
            "conversation_context": conversation_context,
        })
        await self.database_service.update_room_user_message_by_message_id(
            user_message.message_id, user_message
        )

        # Clear the pending flag on the room
        await self._clear_pending_clarification(room)

        logger.info(
            "RoomServices: V2 clarify resume prepared for message %s "
            "(original: %s, %d agents)",
            user_message.message_id,
            pending_clarify_msg_id,
            len(agent_registry),
        )

        return True

    async def _clear_pending_clarification(self, room: Room) -> None:
        """Remove the ``pending_clarification_message_id`` flag from the room."""
        if isinstance(room.extend_info, dict):
            room.extend_info.pop("pending_clarification_message_id", None)
            await self.database_service.update_room_by_room_id(
                room.room_id, room
            )

    async def parse_user_message(
        self,
        room_id: str,
        user_message_id: str,
        message_text: str,
        selected_agent_set: dict,
        user_id: str | None = None,
        is_debate_mode: bool = False,
        auto_assign_agents: bool = False,
        target_group: str | None = None,
        agents: list | None = None,
        conversation_context: str | None = None,
        token: CancellationToken | None = None,
        client_request_id: str | None = None,
    ) -> ParseResult:
        """
        Parse user message

        Args:
            room_id: The room ID
            user_message_id: The user message ID
            message_text: The message text to parse
            selected_agent_set: Dict of {agent_id: agent_name} chosen for this request
            is_debate_mode: Whether to use debate mode
            auto_assign_agents: If True (Auto mode), LLM will auto-assign agents
            agents: Full Agent objects for detailed LLM context (optional)

        Returns:
            ParseResult with ``success`` and ``canceled`` flags.  The caller
            is responsible for sending the appropriate SSE terminal status.
        """
        # Check for cancellation before parsing
        if token and token.is_cancelled:
            logger.info(
                "RoomServices: Message parsing cancelled for %s, stopping all processing",
                user_message_id,
            )
            self.sse_manager.clear_cancellation(user_message_id)
            return ParseResult(success=False, canceled=True)

        # Direct chat: single agent + no debate = skip LLM parsing entirely
        direct_chat = not is_debate_mode and len(selected_agent_set) == 1

        if direct_chat:
            agent_id, agent_name = next(iter(selected_agent_set.items()))
            parsed_result = {
                "message_type": "DIRECT_CHAT",
                "original_text": message_text,
                "needs_decomposition": False,
                "task_steps": [
                    {
                        "step_id": "step_1",
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "task_content": message_text,
                        "dependencies": [],
                    }
                ],
            }
            logger.info("Direct chat mode: skipping LLM parsing for single agent")
        else:
            # Parse user message with full agent details for better LLM assignment
            parsed_result = await self.openai_service.parse_user_message_by_llm(
                message_text,
                selected_agent_set,
                is_debate_mode,
                auto_assign_agents,
                agents,
                conversation_context=conversation_context,
            )

        logger.info(f"LLM Parsed result: {parsed_result}")

        if not parsed_result:
            logger.warning("No parsed result from LLM")
            return ParseResult(success=False)

        extend_info = {
            "allowed_agent_ids": list(selected_agent_set.keys()),
            "target_group": target_group,
            "is_direct_chat": direct_chat,
        }

        agent_messages = await self._generate_agent_messages_based_on_parsed_result(
            parsed_result,
            user_message_id,
            room_id,
            user_id=user_id,
            extend_info=extend_info,
            client_request_id=client_request_id,
        )

        return ParseResult(success=True) if agent_messages else ParseResult(success=False)

    async def send_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: str = "room_team",
        mentioned_agent_ids: list[str] | None = None,
    ) -> RoomCenterUserMessageResponse:
        """Add and parse user message to room and send processing status to client."""

        validation_response = self._validate_send_message_request(request)
        if validation_response:
            return validation_response

        # Block new messages while an HITL request is pending (Risk 2 mitigation).
        # Check BEFORE persisting the message or creating a token so we don't
        # leave orphaned DB records on rejection.
        try:
            from services.hitl_service import hitl_service
            pending_hitl = await hitl_service.get_pending_requests(request.room_id)
            if pending_hitl:
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="An agent is waiting for your input. "
                          "Please reply to the pending request before sending a new message.",
                    status_code=409,
                )
        except Exception as e:
            logger.warning(
                "HITL pending check failed for room %s: %s — proceeding with message",
                request.room_id, e,
            )

        user_message = request.message
        client_request_id = (
            request.client_request_id
            if isinstance(getattr(request, "client_request_id", None), str)
            else None
        )
        if user_message is not None:
            # Breaking cutover invariant: canonical turn key is always present.
            user_message.client_request_id = client_request_id

        # Resolve attachments from both sources before persistence
        att_err = await self._resolve_and_apply_attachments(request, user_message)
        if att_err is not None:
            return att_err

        # ── Pre-persist scope validation ──────────────────────────────────
        # Fetch room early: needed for scope resolution and downstream flags.
        room = await self.database_service.get_room_by_room_id(request.room_id)
        if not room:
            return RoomCenterUserMessageResponse(
                message_id=None, message=None, success=False,
                error="Room not found", status_code=404,
            )

        is_debate_mode = (
            room.extend_info.get("debateMode", False) if room.extend_info else False
        )
        use_supervisor = (
            room.extend_info.get("use_supervisor", False) if room.extend_info else False
        )
        message_text = user_message.message_content.message_text

        # Validate deterministic scope BEFORE persistence so rejected messages
        # never get a real message_id in the database.
        # - canonical mentioned_agent_ids: validated via shared helper
        # - room_team / saved_group: validated via shared helper
        # - all_agents: LLM-driven, cannot pre-validate (persists first)
        # - legacy inline mentions: best-effort, not covered (persists first)
        pre_resolved_mentions: list[dict] | None = None
        pre_resolved_scope: tuple[dict, bool, list] | None = None

        if mentioned_agent_ids:
            mention_result = await self._validate_canonical_mentions(
                mentioned_agent_ids, sender_user_id=request.user_id,
            )
            if isinstance(mention_result, RoomCenterUserMessageResponse):
                return mention_result
            pre_resolved_mentions = mention_result
        elif target_group != "all_agents":
            scope_result = await self._resolve_explicit_target_scope(
                room, message_text, target_group, is_debate_mode,
                sender_user_id=request.user_id,
            )
            if isinstance(scope_result, RoomCenterUserMessageResponse):
                return scope_result
            pre_resolved_scope = scope_result

        # ── Persist ───────────────────────────────────────────────────────
        if not await self._persist_user_message(user_message):
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Failed to add message",
                status_code=500,
            )

        await self._send_processing_status(
            request.room_id,
            user_message.message_id,
            client_request_id,
        )

        # Create a CancellationToken early in the pipeline so the parse step
        # (and later the queue step in RoomMessageCenter) can detect cancels
        # via the token.  If the user already hit cancel before we got here,
        # the token is pre-signalled.
        token = self.sse_manager.create_token(user_message.message_id)

        memory_response = await self._initialize_room_memory(request, user_message)
        if memory_response:
            await record_and_maybe_broadcast_run_event(
                request.room_id,
                SSEProcessingStatus.FAILED,
                user_message.message_id,
                client_request_id=client_request_id,
                details="Failed to initialize room memory",
                sse=self.sse_manager,
            )
            await self.sse_manager.send_processing_status(
                request.room_id, SSEProcessingStatus.FAILED, user_message.message_id,
                client_request_id=client_request_id,
                details="Failed to initialize room memory",
            )
            return memory_response

        # ── Dispatch using pre-resolved scope ─────────────────────────────
        # Canonical mention dispatch: reuse pre-validated mention list.
        if pre_resolved_mentions:
            if (
                use_supervisor
                and isinstance(room.extend_info, dict)
                and room.extend_info.get("pending_clarification_message_id")
            ):
                await self._clear_pending_clarification(room)
            return await self._handle_mentions_flow(
                request, user_message, pre_resolved_mentions
            )

        # Legacy fallback: parse inline <@id|name> mentions from message text.
        # This runs AFTER persistence — legacy inline mentions are best-effort
        # and do not get reject-before-persist protection.
        #
        # When target_group is "all_agents", the room_agent_set may be empty
        # (e.g. newly created rooms from the homepage).  In that case, resolve
        # mentions against all active agents so inline @-mentions are honoured
        # regardless of room membership.
        if target_group == "all_agents":
            all_agents = await self.database_service.get_all_active_agents(
                user_id=request.user_id,
            )
            effective_agent_set = {
                a.agent_id: a.agent_card.name for a in (all_agents or [])
            }
        else:
            effective_agent_set = room.room_agent_set

        mentions = self.parse_agent_mentions(message_text, effective_agent_set)

        if mentions:
            if (
                use_supervisor
                and isinstance(room.extend_info, dict)
                and room.extend_info.get("pending_clarification_message_id")
            ):
                await self._clear_pending_clarification(room)
            return await self._handle_mentions_flow(request, user_message, mentions)

        # Target scope dispatch: reuse pre-resolved scope or run all_agents LLM.
        if target_group == "all_agents":
            required_modes = self._derive_required_input_modes(user_message)
            selection_result = await self._resolve_explicit_target_scope(
                room, message_text, target_group, is_debate_mode,
                sender_user_id=request.user_id,
                required_input_modes=required_modes,
            )
            if isinstance(selection_result, RoomCenterUserMessageResponse):
                # all_agents runs after persist — attach the real message_id
                # so the frontend knows the user message exists in the DB
                # and doesn't rollback optimistic state.
                selection_result.message_id = user_message.message_id
                # Processing status was set before selection; this early return
                # must emit terminal processing_status (runs + SSE) so the client
                # clears processing placeholders.
                await record_and_maybe_broadcast_run_event(
                    request.room_id,
                    SSEProcessingStatus.FAILED,
                    user_message.message_id,
                    client_request_id=client_request_id,
                    details=selection_result.error or "Agent selection failed",
                    sse=self.sse_manager,
                )
                await self.sse_manager.send_processing_status(
                    request.room_id,
                    SSEProcessingStatus.FAILED,
                    user_message.message_id,
                    client_request_id=client_request_id,
                    details=selection_result.error or "Agent selection failed",
                )
                return selection_result
            selected_agent_set, auto_assign, agents = selection_result
        elif pre_resolved_scope is not None:
            selected_agent_set, auto_assign, agents = pre_resolved_scope
        else:
            selected_agent_set, auto_assign, agents = {}, True, []

        if not selected_agent_set:
            return await self._handle_no_agents_fallback(
                request, user_message, target_group
            )

        # Resolve dispatch strategy and annotate in-memory user_message for
        # downstream dispatch logic. NOT persisted back to DB (message already written).
        dispatch_strategy = resolve_strategy(use_supervisor, is_debate_mode, len(selected_agent_set))
        if user_message.extend_info is None:
            user_message.extend_info = {}
        user_message.extend_info["dispatch_strategy"] = dispatch_strategy.value

        # Fetch room memory for context assembly.
        # V2 supervisor always needs room_memory for ContextAssemblyService (§11.1).
        # Non-V2 multi-agent paths need it for build_minimal_context.
        room_memory = None
        if use_supervisor or len(selected_agent_set) > 1:
            room_memory = await self.database_service.get_room_memory_by_room_id(
                request.room_id
            )
            if room_memory and room_memory.memory_content:
                room_memory.memory_content = migrate_legacy_memory(
                    room_memory.memory_content
                )

        # Build conversation_context for non-V2 paths (V1 decomposer, mentions, etc.)
        conversation_context = None
        if room_memory and room_memory.memory_content:
            conversation_context = build_minimal_context(
                room_memory.memory_content,
                current_task=message_text,
                max_turns=5,
            )

        # V2 Supervisor: lightweight preparation (no LLM call, no pre-generated messages)
        if use_supervisor:
            # --- Clarify resume check (§7.4) ---
            clarify_resume_prepared = False
            pending_clarify_msg_id = (
                room.extend_info.get("pending_clarification_message_id")
                if isinstance(room.extend_info, dict)
                else None
            )
            if pending_clarify_msg_id:
                clarify_resume_prepared = await self._prepare_clarify_resume_v2(
                    room=room,
                    user_message=user_message,
                    message_text=message_text,
                    pending_clarify_msg_id=pending_clarify_msg_id,
                    agents=agents,
                    selected_agent_set=selected_agent_set,
                    is_debate_mode=is_debate_mode,
                    room_memory=room_memory,
                )

            if clarify_resume_prepared:
                parse_result = ParseResult(success=True)
            else:
                parse_result = await self._prepare_for_supervisor_v2(
                    room=room,
                    user_message=user_message,
                    message_text=message_text,
                    agents=agents,
                    selected_agent_set=selected_agent_set,
                    is_debate_mode=is_debate_mode,
                    room_memory=room_memory,
                    token=token,
                )
        else:
            parse_result = await self.parse_user_message(
                request.room_id,
                user_message.message_id,
                message_text,
                selected_agent_set,
                user_message.user_id,
                is_debate_mode,
                auto_assign_agents=auto_assign,
                target_group=target_group,
                agents=agents,
                conversation_context=conversation_context,
                token=token,
                client_request_id=client_request_id,
            )

        if not parse_result.success:
            if parse_result.canceled:
                await record_and_maybe_broadcast_run_event(
                    request.room_id,
                    SSEProcessingStatus.CANCELED,
                    user_message.message_id,
                    client_request_id=client_request_id,
                    sse=self.sse_manager,
                )
                await self.sse_manager.send_processing_status(
                    request.room_id,
                    SSEProcessingStatus.CANCELED,
                    user_message.message_id,
                    client_request_id=client_request_id,
                )
            else:
                await record_and_maybe_broadcast_run_event(
                    request.room_id,
                    SSEProcessingStatus.FAILED,
                    user_message.message_id,
                    client_request_id=client_request_id,
                    details="Failed to parse user message",
                    sse=self.sse_manager,
                )
                await self.sse_manager.send_processing_status(
                    request.room_id, SSEProcessingStatus.FAILED, user_message.message_id,
                    client_request_id=client_request_id,
                    details="Failed to parse user message",
                )
            return RoomCenterUserMessageResponse(
                message_id=user_message.message_id,
                message=user_message,
                success=parse_result.canceled,
                error="Failed to parse user message" if not parse_result.canceled else None,
                status_code=200 if parse_result.canceled else 500,
            )

        return RoomCenterUserMessageResponse(
            message_id=user_message.message_id,
            dispatch_root_message_id=user_message.message_id,
            message=user_message,
            success=True,
            error=None,
            status_code=200,
        )

    def _check_message_text_length(
        self, message: RoomUserMessage | None
    ) -> RoomCenterUserMessageResponse | None:
        """Reject messages exceeding MAX_MESSAGE_LENGTH (SDR 2.10)."""
        if (
            message
            and message.message_content
            and message.message_content.message_text
            and len(message.message_content.message_text) > MAX_MESSAGE_LENGTH
        ):
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=f"Message text exceeds maximum length of {MAX_MESSAGE_LENGTH} characters",
                status_code=400,
            )
        return None

    def _validate_send_message_request(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse | None:
        """Validate required fields for send_message_to_room."""
        if request.room_id is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        if request.message is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Message is required",
                status_code=400,
            )

        size_err = self._check_message_text_length(request.message)
        if size_err:
            return size_err

        return None

    async def _persist_user_message(self, user_message: RoomUserMessage) -> bool:
        """Persist user message to the database."""
        return await self.database_service.add_room_user_message(user_message)

    async def _send_processing_status(self, room_id: str, message_id: str, client_request_id: str | None = None) -> None:
        """Notify client that processing has started.

        Args:
            room_id: The room ID.
            message_id: The persisted user message ID.
            client_request_id: Pass-through correlation ID from the frontend request.
        """
        logger.info(
            "RoomServices: Sending processing status to room %s for message %s",
            room_id,
            message_id,
        )
        await record_and_maybe_broadcast_run_event(
            room_id,
            SSEProcessingStatus.PROCESSING,
            message_id,
            client_request_id=client_request_id,
            sse=sse_manager,
        )
        await sse_manager.send_processing_status(
            room_id,
            SSEProcessingStatus.PROCESSING,
            message_id,
            client_request_id=client_request_id,
        )

    async def _initialize_room_memory(
        self, request: RoomCenterUserMessageRequest, user_message: RoomUserMessage
    ) -> RoomCenterUserMessageResponse | None:
        """Initialize or update room memory with conversation history."""
        # Get room to access room_agent_set for cleaning @mentions
        room = await self.database_service.get_room_by_room_id(request.room_id)
        room_agent_set = room.room_agent_set if room else {}

        room_memory_initialize_or_update_response = (
            await self.room_memory_service.initialize_or_update_room_memory(
                RoomCenterMemoryRequest(
                    room_id=request.room_id,
                    message_id=user_message.message_id,
                    memory_content=user_message.message_content.message_text,
                    room_agent_set=room_agent_set,  # Pass for cleaning @mentions
                    user_id=user_message.user_id,
                    attachments=user_message.message_content.attachments,
                )
            )
        )
        if not room_memory_initialize_or_update_response.success:
            return RoomCenterUserMessageResponse(
                message_id=user_message.message_id,
                message=user_message,
                success=False,
                error="Failed to initialize or update room memory",
                status_code=500,
            )
        return None

    async def _handle_mentions_flow(
        self,
        request: RoomCenterUserMessageRequest,
        user_message: RoomUserMessage,
        mentions: list[dict],
    ) -> RoomCenterUserMessageResponse:
        """Deterministically fan out to mentioned agents and finish.

        Note: Does NOT send processing_status COMPLETED here — the actual agent
        execution happens in a background task (process_room_user_message) which
        sends COMPLETED when all agents finish.  Sending it here would prematurely
        clear the frontend processing state and hide the Stop button.
        """
        room = await self.database_service.get_room_by_room_id(request.room_id)
        mention_response = await self.parse_user_message_with_mentions(
            room, user_message, mentions
        )
        return mention_response

    async def _validate_canonical_mentions(
        self,
        mentioned_agent_ids: list[str],
        sender_user_id: str | None,
    ) -> list[dict] | RoomCenterUserMessageResponse:
        """Validate and resolve mentioned_agent_ids into canonical mention dicts.

        Returns the mention list on success, or an error response on failure.
        Called once before persistence; the result is reused downstream.
        """
        canonical_mentions: list[dict] = []
        invalid_ids: list[str] = []
        for aid in mentioned_agent_ids:
            agent = await self.database_service.get_agent_by_agent_id(aid)
            if not agent or agent.agent_status != AgentStatus.active:
                invalid_ids.append(aid)
                continue
            if not agent.is_public and getattr(agent, "provider_id", None) != sender_user_id:
                invalid_ids.append(aid)
                continue
            canonical_mentions.append({
                "agent_id": aid,
                "agent_name": agent.agent_card.name,
                "mention_text": f"<@{aid}|{agent.agent_card.name}>",
            })

        if invalid_ids:
            error_msg = f"Invalid or unauthorized mention targets: {', '.join(invalid_ids)}"
            logger.warning(
                "Canonical mention targets rejected (invalid/unauthorized): %s",
                invalid_ids,
            )
            return RoomCenterUserMessageResponse(
                message_id=None, message=None, success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="unauthorized_mention", message=error_msg,
                ),
                status_code=400,
            )
        return canonical_mentions

    async def _resolve_explicit_target_scope(
        self,
        room: Room,
        message_text: str,
        target_group: str,
        is_debate_mode: bool,
        sender_user_id: str | None = None,
        required_input_modes: list[str] | None = None,
    ) -> tuple[dict, bool, list] | RoomCenterUserMessageResponse:
        """Resolve selected agents and auto-assign flag based on target_group.

        Returns:
            A 3-tuple (selected_agent_set, auto_assign, agents) on success,
            or a RoomCenterUserMessageResponse on scope-resolution failure.

        Deterministic failures (room_team empty, saved_group missing/unauthorized/empty)
        always return structured error responses — never empty tuples.
        """

        async def select_agents_all_agents_mode() -> tuple[dict, bool, list] | RoomCenterUserMessageResponse:
            try:
                selection_result = (
                    await agent_selection_service.select_agents_for_message(
                        message_text,
                        user_id=sender_user_id,
                        required_input_modes=required_input_modes,
                        is_debate_mode=is_debate_mode,
                    )
                )

                if selection_result.agents:
                    selected = {
                        agent.agent_id: agent.agent_name
                        for agent in selection_result.agents
                    }
                    full_agents = []
                    for agent_info in selection_result.agents:
                        full_agent = await self.database_service.get_agent_by_agent_id(
                            agent_info.agent_id
                        )
                        if full_agent:
                            full_agents.append(full_agent)

                    logger.info(
                        "All Agents mode: Selected %s agents with strategy=%s",
                        len(selection_result.agents),
                        selection_result.strategy.value,
                    )

                    return selected, True, full_agents

                logger.warning(
                    "All Agents mode: No agents found — returning empty scope"
                )
                return {}, True, []
            except Exception as e:
                error_msg = "Agent selection failed. Please try again."
                logger.error(
                    "All Agents mode selection failed: %s — returning scope error", e
                )
                return RoomCenterUserMessageResponse(
                    message_id=None, message=None, success=False,
                    error=error_msg,
                    scope_resolution_error=ScopeResolutionError(
                        code="empty_scope", message=error_msg,
                    ),
                    status_code=500,
                )

        if target_group == "all_agents":
            return await select_agents_all_agents_mode()

        if target_group == "room_team":
            if room.room_agent_set:
                logger.info(
                    "Room Default mode: Using %s room agents as candidate scope",
                    len(room.room_agent_set),
                )
                room_agents = await self._fetch_agents_from_set(room.room_agent_set)
                return room.room_agent_set, True, room_agents

            error_msg = "This room has no agents. Add agents before sending a message."
            logger.warning(
                "Room Default mode: room has no agents — returning scope-resolution error"
            )
            return RoomCenterUserMessageResponse(
                message_id=None, message=None, success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="empty_scope", message=error_msg,
                ),
                status_code=400,
            )

        # Custom group (saved-group override at send time)
        group = await self.database_service.get_agent_group_by_id(target_group)
        if not group:
            error_msg = "The selected agent group no longer exists. Please choose a different group."
            logger.warning(
                "Custom group %s not found — returning scope-resolution error",
                target_group,
            )
            return RoomCenterUserMessageResponse(
                message_id=None, message=None, success=False,
                error=error_msg,
                scope_resolution_error=ScopeResolutionError(
                    code="group_not_usable", message=error_msg,
                ),
                status_code=404,
            )

        if group.type != "builtin" and group.owner_id != sender_user_id:
            logger.warning(
                "Sender %s not authorized to use saved group %s (owner: %s)",
                sender_user_id, target_group, group.owner_id,
            )
            return RoomCenterUserMessageResponse(
                message_id=None, message=None, success=False,
                error="You do not have permission to use this saved group",
                status_code=403,
            )

        if group.agents:
            agents = []
            for agent_id in group.agents:
                agent = await self.database_service.get_agent_by_agent_id(agent_id)
                if agent:
                    agents.append(agent)

            selected_agent_set = {
                agent.agent_id: agent.agent_card.name for agent in agents
            }
            logger.info(
                "Custom group '%s': Using %s agents",
                group.name,
                len(selected_agent_set),
            )
            return selected_agent_set, True, agents

        error_msg = f"The selected agent group '{group.name}' has no members."
        logger.warning(
            "Custom group '%s' has no agents — returning scope-resolution error",
            group.name,
        )
        return RoomCenterUserMessageResponse(
            message_id=None, message=None, success=False,
            error=error_msg,
            scope_resolution_error=ScopeResolutionError(
                code="empty_scope", message=error_msg,
            ),
            status_code=400,
        )

    async def _fetch_agents_from_set(self, agent_set: dict | None) -> list:
        """Fetch full Agent objects from an agent_set dict {agent_id: agent_name}."""
        if not agent_set:
            return []

        agents = []
        for agent_id in agent_set.keys():
            agent = await self.database_service.get_agent_by_agent_id(agent_id)
            if agent:
                agents.append(agent)
        return agents

    async def _resolve_room_agent_refs(
        self, agent_set: dict | None, viewer_user_id: str | None = None
    ) -> tuple[list[RoomAgentRef], str]:
        """Resolve agent IDs into RoomAgentRef objects and compute room_default_status.

        Args:
            agent_set: {agent_id: agent_name} dict from the room snapshot.
            viewer_user_id: The current user requesting the read. When provided,
                private agents not owned by this user are marked ``inaccessible``.

        Returns (resolved_agents, room_default_status).
        """
        if not agent_set:
            return [], "empty"

        refs: list[RoomAgentRef] = []
        for agent_id, agent_name in agent_set.items():
            agent = await self.database_service.get_agent_by_agent_id(agent_id)
            if not agent:
                refs.append(RoomAgentRef(id=agent_id, name=agent_name, availability="deleted"))
            elif agent.agent_status != AgentStatus.active:
                refs.append(RoomAgentRef(id=agent_id, name=agent.agent_card.name, availability="inactive"))
            elif not agent.is_public and viewer_user_id and getattr(agent, "provider_id", None) != viewer_user_id:
                refs.append(RoomAgentRef(id=agent_id, name=agent.agent_card.name, availability="inaccessible"))
            else:
                refs.append(RoomAgentRef(id=agent_id, name=agent.agent_card.name, availability="available"))

        available_count = sum(1 for r in refs if r.availability == "available")
        if available_count == len(refs):
            status = "ok"
        elif available_count > 0:
            status = "degraded"
        else:
            status = "all_unavailable"

        return refs, status

    async def _handle_no_agents_fallback(
        self,
        request: RoomCenterUserMessageRequest,
        user_message: RoomUserMessage,
        target_group: str,
    ) -> RoomCenterUserMessageResponse:
        """Send a system message when no agents are available."""
        logger.warning(
            "No room agents and none found via selection; sending system agent response"
        )

        fallback_agent_message = self._generate_new_agent_message(
            room_id=request.room_id,
            related_message_id=user_message.message_id,
            agent_id=CoordinatorAgentId.SYSTEM,
            content=(
                "I couldn't find any agents for this room or via selection. "
                "Please choose agents or a group and try again."
            ),
            user_id=user_message.user_id,
            extend_info={
                "system_fallback": True,
                "reason": "no_agents_found",
                "target_group": target_group,
            },
            client_request_id=user_message.client_request_id,
        )

        added = await self.database_service.add_room_agent_message(
            fallback_agent_message
        )
        if not added:
            return RoomCenterUserMessageResponse(
                message_id=user_message.message_id,
                message=user_message,
                success=False,
                error="Failed to add fallback agent message",
                status_code=500,
            )

        await record_and_maybe_broadcast_run_event(
            request.room_id,
            SSEProcessingStatus.COMPLETED,
            user_message.message_id,
            client_request_id=user_message.client_request_id,
            sse=self.sse_manager,
        )
        await self.sse_manager.send_processing_status(
            request.room_id,
            SSEProcessingStatus.COMPLETED,
            user_message.message_id,
            client_request_id=user_message.client_request_id,
        )

        return RoomCenterUserMessageResponse(
            message_id=user_message.message_id,
            message=user_message,
            success=True,
            error=None,
            status_code=200,
        )

    async def create_and_parse_user_message(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse:
        """Send user message and handle @agent parsing with context grouping"""

        if request.room_id is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        message = request.message
        if message is None:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Message is required",
                status_code=400,
            )

        size_err = self._check_message_text_length(message)
        if size_err:
            return size_err

        # Resolve attachments before persistence
        att_err = await self._resolve_and_apply_attachments(request, message)
        if att_err is not None:
            return att_err

        # Save user message
        message.client_request_id = (
            request.client_request_id
            if isinstance(getattr(request, "client_request_id", None), str)
            else None
        )
        add_message_success = await self.database_service.add_room_user_message(message)
        if not add_message_success:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Failed to add message",
                status_code=500,
            )

        # send processing status via sse to client
        logger.debug(
            f"RoomServices: Sending processing status to room {room_id} for message {message.message_id}"
        )
        await sse_manager.send_processing_status(
            room_id,
            SSEProcessingStatus.PROCESSING,
            message.message_id,
            client_request_id=message.client_request_id,
        )

        # Initialize or update room memory
        room_memory_initialize_or_update_response = (
            await self.room_memory_service.initialize_or_update_room_memory(
                RoomCenterMemoryRequest(
                    room_id=room_id,
                    message_id=message.message_id,
                    memory_content=message.message_content.message_text,
                    attachments=message.message_content.attachments,
                )
            )
        )
        if not room_memory_initialize_or_update_response.success:
            return RoomCenterUserMessageResponse(
                message_id=message.message_id,
                message=message,
                success=False,
                error="Failed to initialize or update room memory",
                status_code=500,
            )

        # Get room information
        room = await self.database_service.get_room_by_room_id(room_id)
        if not room:
            return RoomCenterUserMessageResponse(
                message_id=message.message_id,
                message=message,
                success=True,
                error="Room not found, but message saved",
                status_code=200,
            )

        # Parse @agent mentions and deterministically fan out messages to each mention
        mentions = self.parse_agent_mentions(
            message.message_content.message_text, room.room_agent_set
        )
        mention_response = await self.parse_user_message_with_mentions(
            room, message, mentions
        )
        return mention_response

    async def parse_user_message_with_mentions(
        self,
        room: Room,
        message: RoomUserMessage,
        mentions: list[dict],
    ) -> RoomCenterUserMessageResponse:
        """
        Deterministically handle messages that contain @mentions by creating one task per mention.

        This bypasses LLM routing/assignment to guarantee every mentioned agent receives a task.
        """
        room_id = room.room_id

        # Group mentions by context and detect consecutive patterns
        context_groups = self.group_mentions_by_context(
            message.message_content.message_text, mentions
        )

        created_agent_messages = []
        for context_text, group_info in context_groups.items():
            mentions_in_context = group_info["mentions"]
            is_consecutive = group_info["is_consecutive"]

            try:
                # Create shared message content for this context
                shared_content = self.create_shared_message_content(
                    context_text, mentions_in_context
                )

                # Create tasks for all agents in this context
                tasks_group = await self.create_task_for_agents_group(
                    message, mentions_in_context, shared_content
                )

                if is_consecutive:
                    # Consecutive mentions: chain dependencies in order
                    previous_message_id = (
                        message.message_id
                    )  # Start with user message ID

                    for i, task_info in enumerate(tasks_group):
                        agent_message = RoomAgentMessage(
                            room_id=room_id,
                            message_id=str(uuid4()),
                            related_message_id=previous_message_id,
                            agent_id=task_info["agent_id"],
                            user_id=message.user_id,
                            client_request_id=message.client_request_id,
                            message_content=MessageContent(
                                message_task=task_info["task"]
                            ),
                            message_created_at=utcnow(),
                            task_content=shared_content,
                        )

                        agent_message_success = (
                            await self.database_service.add_room_agent_message(
                                agent_message
                            )
                        )
                        if agent_message_success:
                            created_agent_messages.append(agent_message)
                            previous_message_id = agent_message.message_id
                else:
                    # Non-consecutive: relate all to the user message
                    for task_info in tasks_group:
                        agent_message = RoomAgentMessage(
                            room_id=room_id,
                            message_id=str(uuid4()),
                            related_message_id=message.message_id,
                            agent_id=task_info["agent_id"],
                            user_id=message.user_id,
                            client_request_id=message.client_request_id,
                            message_content=MessageContent(
                                message_task=task_info["task"]
                            ),
                            message_created_at=utcnow(),
                            task_content=shared_content,
                        )

                        agent_message_success = (
                            await self.database_service.add_room_agent_message(
                                agent_message
                            )
                        )
                        if agent_message_success:
                            created_agent_messages.append(agent_message)

            except Exception as e:
                print(
                    f"Error creating agent messages for context '{context_text}': {e}"
                )

        return RoomCenterUserMessageResponse(
            message_id=message.message_id,
            dispatch_root_message_id=message.message_id,
            message=message,
            success=True,
            error=None,
            status_code=200,
        )

    async def _build_room_awareness(
        self,
        room_id: str,
        current_agent_id: str,
        task_description: str | None = None,
        agent_profiles: list[tuple[str, str, str]] | None = None,
    ) -> str | None:
        """
        Build room awareness context for an agent.

        This gives the agent awareness of other agents in the room and their roles,
        enabling better collaboration in multi-agent scenarios.

        Per design doc section 7.4 and 15: This should only be called for Supervisor-
        orchestrated multi-agent tasks. Direct chat (single agent working alone) should
        NOT receive room awareness to avoid misleading the agent about teammates.

        Args:
            room_id: The room ID
            current_agent_id: The ID of the agent receiving the context
            task_description: Specific task description for this agent. If None,
                              this indicates a direct-chat scenario and awareness
                              will be skipped.
            agent_profiles: Optional pre-built list of (agent_id, name, description)
                            tuples to avoid redundant DB lookups. If not provided,
                            will fetch from database.

        Returns:
            Room awareness context string, or None if not applicable
        """
        # Skip for direct chat — only 1 agent is working, awareness is misleading.
        # task_description=None is set precisely for direct-chat scenarios in both
        # legacy and Supervisor paths.
        if task_description is None:
            return None

        try:
            # If agent_profiles provided with descriptions, use them directly (avoids DB calls)
            if agent_profiles is not None:
                # Check if any peer agent has a description - if all are empty,
                # fall through to DB path for richer output
                has_descriptions = any(
                    description
                    for agent_id, name, description in agent_profiles
                    if agent_id != current_agent_id
                )

                if has_descriptions:
                    other_agents: list[str] = []
                    for agent_id, name, description in agent_profiles:
                        if agent_id != current_agent_id:
                            if description:
                                other_agents.append(f"- {name}: {description}")
                            else:
                                other_agents.append(f"- {name}")

                    if not other_agents:
                        return None

                    parts = ["[Room Context]"]
                    parts.append("You are working in a team with these other agents:")
                    parts.extend(other_agents)
                    parts.append(f"\nYour specific role in this task: {task_description}")
                    return "\n".join(parts)

                # Fall through to DB path if no descriptions available

            # Fallback: fetch from database (for backward compatibility)
            room = await self.database_service.get_room_by_room_id(room_id)
            if not room or not room.room_agent_set:
                return None

            # Only inject room awareness for Supervisor-enabled rooms.
            # Legacy multi-agent rooms opted out of this feature.
            room_extend_info = room.extend_info or {}
            if not room_extend_info.get("use_supervisor", False):
                return None

            # Skip room awareness for single-agent rooms
            if len(room.room_agent_set) <= 1:
                return None

            # Build list of other agents in the room
            other_agents: list[str] = []
            for agent_id, agent_name in room.room_agent_set.items():
                if agent_id != current_agent_id:
                    # Try to get agent description for richer context
                    agent = await self.database_service.get_agent_by_agent_id(agent_id)
                    if agent and agent.agent_card and agent.agent_card.description:
                        other_agents.append(
                            f"- {agent_name}: {agent.agent_card.description}"
                        )
                    else:
                        other_agents.append(f"- {agent_name}")

            if not other_agents:
                return None

            # Build the room awareness context
            parts = ["[Room Context]"]
            parts.append("You are working in a team with these other agents:")
            parts.extend(other_agents)
            parts.append(f"\nYour specific role in this task: {task_description}")

            return "\n".join(parts)

        except Exception as e:
            logger.warning(f"Failed to build room awareness: {e}")
            return None

    async def process_agent_message(
        self,
        request: RoomCenterAgentMessageRequest,
        room_memory: "RoomMemory | None" = None,
        quoted_text: str | None = None,
    ) -> RoomCenterAgentMessageResponse:
        """
        Process an agent message by building budget-aware context.

        Uses ContextAssemblyService for structured MemoryContent (§11.2),
        falls back to legacy string formatting for old-style memory.

        Args:
            request: The agent message request
            room_memory: Full RoomMemory object (preferred) or None
            quoted_text: Text the user highlighted and quoted from a previous message

        Returns:
            Response with the prepared A2A message including context
        """
        message = request.message
        if message is None:
            return RoomCenterAgentMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Agent Message is required",
                status_code=400,
            )

        agent_id = message.agent_id
        query_agent_url_response = await self.agent_service.get_agent_url_by_agent_id(
            AgentCenterRequest(agent_id=agent_id)
        )
        if query_agent_url_response.agent_url is None:
            return RoomCenterAgentMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="Agent url is not found",
                status_code=400,
            )

        agent_msg = request.message
        if (
            agent_msg.message_content
            and agent_msg.message_content.message_task
            and agent_msg.message_content.message_task.history
        ):
            agent_message = agent_msg.message_content.message_task.history[0]
        else:
            return RoomCenterAgentMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="No task content found",
                status_code=400,
            )

        # Get agent info for context personalization
        agent = await self.database_service.get_agent_by_agent_id(agent_id)
        agent_name = agent.agent_card.name if agent else None

        # Build room awareness context (other agents in the team)
        # Only for Supervisor-orchestrated multi-agent tasks (task_content != None)
        # Extract pre-built agent_profiles from extend_info to avoid redundant DB lookups
        agent_profiles = None
        if message.extend_info and isinstance(message.extend_info, dict):
            agent_profiles = message.extend_info.get("agent_profiles")

        room_awareness = await self._build_room_awareness(
            room_id=message.room_id,
            current_agent_id=agent_id,
            task_description=message.task_content,
            agent_profiles=agent_profiles,
        )

        # Build context using ContextAssemblyService (§11.2) or legacy fallback
        try:
            if agent_message and agent_message.parts and len(agent_message.parts) > 0:
                original_text = agent_message.parts[0].root.text or ""

                room_memory_content = (
                    room_memory.memory_content if room_memory else None
                )

                if isinstance(room_memory_content, MemoryContent):
                    # Budget-aware context via ContextAssemblyService (§11.2)
                    from services.context_assembly_service import context_assembly_service

                    try:
                        result = context_assembly_service.build_agent_execution_context(
                            room_memory=room_memory,
                            current_task=original_text,
                            agent_name=agent_name,
                            room_awareness=room_awareness,
                            quoted_text=quoted_text,
                            include_system_instruction=True,
                        )
                        context = result.context
                    except Exception as e:
                        logger.warning(
                            "ContextAssemblyService failed for agent, falling back to "
                            "DEPRECATED build_context_for_agent (to be removed): %s", e
                        )
                        context = build_context_for_agent(
                            memory_content=room_memory_content,
                            current_task=original_text,
                            agent_name=agent_name,
                            include_system_instruction=True,
                            quoted_text=quoted_text,
                            room_awareness=room_awareness,
                        )
                elif (
                    isinstance(room_memory_content, str) and room_memory_content.strip()
                ):
                    # Legacy style: Use raw text as context
                    quoted_section = ""
                    if quoted_text:
                        quoted_section = (
                            f"[Quoted context]\n"
                            f'The user is referencing the following specific content:\n'
                            f'"{quoted_text}"\n\n'
                        )
                    room_awareness_section = ""
                    if room_awareness:
                        room_awareness_section = f"{room_awareness}\n\n"
                    context = (
                        f"[Context]\n{room_memory_content}\n\n"
                        f"{quoted_section}"
                        f"{room_awareness_section}"
                        f"[Current request]\nUser: {original_text}"
                    )
                    if agent_name:
                        context += (
                            f"\n\nYou are {agent_name}. "
                            "Please respond to the current request above."
                        )
                else:
                    # No context available
                    quoted_section = ""
                    if quoted_text:
                        quoted_section = (
                            f"[Quoted context]\n"
                            f'The user is referencing the following specific content:\n'
                            f'"{quoted_text}"\n\n'
                        )
                    room_awareness_section = ""
                    if room_awareness:
                        room_awareness_section = f"{room_awareness}\n\n"
                    context = f"{quoted_section}{room_awareness_section}[Current request]\nUser: {original_text}"
                    if agent_name:
                        context += (
                            f"\n\nYou are {agent_name}. "
                            "Please respond to the request above."
                        )

                agent_message.parts[0].root.text = context
        except Exception as e:
            # Log but continue with original message if context building fails
            logger.warning(f"Failed to build context for agent message: {e}")

        # Append file parts from user attachments if the agent supports them
        try:
            from database.mongodb import mongodb

            # Trace back through agent message chain to find the originating user message.
            # In chained mention flows, later agents have related_message_id pointing to
            # a previous agent message, not the user message directly.
            # Use a visited set for cycle detection instead of a fixed hop cap so
            # arbitrarily long chains still resolve correctly.
            user_msg = None
            trace_id = message.related_message_id
            visited: set[str] = set()
            while trace_id and trace_id not in visited:
                visited.add(trace_id)
                user_msg = await mongodb.get_room_user_message_by_message_id(trace_id)
                if user_msg:
                    break
                agent_msg = await mongodb.get_room_agent_message_by_message_id(
                    trace_id
                )
                trace_id = (
                    agent_msg.related_message_id if agent_msg else None
                )

            user_attachments = (
                user_msg.message_content.attachments
                if user_msg and user_msg.message_content
                else None
            )
            if user_attachments:
                agent_obj = await self.database_service.get_agent_by_agent_id(agent_id)
                agent_card_obj = agent_obj.agent_card if agent_obj else None
                if agent_card_obj:
                    file_parts = await self._build_message_parts(
                        "", user_attachments, agent_card_obj
                    )
                    for p in file_parts:
                        if not isinstance(p, TextPart):
                            agent_message.parts.append(Part(root=p))
        except Exception as e:
            logger.warning("Failed to append attachment parts to agent message: %s", e)

        # Return the prepared message without sending
        # RoomMessageCenter will handle the actual sending with streaming support
        return RoomCenterAgentMessageResponse(
            message_id=message.message_id,
            message=message,
            a2a_message=agent_message,  # Return the prepared A2A message
            success=True,
            error=None,
            status_code=200,
        )

    async def update_agent_message_by_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        if request.message_id is None:
            return RoomCenterAgentMessageResponse(
                message=None,
                success=False,
                error="Message id is required",
                status_code=400,
            )

        message_id = request.message_id
        message = request.message
        if message is None:
            return RoomCenterAgentMessageResponse(
                message=None,
                success=False,
                error="Message is required",
                status_code=400,
            )

        # Preserve existing task metadata if incoming message omits it.
        if (
            message.message_content
            and message.message_content.message_task
            and message.message_content.message_task.metadata is None
        ):
            existing_message = (
                await self.database_service.get_room_agent_message_by_message_id(
                    message_id
                )
            )
            if (
                existing_message
                and existing_message.message_content
                and existing_message.message_content.message_task
                and existing_message.message_content.message_task.metadata is not None
            ):
                message.message_content.message_task.metadata = (
                    existing_message.message_content.message_task.metadata
                )

        update_message_success = (
            await self.database_service.update_room_agent_message_by_message_id(
                message_id, message
            )
        )
        if update_message_success:
            return RoomCenterAgentMessageResponse(
                message=message, success=True, error=None, status_code=200
            )
        else:
            return RoomCenterAgentMessageResponse(
                message=None,
                success=False,
                error="Failed to update message",
                status_code=500,
            )

    async def inquiry_user_messages_by_room_id(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse:
        if request.room_id is None:
            return RoomCenterUserMessageResponse(
                message_list=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        messages = await self.database_service.get_room_user_messages_by_room_id(
            room_id
        )

        # Inject presigned URLs for attachments
        s3_keys = []
        for msg in messages:
            if msg.message_content and msg.message_content.attachments:
                for att in msg.message_content.attachments:
                    s3_keys.append(att.s3_key)

        if s3_keys:
            try:
                from services.s3_service import s3_service

                url_map = await s3_service.batch_presigned_urls(s3_keys)
                for msg in messages:
                    if msg.message_content and msg.message_content.attachments:
                        for att in msg.message_content.attachments:
                            att.file_url = url_map.get(att.s3_key, "")
            except Exception:
                logger.warning(
                    "Failed to generate presigned URLs for room %s", room_id
                )

        return RoomCenterUserMessageResponse(
            message_list=messages, success=True, error=None, status_code=200
        )

    async def _refresh_artifact_presigned_urls(
        self, messages: list[RoomAgentMessage],
    ) -> None:
        """Re-sign S3 presigned URLs embedded in agent artifact file parts.

        Scans every artifact part for ``metadata.s3_key`` written during the
        S3-upload step.  Collects them, batch-generates fresh presigned URLs,
        and patches each ``file.uri`` in-place so the frontend always receives
        a valid URL regardless of when the original was created.
        """
        from services.s3_service import s3_service

        key_refs: list[tuple[object, str]] = []
        key_filenames: dict[str, str] = {}

        for msg in messages:
            task = msg.message_content.message_task if msg.message_content else None
            if not task or not task.artifacts:
                continue
            for artifact in task.artifacts:
                if not artifact.parts:
                    continue
                for part in artifact.parts:
                    root = getattr(part, "root", part)
                    if getattr(root, "kind", None) != "file":
                        continue
                    meta = getattr(root, "metadata", None)
                    if not meta:
                        continue
                    s3_key = meta.get("s3_key") if isinstance(meta, dict) else None
                    if not s3_key:
                        continue
                    file_content = getattr(root, "file", None)
                    if file_content is None:
                        continue
                    key_refs.append((file_content, s3_key))
                    fname = getattr(file_content, "name", None)
                    if fname:
                        key_filenames[s3_key] = fname

        if not key_refs:
            return

        unique_keys = list({k for _, k in key_refs})
        try:
            url_map = await s3_service.batch_presigned_urls(
                unique_keys, filenames=key_filenames,
            )
        except Exception:
            logger.warning("Failed to refresh artifact presigned URLs")
            return

        for file_content, s3_key in key_refs:
            new_url = url_map.get(s3_key)
            if new_url:
                file_content.uri = new_url

    async def inquiry_agent_messages_by_room_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        if request.room_id is None:
            return RoomCenterAgentMessageResponse(
                message_list=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        room_id = request.room_id
        messages = await self.database_service.get_room_agent_messages_by_room_id(
            room_id
        )

        # Sync task status for non-terminal tasks
        # This handles cases where SSE updates were missed or task state changed in background
        # Also auto-fails stale tasks that have no recovery path (e.g., server restarted mid-task)

        STALE_TASK_THRESHOLD = timedelta(minutes=10)

        def _is_task_stale(msg: RoomAgentMessage) -> bool:
            """Check if a task's last update is older than the staleness threshold."""
            ts = msg.task_updated_at or msg.task_created_at
            if ts is None:
                return True  # No timestamp at all => treat as stale
            return (utcnow() - ensure_utc(ts)) > STALE_TASK_THRESHOLD

        def _mark_msg_as_failed(msg: RoomAgentMessage, error_text: str) -> None:
            """Set the task on a message to failed state in-place."""
            task = msg.message_content.message_task if msg.message_content else None
            if task:
                task.status = TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        message_id=uuid4().hex,
                        role=Role.agent,
                        parts=[TextPart(text=error_text)],
                    ),
                )
            msg.task_updated_at = utcnow()

        for msg in messages:
            if not (
                msg.message_content
                and msg.message_content.message_task
            ):
                continue

            current_state = msg.message_content.message_task.status.state
            if is_terminal_state(current_state):
                continue

            # --- Case 1: Task WITHOUT task tracking (streaming-only) ---
            # Only auto-fail non-tracked tasks in "working" state, which means
            # the streaming connection died mid-stream (e.g., server restart).
            #
            # Non-tracked tasks in "submitted" state are NOT touched here —
            # they are likely queued pipeline steps waiting for earlier steps
            # to complete.  Genuinely orphaned submitted tasks are cleaned up
            # by the background StaleTaskChecker instead, which avoids
            # killing active pipeline steps on every message fetch.
            if not msg.has_task_tracking:
                if current_state == TaskState.working and _is_task_stale(msg):
                    logger.info(
                        "Auto-failing stale non-tracked task for msg %s (state: %s)",
                        msg.message_id,
                        current_state,
                    )
                    _mark_msg_as_failed(
                        msg,
                        "Task did not complete — the connection was lost, "
                        "possibly due to a server restart.",
                    )
                    try:
                        await self.database_service.update_room_agent_message_by_message_id(
                            msg.message_id, msg
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to persist auto-fail for non-tracked message %s: %s",
                            msg.message_id,
                            e,
                        )
                continue

            # --- Case 2: Task WITH task tracking ---
            # Try syncing with the MetaTask record first.
            synced = False
            try:
                task_res = await self.task_service.query_meta_task_by_task_id(
                    TaskCenterRequest(task_id=msg.message_id)
                )

                if (
                    task_res.success
                    and task_res.meta_task
                    and task_res.meta_task.task
                ):
                    real_task = task_res.meta_task.task
                    real_state = real_task.status.state

                    # If status mismatch, update the message
                    if real_state != current_state:
                        logger.info(
                            "Syncing stale task status for msg %s: %s -> %s",
                            msg.message_id,
                            current_state,
                            real_state,
                        )
                        msg.message_content.message_task = real_task
                        msg.task_updated_at = utcnow()
                        await self.database_service.update_room_agent_message_by_message_id(
                            msg.message_id, msg
                        )
                        synced = True
            except Exception as e:
                logger.warning(
                    "Failed to sync task status for message %s: %s",
                    msg.message_id,
                    e,
                )

            # If MetaTask sync didn't resolve it and the task is stale, auto-fail.
            # This catches the case where both the message and MetaTask are stuck
            # at "working" (e.g., server restarted and the remote agent is also gone).
            if not synced and _is_task_stale(msg):
                logger.info(
                    "Auto-failing stale tracked task for msg %s "
                    "(state: %s, MetaTask sync found no update)",
                    msg.message_id,
                    current_state,
                )
                _mark_msg_as_failed(
                    msg,
                    "Task did not complete — no progress was received within "
                    "the expected timeframe. This may have been caused by "
                    "a server restart or agent failure.",
                )
                try:
                    await self.database_service.update_room_agent_message_by_message_id(
                        msg.message_id, msg
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to persist auto-fail for tracked message %s: %s",
                        msg.message_id,
                        e,
                    )

        await self._refresh_artifact_presigned_urls(messages)

        return RoomCenterAgentMessageResponse(
            message_list=messages, success=True, error=None, status_code=200
        )

    async def inquiry_agent_message_by_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        if request.message_id is None:
            return RoomCenterAgentMessageResponse(
                message=None,
                success=False,
                error="Message id is required",
                status_code=400,
            )

        message_id = request.message_id
        message = await self.database_service.get_room_agent_message_by_message_id(
            message_id
        )
        if message:
            await self._refresh_artifact_presigned_urls([message])
        return RoomCenterAgentMessageResponse(
            message=message, success=True, error=None, status_code=200
        )

    async def inquiry_user_message_by_message_id(
        self, request: RoomCenterUserMessageRequest
    ) -> RoomCenterUserMessageResponse:
        if request.message_id is None:
            return RoomCenterUserMessageResponse(
                message=None,
                success=False,
                error="Message id is required",
                status_code=400,
            )

        message_id = request.message_id
        message = await self.database_service.get_room_user_message_by_message_id(
            message_id
        )
        return RoomCenterUserMessageResponse(
            message=message, success=True, error=None, status_code=200
        )

    async def inquiry_agent_messages_by_related_message_id(
        self, request: RoomCenterAgentMessageRequest
    ) -> RoomCenterAgentMessageResponse:
        if request.related_message_id is None:
            return RoomCenterAgentMessageResponse(
                message_list=None,
                success=False,
                error="Related message id is required",
                status_code=400,
            )

        related_message_id = request.related_message_id
        messages = (
            await self.database_service.get_room_agent_messages_by_related_message_id(
                related_message_id
            )
        )
        return RoomCenterAgentMessageResponse(
            message_list=messages, success=True, error=None, status_code=200
        )

    async def inquiry_room_messages_by_room_id(
        self, request: RoomCenterRoomMessageRequest
    ) -> RoomCenterRoomMessageResponse:
        """
        Retrieve all messages in a room, including user messages and agent messages.
        For user messages: return message_text from message_content
        For agent messages: return text part from the latest message with role "agent" in task.history
        Sort by creation time and return
        """
        if request.room_id is None:
            return RoomCenterRoomMessageResponse(
                message_list=None,
                success=False,
                error="Room id is required",
                status_code=400,
            )

        try:
            room_id = request.room_id

            # Get user messages
            user_message_request = RoomCenterUserMessageRequest(room_id=room_id)
            user_messages_response = await self.inquiry_user_messages_by_room_id(
                user_message_request
            )

            # Get agent messages
            agent_message_request = RoomCenterAgentMessageRequest(room_id=room_id)
            agent_messages_response = await self.inquiry_agent_messages_by_room_id(
                agent_message_request
            )

            combined_messages = []

            # Process user messages
            if user_messages_response.success and user_messages_response.message_list:
                for user_msg in user_messages_response.message_list:
                    room_message = RoomMessage(
                        room_id=user_msg.room_id,
                        message_id=user_msg.message_id,
                        client_request_id=user_msg.client_request_id,
                        message_type="user",
                        message_content=user_msg.message_content,
                        message_created_at=user_msg.message_created_at,
                        user_id=user_msg.user_id,
                    )
                    combined_messages.append(room_message)

            # Process agent messages
            if agent_messages_response.success and agent_messages_response.message_list:
                for agent_msg in agent_messages_response.message_list:
                    # Extract content from task - try history first, then artifacts
                    agent_content = ""
                    task = (
                        agent_msg.message_content.message_task
                        if agent_msg.message_content
                        else None
                    )

                    if task:
                        # First, try artifacts — these represent the final
                        # agent output and take priority over history which
                        # may contain intermediate status messages.
                        if task.artifacts:
                            text_parts = []
                            for artifact in task.artifacts:
                                if not artifact.parts:
                                    continue
                                for part in artifact.parts:
                                    # Handle different part type structures
                                    text = None
                                    if hasattr(part, "text") and part.text:
                                        text = part.text
                                    elif hasattr(part, "root"):
                                        # Discriminated union wrapper
                                        root = part.root
                                        if hasattr(root, "text") and root.text:
                                            text = root.text
                                    if text:
                                        text_parts.append(text)
                            agent_content = "".join(text_parts) if text_parts else ""

                        # Fallback to task.history (streaming agents that
                        # accumulate content in history rather than artifacts)
                        if not agent_content and task.history:
                            # Find the latest message with role "agent"
                            agent_messages = [
                                msg for msg in task.history if msg.role == Role.agent
                            ]

                            if agent_messages:
                                # Get the latest agent message
                                latest_agent_message = agent_messages[-1]

                                # Extract text parts from ALL message parts
                                text_parts = []
                                if (
                                    hasattr(latest_agent_message, "parts")
                                    and latest_agent_message.parts
                                ):
                                    for part in latest_agent_message.parts:
                                        if hasattr(part, "root") and hasattr(
                                            part.root, "text"
                                        ):
                                            text_parts.append(part.root.text)

                                # Combine all text parts
                                agent_content = (
                                    "".join(text_parts) if text_parts else ""
                                )

                    # Fallback to existing message_text if task extraction yielded nothing
                    # This preserves content that was stored directly (e.g., from webhook handler)
                    if not agent_content and agent_msg.message_content:
                        agent_content = agent_msg.message_content.message_text or ""

                    room_message = RoomMessage(
                        room_id=agent_msg.room_id,
                        message_id=agent_msg.message_id,
                        client_request_id=agent_msg.client_request_id,
                        message_type="agent",
                        message_content=MessageContent(
                            message_text=agent_content,
                            message_task=(
                                agent_msg.message_content.message_task
                                if agent_msg.message_content
                                else None
                            ),
                        ),
                        message_created_at=agent_msg.message_created_at,
                        agent_id=agent_msg.agent_id,
                        related_message_id=agent_msg.related_message_id,
                        step_number=agent_msg.step_number,
                        total_steps=agent_msg.total_steps,
                        task_updated_at=agent_msg.task_updated_at,
                        task_content=agent_msg.task_content,
                    )
                    combined_messages.append(room_message)

            # Sort by creation time, then by step_number for task messages, then by message_id for stability
            # This ensures consistent ordering when multiple messages have the same timestamp
            combined_messages.sort(
                key=lambda x: (
                    x.message_created_at,
                    x.step_number if x.step_number is not None else float("inf"),
                    x.message_id,
                )
            )

            return RoomCenterRoomMessageResponse(
                room_id=room_id,
                message_list=combined_messages,
                success=True,
                error=None,
                status_code=200,
            )

        except Exception as e:
            return RoomCenterRoomMessageResponse(
                room_id=request.room_id,
                message_list=None,
                success=False,
                error=str(e),
                status_code=500,
            )

    async def handle_a2a_response_for_room(
        self,
        room_agent_message: RoomAgentMessage,
        message_data: None
        | Task
        | Message
        | TaskStatusUpdateEvent
        | TaskArtifactUpdateEvent,
    ) -> bool:
        # Add null check for process_response
        if message_data is None:
            logger.error(
                "RoomMessageCenter: process_a2a_response returned None for agent message "
            )
            return False

        if message_data.kind == "task":
            room_agent_message.message_content.message_task = message_data
            update_response = await self.update_agent_message_by_message_id(
                RoomCenterAgentMessageRequest(
                    message_id=room_agent_message.message_id,
                    message=room_agent_message,
                )
            )
            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update agent message with task"
                )
                return False
            return True

        elif message_data.kind == "message":
            if (
                room_agent_message.message_content
                and room_agent_message.message_content.message_task
            ):
                if room_agent_message.message_content.message_task.history is None:
                    room_agent_message.message_content.message_task.history = []
                room_agent_message.message_content.message_task.history.append(
                    message_data
                )

            update_response = await self.update_agent_message_by_message_id(
                RoomCenterAgentMessageRequest(
                    message_id=room_agent_message.message_id,
                    message=room_agent_message,
                )
            )

            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update agent message with message: %s",
                    update_response.error,
                )
                return False
            return True

        elif message_data.kind == "status-update":
            # Handle status update responses - update task status and potentially add message
            if hasattr(message_data, "status") and hasattr(
                message_data.status, "state"
            ):
                if (
                    room_agent_message.message_content
                    and room_agent_message.message_content.message_task
                    and room_agent_message.message_content.message_task.status is None
                ):
                    room_agent_message.message_content.message_task.status = TaskStatus(
                        state=TaskState.submitted
                    )
                if (
                    room_agent_message.message_content
                    and room_agent_message.message_content.message_task
                ):
                    room_agent_message.message_content.message_task.status.state = (
                        message_data.status.state
                    )

                # If there's a message in the status update, add it to history
                if (
                    hasattr(message_data.status, "message")
                    and message_data.status.message
                    and room_agent_message.message_content
                    and room_agent_message.message_content.message_task
                ):
                    if room_agent_message.message_content.message_task.history is None:
                        room_agent_message.message_content.message_task.history = []
                    room_agent_message.message_content.message_task.history.append(
                        message_data.status.message
                    )

            update_response = await self.update_agent_message_by_message_id(
                RoomCenterAgentMessageRequest(
                    message_id=room_agent_message.message_id,
                    message=room_agent_message,
                )
            )

            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update agent message with status update: %s",
                    update_response.error,
                )
                return False
            return True

        elif message_data.kind == "artifact-update":
            # Handle artifact update responses - add artifacts to task
            if (
                hasattr(message_data, "artifact")
                and room_agent_message.message_content
                and room_agent_message.message_content.message_task
            ):
                if room_agent_message.message_content.message_task.artifacts is None:
                    room_agent_message.message_content.message_task.artifacts = []
                room_agent_message.message_content.message_task.artifacts.append(
                    message_data.artifact
                )

            update_response = await self.update_agent_message_by_message_id(
                RoomCenterAgentMessageRequest(
                    message_id=room_agent_message.message_id,
                    message=room_agent_message,
                )
            )

            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update agent message with artifact update: %s",
                    update_response.error,
                )
                return False
            return True


# Singleton export
room_services = RoomServices()
