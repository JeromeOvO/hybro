"""Tests for createAndParseUserMessage attachment coverage."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.file_upload import MAX_ATTACHMENT_REFS_PER_REQUEST
from models.response import RoomCenterUserMessageResponse
from api.room_center import _extract_attachments


class TestExtractAttachments:
    """Tests for the _extract_attachments helper in api/room_center.py."""

    def test_top_level_only(self):
        request_data = {
            "attachments": [{"file_id": "f1"}, {"file_id": "f2"}]
        }
        atts, inline, err = _extract_attachments(request_data, None)
        assert err is None
        assert len(atts) == 2
        assert inline is None

    def test_inline_only(self):
        message = {
            "message_content": {
                "attachments": [{"file_id": "f1"}]
            }
        }
        request_data = {}
        atts, inline, err = _extract_attachments(request_data, message)
        assert err is None
        assert atts is None
        assert inline == ["f1"]

    def test_both_sources(self):
        request_data = {
            "attachments": [{"file_id": "f1"}]
        }
        message = {
            "message_content": {
                "attachments": [{"file_id": "f2"}]
            }
        }
        atts, inline, err = _extract_attachments(request_data, message)
        assert err is None
        assert len(atts) == 1
        assert inline == ["f2"]

    def test_inline_stripped_from_message_content(self):
        message = {
            "message_content": {
                "message_text": "hello",
                "attachments": [{"file_id": "f1"}]
            }
        }
        request_data = {}
        _extract_attachments(request_data, message)
        assert "attachments" not in message["message_content"]

    def test_pre_dedup_guard_rejects_over_limit(self):
        attachments = [{"file_id": f"f{i}"} for i in range(30)]
        inline = [{"file_id": f"g{i}"} for i in range(25)]
        request_data = {"attachments": attachments}
        message = {"message_content": {"attachments": inline}}
        atts, inline_ids, err = _extract_attachments(request_data, message)
        assert err is not None
        assert not err.success

    def test_no_attachments(self):
        atts, inline, err = _extract_attachments({}, None)
        assert err is None
        assert atts is None
        assert inline is None

    def test_empty_attachments(self):
        atts, inline, err = _extract_attachments({"attachments": []}, None)
        assert err is None

    def test_invalid_inline_structure(self):
        message = {
            "message_content": {
                "attachments": [{"no_file_id": True}, "invalid"]
            }
        }
        atts, inline, err = _extract_attachments({}, message)
        assert err is None
        assert inline is None

    def test_message_not_dict(self):
        atts, inline, err = _extract_attachments({}, "not a dict")
        assert err is None
        assert atts is None
        assert inline is None

    def test_pre_dedup_guard_counts_both_sources(self):
        top = [{"file_id": f"f{i}"} for i in range(MAX_ATTACHMENT_REFS_PER_REQUEST)]
        message = {"message_content": {"attachments": [{"file_id": "extra"}]}}
        request_data = {"attachments": top}
        _, _, err = _extract_attachments(request_data, message)
        assert err is not None


class TestCreateAndParseOversizedMessage:
    """SDR 2.10: Oversized message rejection in create_and_parse_user_message."""

    @pytest.mark.asyncio
    async def test_rejects_oversized_message_text(self):
        """create_and_parse_user_message should reject messages > MAX_MESSAGE_LENGTH."""
        from unittest.mock import MagicMock
        from models.room import MAX_MESSAGE_LENGTH, MessageContent, RoomUserMessage
        from services.room_services import RoomServices

        rc = object.__new__(RoomServices)
        rc.database_service = MagicMock()
        rc.agent_service = MagicMock()
        rc.openai_service = MagicMock()
        rc.a2a_service = MagicMock()
        rc.room_memory_service = MagicMock()
        rc.sse_manager = MagicMock()
        rc.task_service = MagicMock()

        oversized = RoomUserMessage(
            room_id="room-001",
            message_id="msg-oversized",
            message_content=MessageContent(message_text="x" * (MAX_MESSAGE_LENGTH + 1)),
        )
        request = MagicMock()
        request.room_id = "room-001"
        request.message = oversized

        result = await rc.create_and_parse_user_message(request)
        assert result.success is False
        assert result.status_code == 400
        assert "maximum length" in result.error.lower()


@pytest.mark.asyncio
async def test_create_and_parse_processing_status_is_caller_owned_transport_only_with_client_request_id(
    monkeypatch,
):
    from models.room import MessageContent, RoomUserMessage
    from services.room_services import RoomServices
    import services.room_services as room_services

    rc = object.__new__(RoomServices)

    async def add_room_user_message(message):
        assert message.client_request_id == "cr-create"
        return True

    rc.database_service = MagicMock()
    rc.database_service.add_room_user_message = AsyncMock(side_effect=add_room_user_message)
    rc.database_service.get_room_by_room_id = AsyncMock(return_value=None)
    rc.room_memory_service = MagicMock()
    rc.room_memory_service.initialize_or_update_room_memory = AsyncMock(
        return_value=MagicMock(success=True)
    )

    fake_sse = MagicMock()
    fake_sse.send_processing_status = AsyncMock()
    helper_spy = AsyncMock()
    monkeypatch.setattr(room_services, "sse_manager", fake_sse)
    monkeypatch.setattr(
        room_services,
        "record_and_maybe_broadcast_run_event",
        helper_spy,
        raising=False,
    )

    message = RoomUserMessage(
        room_id="room-1",
        message_id="msg-create",
        user_id="user-1",
        message_content=MessageContent(message_text="hello"),
    )
    request = MagicMock()
    request.room_id = "room-1"
    request.message = message
    request.client_request_id = "cr-create"
    request.attachments = None

    result = await rc.create_and_parse_user_message(request)

    assert result.success is True
    helper_spy.assert_not_awaited()
    fake_sse.send_processing_status.assert_awaited_once_with(
        "room-1",
        "processing",
        "msg-create",
        client_request_id="cr-create",
    )
