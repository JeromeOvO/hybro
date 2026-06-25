from unittest.mock import AsyncMock, MagicMock

import pytest

from app_shell.room_runtime import RoomServices
from models.file_upload import (
    MAX_ATTACHMENT_REFS_PER_REQUEST,
    MAX_ATTACHMENTS_PER_MESSAGE,
)
from models.request import RoomCenterUserMessageRequest, UserAttachmentRequest
from models.response import RoomCenterUserMessageResponse
from models.room import MessageContent, RoomUserMessage


@pytest.fixture
def room_svc():
    svc = RoomServices()
    svc.database_service = MagicMock()
    svc.sse_manager = MagicMock()
    reader = MagicMock()
    reader.get_for_room_file = AsyncMock(
        side_effect=lambda room_id, file_id: _file_meta(file_id, room_id) if room_id == "room1" else None
    )
    svc.bind_attachment_metadata_reader(reader)
    return svc


def _user_msg():
    return RoomUserMessage(
        room_id="room1",
        message_id="msg1",
        message_type="user",
        message_content=MessageContent(message_text="hi"),
    )


def _file_meta(file_id, room_id="room1"):
    return {
        "file_id": file_id,
        "room_id": room_id,
        "s3_key": f"uploads/{room_id}/{file_id}/file.png",
        "mime_type": "image/png",
        "file_name": "file.png",
        "size_bytes": 1024,
    }


class TestDualSourceMerge:
    async def test_top_level_only(self, room_svc):
        request = RoomCenterUserMessageRequest(
            room_id="room1",
            attachments=[UserAttachmentRequest(file_id="f1")],
        )
        msg = _user_msg()

        err = await room_svc._resolve_and_apply_attachments(request, msg)

        assert err is None
        assert len(msg.message_content.attachments) == 1
        assert msg.message_content.attachments[0].file_id == "f1"

    async def test_inline_only(self, room_svc):
        request = RoomCenterUserMessageRequest(
            room_id="room1",
            inline_file_ids=["f1"],
        )
        msg = _user_msg()

        err = await room_svc._resolve_and_apply_attachments(request, msg)

        assert err is None
        assert len(msg.message_content.attachments) == 1

    async def test_dedup_across_sources(self, room_svc):
        request = RoomCenterUserMessageRequest(
            room_id="room1",
            attachments=[UserAttachmentRequest(file_id="f1")],
            inline_file_ids=["f1"],
        )
        msg = _user_msg()

        err = await room_svc._resolve_and_apply_attachments(request, msg)

        assert err is None
        assert len(msg.message_content.attachments) == 1

    async def test_merged_from_both_sources(self, room_svc):
        request = RoomCenterUserMessageRequest(
            room_id="room1",
            attachments=[UserAttachmentRequest(file_id="f1")],
            inline_file_ids=["f2"],
        )
        msg = _user_msg()

        err = await room_svc._resolve_and_apply_attachments(request, msg)

        assert err is None
        assert len(msg.message_content.attachments) == 2

    async def test_no_attachments_noop(self, room_svc):
        request = RoomCenterUserMessageRequest(room_id="room1")
        msg = _user_msg()
        err = await room_svc._resolve_and_apply_attachments(request, msg)
        assert err is None
        assert msg.message_content.attachments is None


class TestCrossRoomRejection:
    async def test_file_from_different_room_rejected(self, room_svc):
        room_svc._attachment_metadata_reader.get_for_room_file = AsyncMock(return_value=None)

        request = RoomCenterUserMessageRequest(
            room_id="room1",
            attachments=[UserAttachmentRequest(file_id="f1")],
        )
        msg = _user_msg()

        err = await room_svc._resolve_and_apply_attachments(request, msg)

        assert isinstance(err, RoomCenterUserMessageResponse)
        assert err.status_code == 404
        assert not err.success


class TestMergedLimitEnforcement:
    async def test_exceeds_max_attachments(self, room_svc):
        ids = [f"f{i}" for i in range(MAX_ATTACHMENTS_PER_MESSAGE + 1)]
        request = RoomCenterUserMessageRequest(
            room_id="room1",
            attachments=[UserAttachmentRequest(file_id=fid) for fid in ids],
        )
        msg = _user_msg()

        err = await room_svc._resolve_and_apply_attachments(request, msg)

        assert isinstance(err, RoomCenterUserMessageResponse)
        assert err.status_code == 400


class TestContentSummary:
    async def test_content_summary_generated(self, room_svc):
        request = RoomCenterUserMessageRequest(
            room_id="room1",
            attachments=[UserAttachmentRequest(file_id="f1")],
        )
        msg = _user_msg()

        await room_svc._resolve_and_apply_attachments(request, msg)

        summary = msg.message_content.content_summary
        assert summary is not None
        assert summary["has_images"] is True
        assert summary["attachment_count"] == 1


class TestPreDedupGuard:
    def test_extract_attachments_rejects_over_limit(self):
        from api.room_center import _extract_attachments

        attachments = [{"file_id": f"f{i}"} for i in range(MAX_ATTACHMENT_REFS_PER_REQUEST + 1)]
        request_data = {"attachments": attachments}
        message = None
        result_atts, result_inline, err = _extract_attachments(request_data, message)
        assert err is not None
        assert not err.success
