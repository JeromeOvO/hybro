from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from agent.protocols import AgentSuggestionService, serialize_agent_suggestion_result
from api_gateway.dependencies import (
    get_agent_selection_service,
    get_execution_engine,
    get_room_center,
    get_room_store,
)
from api_gateway.registry import mark_declared_owner as _mark_declared_owner
from common.auth import ClerkUser, get_current_user
from common.dto import ExecutionRequest, RunInfo
from common.protocols import ExecutionEngine, RoomRouteReader
from common.utils.logger import get_logger
from models.file_upload import MAX_ATTACHMENT_REFS_PER_REQUEST
from models.request import (
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
)
from models.response import (
    ActiveRunRef,
    RoomCenterActiveRunsResponse,
    RoomCenterUserMessageResponse,
)
from room.protocols import RoomCenterCompatibility

router = APIRouter()
logger = get_logger(__name__)


def _run_info_to_active_run_ref(run: RunInfo) -> ActiveRunRef:
    return ActiveRunRef(
        state=str(getattr(run.state, "value", run.state)),
        trigger_message_id=run.trigger_message_id,
        agent_id=run.agent_id,
        updated_at=run.updated_at,
    )


def _message_text_len(message: dict | None) -> int:
    if not isinstance(message, dict):
        return 0
    message_content = message.get("message_content")
    if isinstance(message_content, dict):
        text = message_content.get("message_text")
    else:
        text = message.get("message_text")
    return len(text) if isinstance(text, str) else 0


async def _active_run_refs_for_room(
    room_id: str,
    engine: ExecutionEngine,
) -> list[ActiveRunRef]:
    try:
        runs = await engine.get_runs_for_room(room_id)
    except Exception:
        logger.warning(
            "active-run lookup failed for room_id=%s", room_id, exc_info=True
        )
        return []
    return [_run_info_to_active_run_ref(run) for run in runs]


def _extract_attachments(request_data: dict, message: dict | None):
    """Extract attachment info from both top-level and inline sources.

    Returns (attachments_list_or_None, inline_file_ids_or_None, error_response_or_None).
    If error_response is not None, the caller should return it immediately.
    """
    attachments = request_data.get("attachments")

    msg_content = (message if isinstance(message, dict) else {}).get("message_content")
    msg_content = msg_content if isinstance(msg_content, dict) else {}
    raw_inline_attachments = msg_content.pop("attachments", None)

    top_level_count = len(attachments) if isinstance(attachments, list) else 0
    inline_count = (
        len(raw_inline_attachments) if isinstance(raw_inline_attachments, list) else 0
    )
    if top_level_count + inline_count > MAX_ATTACHMENT_REFS_PER_REQUEST:
        return (
            None,
            None,
            RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=(
                    f"Too many attachment references ({top_level_count + inline_count}); "
                    f"maximum {MAX_ATTACHMENT_REFS_PER_REQUEST} per request"
                ),
                status_code=400,
            ),
        )

    inline_file_ids: list[str] = []
    if raw_inline_attachments and isinstance(raw_inline_attachments, list):
        for item in raw_inline_attachments:
            fid = item.get("file_id") if isinstance(item, dict) else None
            if fid and isinstance(fid, str):
                inline_file_ids.append(fid)

    return attachments, inline_file_ids or None, None


async def verify_room_ownership(
    room_id: str,
    user: ClerkUser,
    store: RoomRouteReader,
) -> None:
    """
    Verify that the current user owns the specified room.
    Raises HTTPException if the room doesn't exist or user is not the owner.
    """
    await _get_verified_room(room_id, user, store)


async def _get_verified_room(
    room_id: str,
    user: ClerkUser,
    store: RoomRouteReader,
):
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")

    room = await store.get_room_by_room_id(room_id)

    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if room.room_owner_id != user.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have permission to access this room"
        )

    return room


@router.post("/roomCenter/createNewRoom")
async def create_new_room(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    center: RoomCenterCompatibility = Depends(get_room_center),
):
    request_data = await request.json()
    room_center_request = RoomCenterRoomSettingRequest(
        room_name=request_data.get("room_name"),
        room_owner_id=user.user_id,
        room_owner_name=request_data.get("room_owner_name"),
        extend_info=request_data.get("extend_info"),
        requesting_user_id=user.user_id,
        # Legacy fields (accepted during rollout)
        room_agent_set=request_data.get("room_agent_set"),
        applied_from_group=request_data.get("applied_from_group"),
        # Canonical membership write input
        membership_seed_input=request_data.get("membership_seed_input"),
        room_agent_ids=request_data.get("room_agent_ids"),
        seed_group_id=request_data.get("seed_group_id"),
        seed_all_current_agents=request_data.get("seed_all_current_agents"),
    )
    room_center_response = await center.create_new_room(room_center_request)
    return room_center_response


@router.post("/roomCenter/inquiryRoomSetting")
async def inquiry_room_setting(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    store: RoomRouteReader = Depends(get_room_store),
    engine: ExecutionEngine = Depends(get_execution_engine),
    center: RoomCenterCompatibility = Depends(get_room_center),
):
    """Get room settings - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")

    # Verify user owns the room
    await verify_room_ownership(room_id, user, store)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, requesting_user_id=user.user_id
    )
    room_center_response = await center.inquiry_room_setting(room_center_request)
    if room_center_response.success and room_id:
        room_center_response.active_runs = await _active_run_refs_for_room(
            room_id,
            engine,
        )
    return room_center_response


@router.post("/roomCenter/inquiryActiveRuns")
async def inquiry_active_runs(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    store: RoomRouteReader = Depends(get_room_store),
    engine: ExecutionEngine = Depends(get_execution_engine),
    center: RoomCenterCompatibility = Depends(get_room_center),
):
    """List non-terminal orchestration runs for a room — same auth as inquiryRoomSetting."""
    request_data = await request.json()
    room_id = request_data.get("room_id")
    trigger_message_id = request_data.get("trigger_message_id")

    await verify_room_ownership(room_id, user, store)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id,
        requesting_user_id=user.user_id,
        trigger_message_id=trigger_message_id,
    )
    active_runs = await _active_run_refs_for_room(room_id, engine)

    turn_completion_kind = None
    trigger_is_active = any(
        run.trigger_message_id == trigger_message_id for run in active_runs
    )
    if trigger_message_id and not trigger_is_active:
        try:
            room_side_response = await center.inquiry_active_runs(room_center_request)
        except Exception:
            logger.warning(
                "turn-completion lookup failed for room_id=%s trigger_message_id=%s",
                room_id,
                trigger_message_id,
                exc_info=True,
            )
        else:
            if room_side_response.success:
                turn_completion_kind = room_side_response.turn_completion_kind

    return RoomCenterActiveRunsResponse(
        room_id=room_id,
        active_runs=active_runs,
        turn_completion_kind=turn_completion_kind,
        success=True,
        error=None,
        status_code=200,
    )


@router.post("/roomCenter/inquiryRoomsByRoomOwnerId")
async def inquiry_rooms_by_room_owner_id(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    center: RoomCenterCompatibility = Depends(get_room_center),
):
    request_data = await request.json()
    room_owner_id = request_data.get("room_owner_id")

    # Verify user is requesting their own rooms
    if room_owner_id != user.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have permission to access these rooms"
        )
    room_center_request = RoomCenterRoomSettingRequest(room_owner_id=room_owner_id)
    room_center_response = await center.inquiry_rooms_by_room_owner_id(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/updateRoomAgentSet")
async def update_room_agent_set(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    store: RoomRouteReader = Depends(get_room_store),
    center: RoomCenterCompatibility = Depends(get_room_center),
):
    """Update room agent set - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")

    await verify_room_ownership(room_id, user, store)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id,
        requesting_user_id=user.user_id,
        # Legacy fields (accepted during rollout)
        room_agent_set=request_data.get("room_agent_set"),
        applied_from_group=request_data.get("applied_from_group"),
        # Canonical membership write input
        membership_seed_input=request_data.get("membership_seed_input"),
        room_agent_ids=request_data.get("room_agent_ids"),
        seed_group_id=request_data.get("seed_group_id"),
        seed_all_current_agents=request_data.get("seed_all_current_agents"),
    )
    room_center_response = await center.update_room_agent_set(room_center_request)
    return room_center_response


@router.post("/roomCenter/updateRoomName")
async def update_room_name(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    store: RoomRouteReader = Depends(get_room_store),
    center: RoomCenterCompatibility = Depends(get_room_center),
):
    """Update room name - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_name = request_data.get("room_name")

    # Verify user owns the room
    await verify_room_ownership(room_id, user, store)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, room_name=room_name
    )
    room_center_response = await center.update_room_name(room_center_request)
    return room_center_response


@router.post("/roomCenter/updateRoomExtendInfo")
async def update_room_extend_info(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    store: RoomRouteReader = Depends(get_room_store),
    center: RoomCenterCompatibility = Depends(get_room_center),
):
    """Update room extended info - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")
    extend_info = request_data.get("extend_info")

    # Verify user owns the room
    await verify_room_ownership(room_id, user, store)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, extend_info=extend_info
    )
    room_center_response = await center.update_room_extend_info(room_center_request)
    return room_center_response


@router.post("/roomCenter/inquiryRoomMessagesByRoomId")
async def inquiry_room_messages(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    store: RoomRouteReader = Depends(get_room_store),
    center: RoomCenterCompatibility = Depends(get_room_center),
):
    """Read room messages - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")

    # Verify user owns the room
    await verify_room_ownership(room_id, user, store)

    room_center_request = RoomCenterRoomMessageRequest(room_id=room_id)
    room_center_response = await center.inquiry_room_messages_by_room_id(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/sendMessage")
async def send_message(
    request: Request,
    background_tasks: BackgroundTasks,
    user: ClerkUser = Depends(get_current_user),
    store: RoomRouteReader = Depends(get_room_store),
    engine: ExecutionEngine = Depends(get_execution_engine),
) -> RoomCenterUserMessageResponse:
    """Send message to room - PROTECTED (requires room ownership)

    This endpoint:
    1. Creates the user message and generates agent messages
    2. Automatically queues background processing of agent messages

    The frontend no longer needs to call processRoomUserMessage separately.
    Processing happens atomically to prevent orphaned messages on page refresh.
    """
    request_data = await request.json()
    room_id = request_data.get("room_id")
    message = request_data.get("message")
    client_request_id = request_data.get("client_request_id")
    mode = request_data.get("mode", "direct")
    if mode is None:
        mode = "direct"
    if not isinstance(mode, str) or mode not in {"direct", "supervisor", "debate"}:
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="mode must be one of: direct, supervisor, debate",
            status_code=400,
        )

    orchestration_schema_version = request_data.get("orchestration_schema_version")
    if orchestration_schema_version is not None and (
        isinstance(orchestration_schema_version, bool)
        or not isinstance(orchestration_schema_version, int)
    ):
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="orchestration_schema_version must be an integer",
            status_code=400,
        )
    if not isinstance(client_request_id, str) or not client_request_id.strip():
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="client_request_id is required",
            status_code=400,
        )

    if "target_group" in request_data:
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="target_group is no longer supported; use message_target_mode and target_group_id",
            status_code=400,
        )

    room = await _get_verified_room(room_id, user, store)
    room_uses_supervisor = (
        isinstance(room.extend_info, dict)
        and room.extend_info.get("use_supervisor", False) is True
    )
    is_v2_supervisor_orchestration = (
        mode == "supervisor"
        and orchestration_schema_version == 2
        and room_uses_supervisor
    )

    message_target_mode = request_data.get("message_target_mode")
    target_group_id = request_data.get("target_group_id")
    mentioned_agent_ids = request_data.get("mentioned_agent_ids")
    selected_agent_ids = request_data.get("selected_agent_ids")
    candidate_scope_mode = request_data.get("candidate_scope_mode")
    candidate_scope_group_id = request_data.get("candidate_scope_group_id")
    has_target_group_id = "target_group_id" in request_data

    if selected_agent_ids is not None:
        if not isinstance(selected_agent_ids, list) or not all(
            isinstance(agent_id, str) and agent_id.strip()
            for agent_id in selected_agent_ids
        ):
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="selected_agent_ids must be a list of non-empty strings",
                status_code=400,
            )
        selected_agent_ids = [agent_id.strip() for agent_id in selected_agent_ids]

    if candidate_scope_mode is not None:
        if (
            not isinstance(candidate_scope_mode, str)
            or not candidate_scope_mode.strip()
        ):
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="candidate_scope_mode must be a non-empty string",
                status_code=400,
            )
        candidate_scope_mode = candidate_scope_mode.strip()

    if candidate_scope_group_id is not None:
        if (
            not isinstance(candidate_scope_group_id, str)
            or not candidate_scope_group_id.strip()
        ):
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="candidate_scope_group_id must be a non-empty string",
                status_code=400,
            )
        candidate_scope_group_id = candidate_scope_group_id.strip()

    if mentioned_agent_ids is not None:
        if not isinstance(mentioned_agent_ids, list) or not all(
            isinstance(agent_id, str) and agent_id.strip()
            for agent_id in mentioned_agent_ids
        ):
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="mentioned_agent_ids must be a list of non-empty strings",
                status_code=400,
            )
        mentioned_agent_ids = [agent_id.strip() for agent_id in mentioned_agent_ids]

    # Reject mixed payloads: mentions + target mode should not coexist.
    if (
        mentioned_agent_ids
        and message_target_mode is not None
        and not is_v2_supervisor_orchestration
    ):
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="Cannot specify both mentioned_agent_ids and message_target_mode",
            status_code=400,
        )

    if message_target_mode is not None:
        if message_target_mode == "saved_group":
            if not isinstance(target_group_id, str) or not target_group_id.strip():
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="target_group_id is required when message_target_mode is saved_group",
                    status_code=400,
                )
            target_group_id = target_group_id.strip()
            if target_group_id in {"room_team", "all_agents"}:
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="target_group_id cannot be a reserved target group",
                    status_code=400,
                )
            target_group = target_group_id
        elif message_target_mode == "room_default":
            if has_target_group_id:
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="target_group_id is only supported when message_target_mode is saved_group",
                    status_code=400,
                )
            target_group = "room_team"
        elif message_target_mode == "all_agents":
            if has_target_group_id:
                return RoomCenterUserMessageResponse(
                    message_id=None,
                    message=None,
                    success=False,
                    error="target_group_id is only supported when message_target_mode is saved_group",
                    status_code=400,
                )
            target_group = "all_agents"
        else:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error=(
                    "message_target_mode must be one of: room_default, "
                    "all_agents, saved_group"
                ),
                status_code=400,
            )
    elif is_v2_supervisor_orchestration and selected_agent_ids:
        if has_target_group_id:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="target_group_id is only supported when message_target_mode is saved_group",
                status_code=400,
            )
        target_group = "room_team"
    elif mentioned_agent_ids:
        if has_target_group_id:
            return RoomCenterUserMessageResponse(
                message_id=None,
                message=None,
                success=False,
                error="target_group_id is only supported when message_target_mode is saved_group",
                status_code=400,
            )
        target_group = "room_team"
    else:
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="message_target_mode is required when mentioned_agent_ids is not provided",
            status_code=400,
        )

    attachments, inline_file_ids, err = _extract_attachments(request_data, message)
    if err is not None:
        return err

    logger.info(
        "gateway_send_message_received",
        extra={
            "room_id": room_id,
            "client_request_id": client_request_id,
            "mode": mode,
            "orchestration_schema_version": orchestration_schema_version,
            "room_supervisor": room_uses_supervisor,
            "target_mode": message_target_mode,
            "target_group_id": target_group_id,
            "mentioned_count": len(mentioned_agent_ids or []),
            "selected_count": len(selected_agent_ids or []),
            "attachment_count": len(attachments or []),
            "inline_file_count": len(inline_file_ids or []),
            "message_length": _message_text_len(message),
        },
    )

    related_message_id = ""
    if isinstance(message, dict):
        related_message_id = message.get("related_message_id") or ""

    execution_request = ExecutionRequest(
        room_id=room_id,
        sender_id=user.user_id,
        sender_name=getattr(user, "username", None) or getattr(user, "email", None),
        message=jsonable_encoder(message),
        attachments=jsonable_encoder(attachments),
        inline_file_ids=inline_file_ids,
        client_request_id=client_request_id,
        target_group=target_group,
        target_group_id=target_group_id,
        message_target_mode=message_target_mode,
        mentioned_agent_ids=mentioned_agent_ids,
        selected_agent_ids=selected_agent_ids,
        candidate_scope_mode=candidate_scope_mode,
        candidate_scope_group_id=candidate_scope_group_id,
        orchestration_schema_version=orchestration_schema_version,
        parent_message_id=related_message_id or None,
        mode=mode,
    )
    ack = await engine.execute(execution_request)
    logger.info(
        "gateway_send_message_completed",
        extra={
            "room_id": room_id,
            "message_id": ack.message_id,
            "user_message_id": ack.message_id,
            "client_request_id": client_request_id,
            "outcome": "success" if ack.success else "error",
            "status": ack.status_code,
            "should_start_orchestration": ack.should_start_orchestration,
            "preflight_outcome": ack.preflight_outcome,
        },
    )

    # Auto-trigger processing as background task if message was created successfully
    if ack.success and ack.message_id and ack.should_start_orchestration:
        logger.info(
            "gateway_send_message_background_scheduled",
            extra={
                "room_id": room_id,
                "message_id": ack.message_id,
                "user_message_id": ack.message_id,
                "client_request_id": client_request_id,
            },
        )
        background_tasks.add_task(
            engine.start_orchestration,
            execution_request,
            ack,
        )

    return RoomCenterUserMessageResponse(**ack.model_dump())


@router.post("/roomCenter/suggestAgents")
async def suggest_agents(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    selection_service: AgentSuggestionService = Depends(get_agent_selection_service),
):
    """
    Suggest agents for a message based on content analysis.
    Used for Auto mode to preview which agents will be selected.
    """
    request_data = await request.json()
    message_text = request_data.get("message_text", "")
    top_k = request_data.get("top_k", 3)

    if not message_text:
        return {
            "success": False,
            "error": "message_text is required",
            "status_code": 400,
        }
    try:
        suggestion_result = await selection_service.suggest_agents(message_text, top_k)
        return {
            "success": True,
            **serialize_agent_suggestion_result(suggestion_result),
            "status_code": 200,
        }
    except Exception as e:
        return {"success": False, "error": str(e), "status_code": 500}


_mark_declared_owner(router, __name__)
