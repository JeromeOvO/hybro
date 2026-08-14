from unittest.mock import AsyncMock, MagicMock


class TestMissingFileId:
    async def test_resolve_attachments_missing_file(self):
        from models.response import RoomCenterUserMessageResponse
        from room.compat.runtime import RoomServices

        svc = RoomServices()
        svc.database_service = MagicMock()

        reader = MagicMock()
        reader.get_for_room_file = AsyncMock(return_value=None)
        svc.bind_attachment_metadata_reader(reader)

        result = await svc._resolve_attachments(["nonexistent"], "room1")

        assert isinstance(result, RoomCenterUserMessageResponse)
        assert result.status_code == 404
        assert not result.success
