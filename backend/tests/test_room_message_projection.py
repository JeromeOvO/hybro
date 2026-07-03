from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from common.types import Message, MessageRole, Part, Task, TaskState, TaskStatus, TextPart
from models.request import RoomCenterRoomMessageRequest
from models.response import (
    RoomCenterAgentMessageResponse,
    RoomCenterUserMessageResponse,
)
from models.room import MessageContent, RoomAgentMessage
from room.compat.runtime import RoomServices


NOW = datetime(2026, 7, 3, tzinfo=UTC)


def _text_message(text: str, *, message_id: str) -> Message:
    return Message(
        role=MessageRole.AGENT,
        message_id=message_id,
        parts=[Part(root=TextPart(text=text))],
    )


@pytest.mark.asyncio
async def test_room_message_projection_prefers_terminal_message_text_over_history_status() -> None:
    final_text = "Hello! I'm your cyber insurance broker agent. How can I assist you today?"
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
    runtime.inquiry_user_messages_by_room_id = AsyncMock(
        return_value=RoomCenterUserMessageResponse(
            room_id="room-1",
            message_list=[],
            success=True,
            error=None,
        )
    )
    runtime.inquiry_agent_messages_by_room_id = AsyncMock(
        return_value=RoomCenterAgentMessageResponse(
            room_id="room-1",
            message_list=[agent_message],
            success=True,
            error=None,
        )
    )

    response = await runtime.inquiry_room_messages_by_room_id(
        RoomCenterRoomMessageRequest(room_id="room-1")
    )

    assert response.success is True
    assert response.message_list is not None
    assert response.message_list[0].message_content.message_text == final_text
