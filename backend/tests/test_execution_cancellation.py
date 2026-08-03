from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.types import Task, TaskState, TaskStatus
from execution.cancellation import (
    AgentTaskCleanupAdapter,
    MongoCancellationStoreAdapter,
)
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
async def test_mongo_cancellation_adapter_exposes_reconciliation():
    mongodb = SimpleNamespace(
        cancel_message=AsyncMock(return_value=True),
        mark_cancellation_reconciled=AsyncMock(return_value=True),
    )
    adapter = MongoCancellationStoreAdapter(mongodb)

    assert await adapter.mark_cancellation_reconciled("message-1") is True

    mongodb.mark_cancellation_reconciled.assert_awaited_once_with("message-1")


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


@pytest.mark.asyncio
async def test_cleanup_traverses_nested_queue_descendants():
    step_one = _tracked_message("step-1", TaskState.working)
    step_one.related_message_id = "user-1"
    step_two = _tracked_message("step-2", TaskState.submitted)
    step_two.related_message_id = "step-1"

    async def children(parent_message_id):
        return {
            "user-1": [step_one],
            "step-1": [step_two],
            "step-2": [],
        }[parent_message_id]

    store = SimpleNamespace(
        get_room_agent_messages_by_related_message_id=AsyncMock(side_effect=children),
        update_task_state_on_message=AsyncMock(return_value=(True, None)),
    )
    notify = AsyncMock(return_value=True)
    adapter = AgentTaskCleanupAdapter(
        message_task_store=store,
        get_agent_card_from_url=AsyncMock(),
        cancel_remote_task=AsyncMock(return_value=True),
        notify_task_update=notify,
    )

    await adapter.cleanup_cancelled_message_tasks(
        room_id="room-1",
        message_id="user-1",
    )

    assert [
        call.args[0] for call in store.update_task_state_on_message.await_args_list
    ] == ["step-1", "step-2"]
    assert notify.await_count == 2


@pytest.mark.asyncio
async def test_remote_cleanup_exception_does_not_block_local_cleanup():
    working = _tracked_message("working", TaskState.working)
    working.agent_url = "https://agent.example"
    store = SimpleNamespace(
        get_room_agent_messages_by_related_message_id=AsyncMock(return_value=[working]),
        update_task_state_on_message=AsyncMock(),
    )
    adapter = AgentTaskCleanupAdapter(
        message_task_store=store,
        get_agent_card_from_url=AsyncMock(return_value=SimpleNamespace()),
        cancel_remote_task=AsyncMock(side_effect=RuntimeError("offline")),
        notify_task_update=AsyncMock(return_value=True),
    )

    await adapter.cleanup_cancelled_message_tasks(
        room_id="room-1",
        message_id="user-1",
    )

    store.update_task_state_on_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_notification_does_not_block_local_terminalization():
    working = _tracked_message("working", TaskState.working)
    store = SimpleNamespace(
        get_room_agent_messages_by_related_message_id=AsyncMock(return_value=[working]),
        update_task_state_on_message=AsyncMock(return_value=(True, None)),
        reset_last_notified_state=AsyncMock(return_value=True),
    )
    adapter = AgentTaskCleanupAdapter(
        message_task_store=store,
        get_agent_card_from_url=AsyncMock(),
        cancel_remote_task=AsyncMock(return_value=True),
        notify_task_update=AsyncMock(side_effect=[False, True]),
    )

    await adapter.cleanup_cancelled_message_tasks(
        room_id="room-1",
        message_id="user-1",
    )

    store.update_task_state_on_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsupported_remote_cancellation_still_completes_local_cleanup():
    working = _tracked_message("working", TaskState.working)
    working.agent_url = "https://agent.example"
    store = SimpleNamespace(
        get_room_agent_messages_by_related_message_id=AsyncMock(return_value=[working]),
        update_task_state_on_message=AsyncMock(return_value=(True, None)),
    )
    adapter = AgentTaskCleanupAdapter(
        message_task_store=store,
        get_agent_card_from_url=AsyncMock(return_value=SimpleNamespace()),
        cancel_remote_task=AsyncMock(return_value=False),
        notify_task_update=AsyncMock(return_value=True),
    )

    await adapter.cleanup_cancelled_message_tasks(
        room_id="room-1",
        message_id="user-1",
    )

    store.update_task_state_on_message.assert_awaited_once()
