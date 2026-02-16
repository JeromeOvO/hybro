from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from common.auth import ClerkUser, get_current_user
from models.request import (
    OrchestrationRequest,
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
)
from modules.RoomMessageCenter import room_message_center
from modules.RoomCenter import RoomCenter
from services.database_service import db_service

router = APIRouter()
room_center = RoomCenter()  # Singleton instance


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
    room_name = request_data.get("room_name")
    # Take room_owner_id from authenticated user
    room_owner_id = user.user_id
    room_owner_name = request_data.get("room_owner_name")
    room_agent_set = request_data.get("room_agent_set")
    applied_from_group = request_data.get(
        "applied_from_group"
    )  # Group ID if agents from a group
    extend_info = request_data.get("extend_info")
    room_center_request = RoomCenterRoomSettingRequest(
        room_name=room_name,
        room_owner_id=room_owner_id,
        room_owner_name=room_owner_name,
        room_agent_set=room_agent_set,
        applied_from_group=applied_from_group,
        extend_info=extend_info,
        requesting_user_id=user.user_id,  # Pass user for agent visibility validation
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

    room_center_request = RoomCenterRoomSettingRequest(room_id=room_id)
    room_center_response = await room_center.inquiry_room_setting(room_center_request)
    return room_center_response


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
    room_agent_set = request_data.get("room_agent_set")

    # Verify user owns the room
    await verify_room_ownership(room_id, user)

    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id,
        room_agent_set=room_agent_set,
        requesting_user_id=user.user_id,  # Pass user for agent visibility validation
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


@router.post("/roomCenter/createAndParseUserMessage")
async def create_and_parse_user_message(
    request: Request,
    user: ClerkUser = Depends(get_current_user),
):
    """Create and parse user message - PROTECTED (requires room ownership)"""
    request_data = await request.json()
    room_id = request_data.get("room_id")
    message = request_data.get("message")

    # Verify user owns the room
    await verify_room_ownership(room_id, user)

    room_center_request = RoomCenterUserMessageRequest(room_id=room_id, message=message)
    room_center_response = await room_center.create_and_parse_user_message(
        room_center_request
    )
    return room_center_response


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
    target_group = request_data.get(
        "target_group", "room_team"
    )  # Group ID: "all_agents", "room_team", or custom group ID

    # Verify user owns the room
    await verify_room_ownership(room_id, user)

    room_center_request = RoomCenterUserMessageRequest(room_id=room_id, message=message)
    room_center_response = await room_center.send_message_to_room(
        room_center_request, target_group
    )

    # Extract related_message_id from the user message (set when quoting)
    related_message_id = ""
    if isinstance(message, dict):
        related_message_id = message.get("related_message_id") or ""

    # Auto-trigger processing as background task if message was created successfully
    # This prevents orphaned messages when user refreshes before frontend calls processRoomUserMessage
    if room_center_response.success and room_center_response.message_id:
        orchestration_request = OrchestrationRequest(
            room_id=room_id,
            room_user_message_id=room_center_response.message_id,
            room_related_message_id=related_message_id,
            user_id=user.user_id,
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
