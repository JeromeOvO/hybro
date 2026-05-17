from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from common.auth import ClerkUser, get_current_user
from models.file_upload import MAX_ATTACHMENT_REFS_PER_REQUEST
from models.request import (
    OrchestrationRequest,
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
)
from models.response import RoomCenterUserMessageResponse
from modules.RoomMessageCenter import room_message_center
from modules.RoomCenter import RoomCenter
from services.database_service import db_service

router = APIRouter()
room_center = RoomCenter()  # Singleton instance


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


async def verify_room_ownership(room_id: str, user: ClerkUser) -> None:
    """
    Verify that the current user owns the specified room.
    Raises HTTPException if the room doesn't exist or user is not the owner.
    """
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")

    room = await db_service.get_room_by_room_id(room_id)

    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if room.room_owner_id != user.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have permission to access this room"
        )


@router.post("/roomCenter/createNewRoom")
async def create_new_room(
    request: Request, user: ClerkUser = Depends(get_current_user)
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
    room_center_response = await room_center.create_new_room(room_center_request)
    return room_center_response


@router.post("/roomCenter/inquiryRoomSetting")
async def inquiry_room_setting(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Get room settings - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")

    # Verify user owns the room
    await verify_room_ownership(room_id, user)

    room_center_request = RoomCenterRoomSettingRequest(room_id=room_id, requesting_user_id=user.user_id)
    room_center_response = await room_center.inquiry_room_setting(room_center_request)
    return room_center_response


@router.post("/roomCenter/inquiryActiveRuns")
async def inquiry_active_runs(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """List non-terminal orchestration runs for a room — same auth as inquiryRoomSetting."""
    request_data = await request.json()
    room_id = request_data.get("room_id")

    await verify_room_ownership(room_id, user)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, requesting_user_id=user.user_id
    )
    return await room_center.inquiry_active_runs(room_center_request)


@router.post("/roomCenter/inquiryRoomsByRoomOwnerId")
async def inquiry_rooms_by_room_owner_id(
    request: Request, user: ClerkUser = Depends(get_current_user)
):
    request_data = await request.json()
    room_owner_id = request_data.get("room_owner_id")

    # Verify user is requesting their own rooms
    if room_owner_id != user.user_id:
        raise HTTPException(
            status_code=403, detail="You do not have permission to access these rooms"
        )
    room_center_request = RoomCenterRoomSettingRequest(room_owner_id=room_owner_id)
    room_center_response = await room_center.inquiry_rooms_by_room_owner_id(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/updateRoomAgentSet")
async def update_room_agent_set(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Update room agent set - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")

    await verify_room_ownership(room_id, user)

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
    room_center_response = await room_center.update_room_agent_set(room_center_request)
    return room_center_response


@router.post("/roomCenter/updateRoomName")
async def update_room_name(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Update room name - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_name = request_data.get("room_name")

    # Verify user owns the room
    await verify_room_ownership(room_id, user)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, room_name=room_name
    )
    room_center_response = await room_center.update_room_name(room_center_request)
    return room_center_response


@router.post("/roomCenter/updateRoomExtendInfo")
async def update_room_extend_info(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Update room extended info - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")
    extend_info = request_data.get("extend_info")

    # Verify user owns the room
    await verify_room_ownership(room_id, user)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, extend_info=extend_info
    )
    room_center_response = await room_center.update_room_extend_info(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/createAndParseUserMessage", deprecated=True)
async def create_and_parse_user_message(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """
    **Deprecated.** Message creation and processing now go through sendMessage.
    This endpoint returns HTTP 410 Gone before parsing the request body.
    """
    return JSONResponse(
        status_code=410,
        content={
            "success": False,
            "error": "This endpoint is deprecated. Use /roomCenter/sendMessage.",
        },
    )


@router.post("/roomCenter/inquiryRoomMessagesByRoomId")
async def inquiry_room_messages(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Read room messages - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")

    # Verify user owns the room
    await verify_room_ownership(room_id, user)

    room_center_request = RoomCenterRoomMessageRequest(room_id=room_id)
    room_center_response = await room_center.inquiry_room_messages_by_room_id(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/sendMessage")
async def send_message(
    request: Request,
    background_tasks: BackgroundTasks,
    user: ClerkUser = Depends(get_current_user),
):
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
    if not isinstance(client_request_id, str) or not client_request_id.strip():
        return RoomCenterUserMessageResponse(
            message_id=None,
            message=None,
            success=False,
            error="client_request_id is required",
            status_code=400,
        )

    # Canonical field takes precedence; legacy target_group is fallback only.
    message_target_mode = request_data.get("message_target_mode")
    target_group_id = request_data.get("target_group_id")
    mentioned_agent_ids = request_data.get("mentioned_agent_ids")

    # Reject mixed payloads: mentions + target mode should not coexist.
    if mentioned_agent_ids and message_target_mode is not None:
        return RoomCenterUserMessageResponse(
            message_id=None, message=None, success=False,
            error="Cannot specify both mentioned_agent_ids and message_target_mode",
            status_code=400,
        )

    if message_target_mode is not None:
        if message_target_mode == "saved_group":
            if not target_group_id:
                return RoomCenterUserMessageResponse(
                    message_id=None, message=None, success=False,
                    error="target_group_id is required when message_target_mode is saved_group",
                    status_code=400,
                )
            target_group = target_group_id
        elif message_target_mode == "room_default":
            target_group = "room_team"
        else:
            target_group = message_target_mode
    else:
        target_group = request_data.get("target_group", "room_team")

    await verify_room_ownership(room_id, user)

    attachments, inline_file_ids, err = _extract_attachments(request_data, message)
    if err is not None:
        return err

    room_center_request = RoomCenterUserMessageRequest(
        room_id=room_id,
        user_id=user.user_id,
        message=message,
        attachments=attachments,
        inline_file_ids=inline_file_ids,
        client_request_id=client_request_id,
    )
    room_center_response = await room_center.send_message_to_room(
        room_center_request, target_group, mentioned_agent_ids
    )

    # Extract related_message_id from the user message (set when quoting)
    related_message_id = ""
    if isinstance(message, dict):
        related_message_id = message.get("related_message_id") or ""

    # Auto-trigger processing as background task if message was created successfully
    if room_center_response.success and room_center_response.message_id:
        orchestration_request = OrchestrationRequest(
            room_id=room_id,
            room_user_message_id=room_center_response.message_id,
            room_related_message_id=related_message_id,
            user_id=user.user_id,
            client_request_id=client_request_id,
        )
        background_tasks.add_task(
            room_message_center.process_room_user_message, orchestration_request
        )

    return room_center_response


@router.post("/roomCenter/suggestAgents")
async def suggest_agents(request: Request):
    """
    Suggest agents for a message based on content analysis.
    Used for Auto mode to preview which agents will be selected.
    """
    from services.agent_selection_service import agent_selection_service

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
        suggestion_result = await agent_selection_service.suggest_agents(
            message_text, top_k
        )
        return {"success": True, **suggestion_result, "status_code": 200}
    except Exception as e:
        return {"success": False, "error": str(e), "status_code": 500}
