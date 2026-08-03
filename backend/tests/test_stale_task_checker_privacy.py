import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.observability.logging import StructuredFormatter
from common.types import Task, TaskState, TaskStatus
from jobs.stale_task_checker import StaleTaskChecker, StaleTaskCheckerDeps
from models.room import MessageContent, RoomAgentMessage


@pytest.mark.asyncio
async def test_polled_stale_task_persists_only_public_terminal_artifacts():
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
    assert "Visible terminal output" in persisted_json
    assert "Visible agent answer" not in persisted_json
    assert persisted_task["history"] is None
    assert persisted_task["metadata"] is None
    assert store.update_task_on_message.await_args.kwargs["message_text"] == (
        "Visible terminal output"
    )
    store.touch_task_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_orphan_recovery_failure_logs_only_safe_exception_metadata(caplog):
    private_sentinel = "PRIVATE_ORPHAN_RECOVERY_SENTINEL"
    orphan = SimpleNamespace(
        agent_id=None,
        message_id="agent-message-1",
        related_message_id="user-message-1",
        room_id="room-1",
    )
    store = SimpleNamespace(
        get_orphaned_agent_messages=AsyncMock(return_value=[orphan]),
        is_message_cancelled=AsyncMock(return_value=False),
    )
    checker = StaleTaskChecker()
    checker.set_runtime_deps(
        StaleTaskCheckerDeps(
            store=store,
            rooms_collection=None,
            notify_task_update=AsyncMock(),
            increment_counter=MagicMock(),
            a2a_service=MagicMock(),
        )
    )

    def schedule_recovery(*_args, **_kwargs):
        raise RuntimeError(private_sentinel)

    checker.set_execution_recovery_deps(
        SimpleNamespace(schedule_recovery=schedule_recovery)
    )
    caplog.set_level(logging.ERROR, logger="jobs.stale_task_checker")

    await checker._recover_orphaned_messages()

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "orphan_recovery_schedule_failed"
    )
    formatted = StructuredFormatter(
        output_format="json",
        environment="test",
        service_version="test",
    ).format(record)
    assert record.room_id == "room-1"
    assert record.user_message_id == "user-message-1"
    assert record.error_type == "RuntimeError"
    assert private_sentinel not in formatted
