import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from models.room import RoomUserMessage, MessageContent, UserAttachment
from models.request import RoomCenterUserMessageRequest
from services.room_services import RoomServices


@pytest.fixture
def room_services():
    svc = RoomServices()
    svc.database_service = MagicMock()
    svc.sse_manager = MagicMock()
    svc.room_memory_service = MagicMock()
    return svc


def _make_msg_with_attachment(s3_key="uploads/r/f1/photo.png"):
    att = UserAttachment(
        file_id="f1", s3_key=s3_key, mime_type="image/png",
        file_name="photo.png", size_bytes=100,
    )
    return RoomUserMessage(
        room_id="room1", message_id="msg1", message_type="user",
        message_content=MessageContent(message_text="hi", attachments=[att]),
    )


class TestMessageRetrieval:
    async def test_presigned_url_injected(self, room_services):
        msg = _make_msg_with_attachment()
        room_services.database_service.get_room_user_messages_by_room_id = AsyncMock(return_value=[msg])

        with patch("services.s3_service.s3_service") as mock_s3:
            mock_s3.batch_presigned_urls = AsyncMock(return_value={"uploads/r/f1/photo.png": "https://presigned"})
            result = await room_services.inquiry_user_messages_by_room_id(
                RoomCenterUserMessageRequest(room_id="room1")
            )

        assert result.success
        assert result.message_list[0].message_content.attachments[0].file_url == "https://presigned"

    async def test_no_attachments_no_s3_call(self, room_services):
        msg = RoomUserMessage(
            room_id="room1", message_id="msg1", message_type="user",
            message_content=MessageContent(message_text="hi"),
        )
        room_services.database_service.get_room_user_messages_by_room_id = AsyncMock(return_value=[msg])

        result = await room_services.inquiry_user_messages_by_room_id(
            RoomCenterUserMessageRequest(room_id="room1")
        )

        assert result.success
