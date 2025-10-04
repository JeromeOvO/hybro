from fastapi import APIRouter, HTTPException, Request
from models.request import (
    RoomCenterRoomMessageRequest,
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
)
from modules.RoomCenter import RoomCenter

router = APIRouter()


@router.post("/roomCenter/createNewRoom")
async def create_new_room(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_name = request_data.get("room_name")
    room_owner_id = request_data.get("room_owner_id")
    room_owner_name = request_data.get("room_owner_name")
    room_agent_set = request_data.get("room_agent_set")
    extend_info = request_data.get("extend_info")
    room_center_request = RoomCenterRoomSettingRequest(
        room_name=room_name,
        room_owner_id=room_owner_id,
        room_owner_name=room_owner_name,
        room_agent_set=room_agent_set,
        extend_info=extend_info,
    )
    room_center_response = await room_center.create_new_room(room_center_request)
    return room_center_response


@router.post("/roomCenter/inquiryRoomSetting")
async def inquiry_room_setting(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_center_request = RoomCenterRoomSettingRequest(room_id=room_id)
    room_center_response = await room_center.inquiry_room_setting(room_center_request)
    return room_center_response


@router.post("/roomCenter/inquiryRoomsByRoomOwnerId")
async def inquiry_rooms_by_room_owner_id(request: Request):
    room_center = RoomCenter()
    request_data = await request.json()
    room_owner_id = request_data.get("room_owner_id")
    room_center_request = RoomCenterRoomSettingRequest(room_owner_id=room_owner_id)
    room_center_response = await room_center.inquiry_rooms_by_room_owner_id(
        room_center_request
    )
    return room_center_response


@router.post("/roomCenter/updateRoomAgentSet")
async def update_room_agent_set(request: Request):
    room_center = RoomCenter()
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
    room_center = RoomCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_name = request_data.get("room_name")
    room_center_request = RoomCenterRoomSettingRequest(
        room_id=room_id, room_name=room_name
    )
    room_center_response = await room_center.update_room_name(room_center_request)
    return room_center_response


@router.post("/roomCenter/createAndParseUserMessage")
async def create_and_parse_user_message(request: Request):
    room_center = RoomCenter()
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
    room_center = RoomCenter()
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
    room_center = RoomCenter()
    request_data = await request.json()
    room_id = request_data.get("room_id")
    room_center_request = RoomCenterRoomMessageRequest(room_id=room_id)
    room_center_response = await room_center.inquiry_room_messages_by_room_id(
        room_center_request
    )
    return room_center_response
