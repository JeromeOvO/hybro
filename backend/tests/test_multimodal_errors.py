from unittest.mock import AsyncMock, MagicMock

from conversation.attachments import resolve_room_attachments
from models.response import RoomCenterUserMessageResponse


async def test_resolve_attachments_missing_file():
    reader = MagicMock()
    reader.get_for_room_file = AsyncMock(return_value=None)

    result = await resolve_room_attachments(
        file_ids=["nonexistent"], conversation_id="room1", attachment_reader=reader
    )

    assert isinstance(result, RoomCenterUserMessageResponse)
    assert result.status_code == 404
    assert not result.success
