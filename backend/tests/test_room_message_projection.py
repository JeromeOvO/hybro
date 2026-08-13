from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.dto import RoomTimelineEntry, RoomTimelinePage
from common.types import (
    Artifact,
    FileContent,
    FilePart,
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from models.request import RoomCenterRoomMessageRequest
from models.room import MessageContent, RoomAgentMessage
from room.compat.runtime import RoomServices
from room.timeline_projection import RoomTimelineProjector


def _bind_projector(runtime: RoomServices) -> None:
    hitl_reader = MagicMock()
    hitl_reader.get_hitl_request = AsyncMock(return_value=None)
    attachment_reader = MagicMock()
    attachment_reader.get_for_room_file = AsyncMock(return_value=None)
    runtime.bind_timeline_projector(
        RoomTimelineProjector(
            hitl_reader=hitl_reader,
            attachment_metadata_reader=attachment_reader,
        )
    )


NOW = datetime(2026, 7, 3, tzinfo=UTC)


def _text_message(text: str, *, message_id: str) -> Message:
    return Message(
        role=MessageRole.AGENT,
        message_id=message_id,
        parts=[Part(root=TextPart(text=text))],
    )


@pytest.mark.asyncio
async def test_room_message_projection_backfills_legacy_terminal_text_as_artifact() -> (
    None
):
    final_text = (
        "Hello! I'm your cyber insurance broker agent. How can I assist you today?"
    )
    progress_text = "Preparing cyber broker submission..."
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(
            state=TaskState.completed,
            message=_text_message(final_text, message_id="status-message"),
        ),
        artifacts=None,
        history=[_text_message(progress_text, message_id="history-message")],
    )
    agent_message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-1",
        agent_id="agent-1",
        related_message_id="user-1",
        message_created_at=NOW,
        message_content=MessageContent(
            message_text=final_text,
            message_task=task,
        ),
    )
    runtime = RoomServices()
    facade = AsyncMock()
    facade.get_timeline_page.return_value = RoomTimelinePage(
        entries=[RoomTimelineEntry(source="agent", message=agent_message)],
        has_more=False,
        next_position=None,
    )
    runtime.bind_facade(facade)
    _bind_projector(runtime)

    response = await runtime.inquiry_room_messages_by_room_id(
        RoomCenterRoomMessageRequest(room_id="room-1")
    )

    assert response.success is True
    assert response.message_list is not None
    projected = response.message_list[0]
    assert projected.message_content.message_text == final_text
    assert projected.message_content.message_task.history is None
    assert (
        projected.message_content.message_task.artifacts[0].parts[0].root.text
        == final_text
    )


@pytest.mark.asyncio
async def test_room_message_projection_drops_inline_file_without_uri() -> None:
    private_bytes = "PRIVATE_SENTINEL_room_inline_file"
    task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.completed),
        artifacts=[
            Artifact(
                artifact_id="final-response",
                parts=[
                    Part(root=TextPart(text="Public final response")),
                    Part(
                        root=FilePart(
                            file=FileContent(
                                bytes=private_bytes,
                                mimeType="text/plain",
                                name="private.txt",
                            )
                        )
                    ),
                ],
            )
        ],
    )
    agent_message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-1",
        agent_id="agent-1",
        related_message_id="user-1",
        message_created_at=NOW,
        message_content=MessageContent(message_task=task),
    )
    runtime = RoomServices()
    facade = AsyncMock()
    facade.get_timeline_page.return_value = RoomTimelinePage(
        entries=[RoomTimelineEntry(source="agent", message=agent_message)],
        has_more=False,
        next_position=None,
    )
    runtime.bind_facade(facade)
    _bind_projector(runtime)

    response = await runtime.inquiry_room_messages_by_room_id(
        RoomCenterRoomMessageRequest(room_id="room-1")
    )

    assert response.success is True
    assert response.message_list is not None
    projected = response.message_list[0]
    assert projected.message_content.message_text == "Public final response"
    assert len(projected.message_content.message_task.artifacts[0].parts) == 1
    assert private_bytes not in projected.model_dump_json()
