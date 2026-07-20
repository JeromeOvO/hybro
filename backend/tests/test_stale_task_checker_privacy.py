import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import Task, TaskState, TaskStatus
from jobs.stale_task_checker import StaleTaskChecker, StaleTaskCheckerDeps
from models.room import MessageContent, RoomAgentMessage


@pytest.mark.asyncio
async def test_polled_stale_task_persists_only_public_task_history():
    private_text = "PRIVATE_SENTINEL_stale_poll_history"
    remote_task = Task(
        id="task-1",
        contextId="context-1",
        status=TaskStatus(state=TaskState.completed),
        history=[
            {
                "kind": "message",
                "messageId": "private-user-message",
                "role": "user",
                "parts": [{"kind": "text", "text": private_text}],
                "metadata": {"private": private_text},
            },
            {
                "kind": "message",
                "messageId": "public-agent-message",
                "role": "agent",
                "parts": [{"kind": "text", "text": "Visible agent answer"}],
                "metadata": {"private": private_text},
            },
        ],
        artifacts=[
            {
                "artifactId": "artifact-1",
                "parts": [{"kind": "text", "text": "Visible terminal output"}],
            }
        ],
        metadata={"agent_id": "agent-1", "private": private_text},
    )
    tracked_message = RoomAgentMessage(
        room_id="room-1",
        message_id="agent-message-1",
        user_id="user-1",
        agent_id="agent-1",
        related_message_id="user-message-1",
        agent_url="https://agent.example",
        has_task_tracking=True,
        message_content=MessageContent(
            message_task=Task(
                id="task-1",
                contextId="context-1",
                status=TaskStatus(state=TaskState.working),
            )
        ),
    )
    store = SimpleNamespace(
        is_message_cancelled=AsyncMock(return_value=False),
        update_task_on_message=AsyncMock(return_value=True),
        touch_task_message=AsyncMock(),
    )
    notify_task_update = AsyncMock()
    checker = StaleTaskChecker()
    checker.set_runtime_deps(
        StaleTaskCheckerDeps(
            store=store,
            rooms_collection=None,
            notify_task_update=notify_task_update,
            increment_counter=MagicMock(),
            a2a_service=SimpleNamespace(
                get_agent_card_from_url=AsyncMock(return_value=MagicMock())
            ),
        )
    )
    checker._get_task_from_agent = AsyncMock(return_value=remote_task)

    await checker._process_stale_task(tracked_message)

    persisted_task = store.update_task_on_message.await_args.args[1]
    persisted_json = json.dumps(persisted_task, sort_keys=True)
    assert private_text not in persisted_json
    assert "Visible agent answer" in persisted_json
    assert persisted_task["history"][0]["role"] == "agent"
    assert persisted_task["history"][0]["metadata"] is None
    assert persisted_task["metadata"] is None
    assert store.update_task_on_message.await_args.kwargs["message_text"] == (
        "Visible terminal output"
    )
    store.touch_task_message.assert_not_awaited()
