from pydantic import root_model
from models.room import (
    Room,
    RoomUserMessage,
    RoomAgentMessage,
    RoomMemory,
    MessageContent,
    MemoryContent
)
from models.request import (
    RoomCenterRoomSettingRequest,
    RoomCenterUserMessageRequest,
    RoomCenterAgentMessageRequest,
    RoomCenterMemoryRequest
)
from models.response import (
    RoomCenterRoomSettingResponse,
    RoomCenterUserMessageResponse,
    RoomCenterAgentMessageResponse,
    RoomCenterMemoryResponse
)
from models.task import Task
from services.database_service import DatabaseService
from uuid import uuid4
from datetime import datetime

class RoomServices:
    def __init__(self):
        self.database_service = DatabaseService()

    async def create_new_room(self, room_create_request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:

        if room_create_request.room_name is None:
            return RoomCenterRoomSettingResponse(room_id=None, success=False, error="Room name is required", status_code=400)
        if room_create_request.room_owner_id is None:
            return RoomCenterRoomSettingResponse(room_id=None, success=False, error="Room owner id is required", status_code=400)
        if room_create_request.room_owner_name is None:
            return RoomCenterRoomSettingResponse(room_id=None, success=False, error="Room owner name is required", status_code=400)

        if room_create_request.room is not None:
            room = room_create_request.room
        else:
            room = Room(
                room_id = str(uuid4()),
                room_name=room_create_request.room_name,
                room_owner_id=room_create_request.room_owner_id,
                room_owner_name=room_create_request.room_owner_name,
                room_agent_set=room_create_request.room_agent_set or dict(),
                room_created_at=datetime.now(),
                extend_info=room_create_request.extend_info or None
            )

        success = await self.database_service.add_room(room)
        if success:
            return RoomCenterRoomSettingResponse(room_id=room.room_id, room=room, success=True, error=None, status_code=200)
        else:
            return RoomCenterRoomSettingResponse(room_id=None, room=None, success=False, error="Failed to create room", status_code=500)
        
    async def get_rooms_by_room_owner_id(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        if request.room_owner_id is None:
            return RoomCenterRoomSettingResponse(room_list=None, success=False, error="Room owner id is required", status_code=400)
        
        room_owner_id = request.room_owner_id
        rooms = await self.database_service.get_rooms_by_room_owner_id(room_owner_id)
        return RoomCenterRoomSettingResponse(room_list=rooms, success=True, error=None, status_code=200)
    
    async def get_room_setting_by_room_id(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(room_id=None, room=None, success=False, error="Room id is required", status_code=400)
        
        room_id = request.room_id
        room = await self.database_service.get_room_by_room_id(room_id)
        if room is None:
            return RoomCenterRoomSettingResponse(room_id=None, room=None, success=False, error="Room not found", status_code=404)
        else:
            return RoomCenterRoomSettingResponse(room_id=room.room_id, room=room, success=True, error=None, status_code=200)
    
    async def update_room_setting_by_room_id(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        if request.room_id is None:
            return RoomCenterRoomSettingResponse(room_id=None, room=None, success=False, error="Room id is required", status_code=400)
        
        room_id = request.room_id
        
        new_room = request.room
        if new_room is None:
            return RoomCenterRoomSettingResponse(room_id=None, room=None, success=False, error="Room is required", status_code=400)
        
        success = await self.database_service.update_room_by_room_id(room_id, new_room)
        if success:
            return RoomCenterRoomSettingResponse(room_id=room_id, room=new_room, success=True, error=None, status_code=200)
        else:
            return RoomCenterRoomSettingResponse(room_id=room_id, room=None, success=False, error="Failed to update room", status_code=500)
    
    