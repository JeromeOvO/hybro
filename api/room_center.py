from fastapi import APIRouter, Request

from models.request import (
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
)
from modules.RoomCenter import RoomCenter

router = APIRouter()
room_center = RoomCenter()  # Singleton instance


@router.post("/roomCenter/createNewRoom")
async def create_new_room(request: Request):
    request_data = await request.json()
    room_name = request_data.get("room_name")
    room_owner_id = request_data.get("room_owner_id")
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
    )
    room_center_response = await room_center.create_new_room(room_center_request)
    return room_center_response


@router.post("/roomCenter/inquiryRoomSetting")
async def inquiry_room_setting(request: Request):
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_center_request = RoomCenterRoomSettingRequest(room_id=room_id)
    room_center_response = await room_center.inquiry_room_setting(room_center_request)
    return room_center_response


@router.post("/roomCenter/inquiryRoomsByRoomOwnerId")
async def inquiry_rooms_by_room_owner_id(request: Request):
    request_data = await request.json()
    room_owner_id = request_data.get("room_owner_id")
    room_center_request = RoomCenterRoomSettingRequest(room_owner_id=room_owner_id)
    room_center_response = await room_center.inquiry_rooms_by_room_owner_id(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/updateRoomAgentSet")
async def update_room_agent_set(request: Request):
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_agent_set = request_data.get("room_agent_set")
    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, room_agent_set=room_agent_set
    )
    room_center_response = await room_center.update_room_agent_set(room_center_request)
    return room_center_response


@router.post("/roomCenter/updateRoomName")
async def update_room_name(request: Request):
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_name = request_data.get("room_name")
    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, room_name=room_name
    )
    room_center_response = await room_center.update_room_name(room_center_request)
    return room_center_response


@router.post("/roomCenter/updateRoomExtendInfo")
async def update_room_extend_info(request: Request):
    request_data = await request.json()
    room_id = request_data.get("room_id")
    extend_info = request_data.get("extend_info")
    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, extend_info=extend_info
    )
    room_center_response = await room_center.update_room_extend_info(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/createAndParseUserMessage")
async def create_and_parse_user_message(request: Request):
    request_data = await request.json()
    room_id = request_data.get("room_id")
    message = request_data.get("message")
    room_center_request = RoomCenterUserMessageRequest(room_id=room_id, message=message)
    room_center_response = await room_center.create_and_parse_user_message(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/createAndParseUserMessageWithDebate")
async def create_and_parse_user_message_with_debate(request: Request):
    request_data = await request.json()
    room_id = request_data.get("room_id")
    message = request_data.get("message")
    room_center_request = RoomCenterUserMessageRequest(room_id=room_id, message=message)
    room_center_response = await room_center.create_and_parse_user_message_with_debate(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/inquiryRoomMessagesByRoomId")
async def inquiry_room_messages(request: Request):
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_center_request = RoomCenterRoomMessageRequest(room_id=room_id)
    room_center_response = await room_center.inquiry_room_messages_by_room_id(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/sendMessage")
async def send_message(request: Request):
    request_data = await request.json()
    room_id = request_data.get("room_id")
    message = request_data.get("message")
    target_group = request_data.get(
        "target_group", "room_team"
    )  # Group ID: "all_agents", "room_team", or custom group ID
    room_center_request = RoomCenterUserMessageRequest(room_id=room_id, message=message)
    room_center_response = await room_center.send_message_to_room(
        room_center_request, target_group
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
