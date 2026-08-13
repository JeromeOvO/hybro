from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.dto import RoomTimelineEntry, RoomTimelinePage
from common.protocols import AttachmentMetadataReader
from common.types import Task, TaskState, TaskStatus
from models.room import (
    MessageContent,
    RoomAgentMessage,
    RoomUserMessage,
    UserAttachment,
)
from room.compat.runtime import RoomServices
from room.timeline_projection import (
    HITLProjectionReader,
    RoomTimelineProjector,
)


def _page(*messages) -> RoomTimelinePage:
    return RoomTimelinePage(
        entries=[
            RoomTimelineEntry(
                source="user" if isinstance(message, RoomUserMessage) else "agent",
                message=message,
            )
            for message in messages
        ],
        has_more=False,
        next_position=None,
    )


def _projector(*, hitl=None, attachment=None) -> RoomTimelineProjector:
    return RoomTimelineProjector(
        hitl_reader=hitl
        or SimpleNamespace(get_hitl_request=AsyncMock(return_value=None)),
        attachment_metadata_reader=attachment
        or SimpleNamespace(get_for_room_file=AsyncMock(return_value=None)),
    )


def test_runtime_timeline_method_keeps_only_query_and_response_orchestration():
    source = inspect.getsource(RoomServices.inquiry_room_messages_by_room_id)

    assert "decode_timeline_cursor" in source
    assert "get_timeline_page" in source
    assert "_require_timeline_projector().project(page)" in source
    assert "encode_timeline_cursor" in source
    assert "public_persisted_task_data" not in source
    assert "RoomMessage(" not in source
    assert "get_for_room_file" not in source


def test_projector_is_projection_only_and_uses_narrow_ports():
    source = Path("room/timeline_projection.py").read_text()
    init_signature = inspect.signature(RoomTimelineProjector)

    assert "RoomCenterRoomMessageRequest" not in source
    assert "RoomCenterRoomMessageResponse" not in source
    assert "decode_timeline_cursor" not in source
    assert "get_timeline_page" not in source
    assert init_signature.parameters["hitl_reader"].annotation == "HITLProjectionReader"
    assert (
        init_signature.parameters["attachment_metadata_reader"].annotation
        == "AttachmentMetadataReader"
    )
    assert set(HITLProjectionReader.__dict__) >= {"get_hitl_request"}
    assert set(AttachmentMetadataReader.__dict__) >= {"get_for_room_file"}


def test_container_binds_narrow_timeline_projector_dependencies():
    source = Path("container.py").read_text()

    assert "RoomTimelineProjector(" in source
    assert "get_hitl_request=hitl_store.get_hitl_request" in source
    assert "get_for_room_file=file_storage.get_for_room_file" in source
    assert "room_runtime.bind_timeline_projector(" in source


def test_missing_projector_fails_fast():
    with pytest.raises(RuntimeError, match="timeline projector"):
        RoomServices()._require_timeline_projector()


async def test_user_attachment_projection_is_room_scoped_ordered_and_immutable():
    first_attachment = UserAttachment(
        file_id="file-1",
        mime_type="text/plain",
        file_name="one.txt",
        size_bytes=3,
        file_url="PRIVATE_OLD_URL",
    )
    first = RoomUserMessage(
        room_id="room-1",
        message_id="user-1",
        message_content=MessageContent(
            message_text="first",
            attachments=[first_attachment],
        ),
    )
    second = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-1",
        agent_id="agent-1",
        message_content=MessageContent(message_text="second"),
    )
    attachment_reader = SimpleNamespace(
        get_for_room_file=AsyncMock(
            return_value={
                "room_id": "room-1",
                "file_id": "file-1",
                "status": "ready",
                "content_url": "/api/v1/files/file-1/content",
            }
        )
    )

    projected = await _projector(attachment=attachment_reader).project(
        _page(first, second)
    )

    assert [message.message_id for message in projected] == ["user-1", "agent-1"]
    assert (
        projected[0].message_content.attachments[0].file_url
        == "/api/v1/files/file-1/content"
    )
    assert first_attachment.file_url == "PRIVATE_OLD_URL"
    attachment_reader.get_for_room_file.assert_awaited_once_with("room-1", "file-1")


async def test_missing_room_attachment_drops_existing_untrusted_url_without_fallback():
    attachment = UserAttachment(
        file_id="file-1",
        mime_type="text/plain",
        file_name="one.txt",
        size_bytes=3,
        file_url="PRIVATE_UNTRUSTED_URL",
    )
    user = RoomUserMessage(
        room_id="room-1",
        message_id="user-1",
        message_content=MessageContent(attachments=[attachment]),
    )

    projected = await _projector().project(_page(user))

    assert projected[0].message_content.attachments[0].file_url is None
    assert attachment.file_url == "PRIVATE_UNTRUSTED_URL"


def _hitl_message() -> tuple[RoomAgentMessage, Task]:
    task = Task(
        id="task-1",
        contextId="context-1",
        status=TaskStatus(state=TaskState.input_required),
        metadata={"hitl_request_id": "hitl-1", "private": "PRIVATE_SENTINEL"},
    )
    return (
        RoomAgentMessage(
            room_id="room-1",
            message_id="agent-1",
            agent_id="agent-1",
            message_content=MessageContent(message_task=task),
        ),
        task,
    )


async def test_trusted_hitl_projection_uses_verified_record_and_redacts_agent_prompt():
    message, task = _hitl_message()
    reader = SimpleNamespace(
        get_hitl_request=AsyncMock(
            return_value={
                "request_id": "hitl-1",
                "room_id": "room-1",
                "source": "agent",
                "status": "pending",
                "display_message_id": "agent-1",
                "agent_id": "agent-1",
                "a2a_task_id": "task-1",
                "a2a_context_id": "context-1",
                "prompt": "PRIVATE_SENTINEL",
            }
        )
    )

    metadata, request_id = await _projector(hitl=reader).trusted_hitl_projection(
        message, task
    )

    assert request_id == "hitl-1"
    assert metadata == {
        "hitl_request_id": "hitl-1",
        "hitl_prompt": "The agent needs additional information.",
        "hitl_prompt_type": "text",
        "hitl_choices": None,
        "hitl_a2a_task_id": "task-1",
        "hitl_a2a_context_id": "context-1",
    }


@pytest.mark.parametrize(
    "record",
    [
        {"request_id": "other"},
        {
            "request_id": "hitl-1",
            "room_id": "room-1",
            "source": "agent",
            "status": "canceled",
            "display_message_id": "agent-1",
        },
    ],
)
async def test_untrusted_or_canceled_hitl_projection_fails_closed(record):
    message, task = _hitl_message()
    reader = SimpleNamespace(get_hitl_request=AsyncMock(return_value=record))

    assert await _projector(hitl=reader).trusted_hitl_projection(message, task) == (
        None,
        None,
    )


async def test_hitl_reader_error_fails_closed(caplog):
    message, task = _hitl_message()
    reader = SimpleNamespace(
        get_hitl_request=AsyncMock(side_effect=RuntimeError("database unavailable"))
    )

    assert await _projector(hitl=reader).trusted_hitl_projection(message, task) == (
        None,
        None,
    )
    assert "Failed to verify HITL metadata" in caplog.text
