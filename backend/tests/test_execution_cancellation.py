from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.types import Task, TaskState, TaskStatus
from execution.cancellation import AgentTaskCleanupAdapter
from models.room import MessageContent, RoomAgentMessage


def _tracked_message(message_id: str, state: TaskState) -> RoomAgentMessage:
    return RoomAgentMessage(
        room_id="room-1",
        message_id=message_id,
        agent_id="agent-1",
        related_message_id="user-1",
        has_task_tracking=True,
        message_content=MessageContent(
            message_task=Task(
                id=f"task-{message_id}",
                status=TaskStatus(state=state),
            )
        ),
    )


@pytest.mark.asyncio
async def test_cleanup_cancels_only_non_terminal_agent_tasks():
    completed = _tracked_message("completed", TaskState.completed)
    working = _tracked_message("working", TaskState.working)
    store = SimpleNamespace(
        get_room_agent_messages_by_related_message_id=AsyncMock(
            return_value=[completed, working]
        ),
        update_task_state_on_message=AsyncMock(),
    )
    notify = AsyncMock(return_value=True)
    adapter = AgentTaskCleanupAdapter(
        message_task_store=store,
        get_agent_card_from_url=AsyncMock(),
        cancel_remote_task=AsyncMock(),
        notify_task_update=notify,
    )

    await adapter.cleanup_cancelled_message_tasks(
        room_id="room-1",
        message_id="user-1",
    )

    store.update_task_state_on_message.assert_awaited_once_with(
        "working",
        "canceled",
        message_text="Task was canceled",
    )
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["message_id"] == "working"
