from services.room_services import RoomServices
from models.request import RoomCenterRoomSettingRequest, RoomCenterUserMessageRequest, RoomCenterAgentMessageRequest, RoomCenterRoomMessageRequest  
from models.response import RoomCenterRoomSettingResponse, RoomCenterUserMessageResponse, RoomCenterAgentMessageResponse, RoomCenterRoomMessageResponse

class RoomCenter:
    def __init__(self):
        self.room_services = RoomServices()

    def create_new_room(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        return self.room_services.create_new_room(request)
    
    def inquiry_room_setting(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        return self.room_services.inquiry_room_setting(request)
    
    def delete_room_by_room_id(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        return self.room_services.delete_room_by_room_id(request)
    
    def inquiry_rooms_by_room_owner_id(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        return self.room_services.inquiry_rooms_by_room_owner_id(request)
    
    def update_room_agent_set(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        return self.room_services.update_room_agent_set(request)

    def update_room_name(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        return self.room_services.update_room_name(request)
    
    def update_room_extend_info(self, request: RoomCenterRoomSettingRequest) -> RoomCenterRoomSettingResponse:
        return self.room_services.update_room_extend_info(request)

    def create_and_parse_user_message(self, request: RoomCenterUserMessageRequest) -> RoomCenterUserMessageResponse:
        return self.room_services.create_and_parse_user_message(request)

    def create_and_parse_user_message_with_debate(self, request: RoomCenterUserMessageRequest) -> RoomCenterUserMessageResponse:
        return self.room_services.create_and_parse_user_message_with_debate(request)
    
    def inquiry_room_messages_by_room_id(self, request: RoomCenterRoomMessageRequest) -> RoomCenterRoomMessageResponse:
        return self.room_services.inquiry_room_messages_by_room_id(request)
    
    def inquiry_agent_messages_by_related_message_id(self, request: RoomCenterAgentMessageRequest) -> RoomCenterAgentMessageResponse:
        return self.room_services.inquiry_agent_messages_by_related_message_id(request)
    
    def send_message_to_room(self, request: RoomCenterUserMessageRequest) -> RoomCenterUserMessageResponse:
        return self.room_services.send_message_to_room(request)