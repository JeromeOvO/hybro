"""
Unit tests for TaskStateManager module.

Tests cover:
- get_task: null-safe accessor
- state_str: enum/string conversion
- transition_task: terminal-state guard, state mutation, persist flag
- cancel_remaining_queue: batch cancellation
"""

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import TaskState, TaskStatus

from common.types import Task as CommonTask
from common.types import TaskState as CommonTaskState
from common.types import TaskStatus as CommonTaskStatus
from execution.state.task_state_manager import TaskStateManager, get_task, state_str
from execution.state.task_status_mapping import system_task_state_from_runtime_status
from models.room import MessageContent, RoomAgentMessage
from models.supervisor import RunStatus


def _make_message_with_task(state: TaskState | None = None) -> RoomAgentMessage:
    """Helper to create a RoomAgentMessage with an embedded Task."""
    task = MagicMock()
    if state is not None:
        task.status = TaskStatus(state=state)
    else:
        task.status = None

    content = MagicMock()
    content.message_task = task

    msg = MagicMock(spec=RoomAgentMessage)
    msg.message_content = content
    msg.message_id = "msg-001"
    msg.task_updated_at = None
    msg.step_number = 1
    msg.total_steps = 3
    msg.task_content = "Test task"
    return msg


def _make_real_message_with_common_task(
    state: CommonTaskState | None = None,
) -> RoomAgentMessage:
    task = (
        CommonTask(
            id="task-001",
            context_id="msg-preflight",
            status=CommonTaskStatus(state=state),
        )
        if state is not None
        else None
    )
    return RoomAgentMessage(
        room_id="room-001",
        message_id="msg-preflight",
        agent_id="agent-001",
        message_content=MessageContent(
            message_text="before",
            message_task=task,
        ),
        task_updated_at=None,
    )


# =============================================================================
# system task status mapping Tests
# =============================================================================


class TestSystemTaskStateFromRuntimeStatus:
    @pytest.mark.parametrize(
        ("runtime_status", "task_state"),
        [
            (RunStatus.AWAITING_INPUT, CommonTaskState.input_required),
            ("awaiting_input", CommonTaskState.input_required),
            ("input_required", CommonTaskState.input_required),
            ("auth_required", CommonTaskState.auth_required),
            ("completed", CommonTaskState.completed),
        ],
    )
    def test_maps_runtime_status_to_a2a_task_state(
        self, runtime_status, task_state
    ):
        assert system_task_state_from_runtime_status(runtime_status) == task_state


# =============================================================================
# get_task Tests
# =============================================================================


class TestGetTask:
    def test_returns_task_when_present(self):
        msg = _make_message_with_task(TaskState.submitted)
        assert get_task(msg) is not None

    def test_returns_none_when_content_is_none(self):
        msg = MagicMock()
        msg.message_content = None
        assert get_task(msg) is None

    def test_returns_none_when_task_is_none(self):
        msg = MagicMock()
        msg.message_content = MagicMock()
        msg.message_content.message_task = None
        assert get_task(msg) is None


# =============================================================================
# state_str Tests
# =============================================================================


class TestStateStr:
    def test_extracts_enum_value(self):
        assert state_str(TaskState.working) == "working"

    def test_passes_through_string(self):
        assert state_str("custom-state") == "custom-state"

    def test_handles_non_enum_non_string(self):
        assert state_str(42) == "42"


# =============================================================================
# transition_task Tests
# =============================================================================


class TestTransitionTask:
    @pytest.fixture
    def tsm(self):
        room_svc = MagicMock()
        room_svc.update_agent_message_by_message_id = AsyncMock(
            return_value=MagicMock(success=True)
        )
        notif_svc = MagicMock()
        notif_svc.send_task_update = AsyncMock()
        return TaskStateManager(
            room_runtime=room_svc,
            task_notifier=notif_svc,
        )

    @pytest.mark.asyncio
    async def test_transitions_non_terminal_state(self, tsm):
        msg = _make_message_with_task(TaskState.submitted)
        await tsm.transition_task(msg, TaskState.working)

        task = get_task(msg)
        assert task.status.state == TaskState.working

    @pytest.mark.asyncio
    async def test_blocks_terminal_to_terminal_transition(self, tsm):
        """Completed task should not be overwritten."""
        msg = _make_message_with_task(TaskState.completed)
        await tsm.transition_task(msg, TaskState.failed)

        task = get_task(msg)
        assert task.status.state == TaskState.completed

    @pytest.mark.asyncio
    async def test_blocks_failed_to_working_transition(self, tsm):
        msg = _make_message_with_task(TaskState.failed)
        await tsm.transition_task(msg, TaskState.working)

        task = get_task(msg)
        assert task.status.state == TaskState.failed

    @pytest.mark.asyncio
    async def test_persists_by_default(self, tsm):
        msg = _make_message_with_task(TaskState.submitted)
        await tsm.transition_task(msg, TaskState.working)
        tsm.room_runtime.update_agent_message_by_message_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_persist_when_disabled(self, tsm):
        msg = _make_message_with_task(TaskState.submitted)
        await tsm.transition_task(msg, TaskState.working, persist=False)
        tsm.room_runtime.update_agent_message_by_message_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_longer_notifies_directly(self, tsm):
        """transition_task no longer sends notifications — that's notify_task_update's job."""
        msg = _make_message_with_task(TaskState.submitted)
        await tsm.transition_task(msg, TaskState.working)
        tsm.task_notifier.send_task_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_attaches_error_message(self, tsm):
        msg = _make_message_with_task(TaskState.submitted)
        await tsm.transition_task(msg, TaskState.failed, error="boom")

        task = get_task(msg)
        assert task.status.state == TaskState.failed
        assert task.status.message is not None

    @pytest.mark.asyncio
    async def test_noop_when_no_task(self, tsm):
        msg = MagicMock()
        msg.message_content = None
        await tsm.transition_task(msg, TaskState.working)
        tsm.room_runtime.update_agent_message_by_message_id.assert_not_called()


@pytest.mark.asyncio
async def test_fail_pre_dispatch_task_creates_failed_task_when_missing():
    room_svc = MagicMock()
    room_svc.update_agent_message_by_message_id = AsyncMock(
        return_value=MagicMock(success=True)
    )
    notif_svc = MagicMock()
    notif_svc.send_task_update = AsyncMock()
    tsm = TaskStateManager(room_runtime=room_svc, task_notifier=notif_svc)

    msg = _make_real_message_with_common_task()

    await tsm.fail_pre_dispatch_task(
        msg,
        error="Attached file report.pdf exceeds the inline A2A limit.",
        error_code="file_too_large",
    )

    task = get_task(msg)
    assert task is not None
    assert task.status.state.value == "failed"
    assert task.status.message.parts[0].root.text == (
        "Attached file report.pdf exceeds the inline A2A limit."
    )
    assert task.metadata["preflight_failure_code"] == "file_too_large"
    assert msg.message_content.message_text == (
        "Attached file report.pdf exceeds the inline A2A limit."
    )
    assert msg.task_updated_at is not None
    room_svc.update_agent_message_by_message_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_pre_dispatch_task_transitions_existing_task_once_with_metadata():
    room_svc = MagicMock()
    room_svc.update_agent_message_by_message_id = AsyncMock(
        return_value=MagicMock(success=True)
    )
    notif_svc = MagicMock()
    notif_svc.send_task_update = AsyncMock()
    tsm = TaskStateManager(room_runtime=room_svc, task_notifier=notif_svc)

    msg = _make_real_message_with_common_task(CommonTaskState.submitted)

    await tsm.fail_pre_dispatch_task(
        msg,
        error="Attached file report.pdf exceeds the inline A2A limit.",
        error_code="file_too_large",
    )

    task = get_task(msg)
    assert task.status.state == CommonTaskState.failed
    assert task.status.message.parts[0].root.text == (
        "Attached file report.pdf exceeds the inline A2A limit."
    )
    assert task.metadata["preflight_failure_code"] == "file_too_large"
    assert msg.task_updated_at is not None
    room_svc.update_agent_message_by_message_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_pre_dispatch_task_leaves_terminal_task_unchanged():
    room_svc = MagicMock()
    room_svc.update_agent_message_by_message_id = AsyncMock(
        return_value=MagicMock(success=True)
    )
    notif_svc = MagicMock()
    notif_svc.send_task_update = AsyncMock()
    tsm = TaskStateManager(room_runtime=room_svc, task_notifier=notif_svc)

    msg = _make_real_message_with_common_task(CommonTaskState.completed)
    task = get_task(msg)
    original_status = task.status

    await tsm.fail_pre_dispatch_task(
        msg,
        error="Attached file report.pdf exceeds the inline A2A limit.",
        error_code="file_too_large",
    )

    assert task.status is original_status
    assert task.status.state == CommonTaskState.completed
    assert task.metadata is None
    assert msg.message_content.message_text == "before"
    assert msg.task_updated_at is None
    room_svc.update_agent_message_by_message_id.assert_not_awaited()


# =============================================================================
# cancel_remaining_queue Tests
# =============================================================================


class TestCancelRemainingQueue:
    @pytest.fixture
    def tsm(self):
        room_svc = MagicMock()
        room_svc.update_agent_message_by_message_id = AsyncMock(
            return_value=MagicMock(success=True)
        )
        notif_svc = MagicMock()
        return TaskStateManager(
            room_runtime=room_svc,
            task_notifier=notif_svc,
        )

    @pytest.mark.asyncio
    async def test_cancels_current_and_queued(self, tsm):
        current = _make_message_with_task(TaskState.working)
        q1 = _make_message_with_task(TaskState.submitted)
        q2 = _make_message_with_task(TaskState.submitted)

        await tsm.cancel_remaining_queue(deque([q1, q2]), current)

        assert get_task(current).status.state == TaskState.canceled
        assert get_task(q1).status.state == TaskState.canceled
        assert get_task(q2).status.state == TaskState.canceled

    @pytest.mark.asyncio
    async def test_skips_already_terminal_in_queue(self, tsm):
        already_done = _make_message_with_task(TaskState.completed)
        pending = _make_message_with_task(TaskState.submitted)

        await tsm.cancel_remaining_queue(deque([already_done, pending]))

        assert get_task(already_done).status.state == TaskState.completed
        assert get_task(pending).status.state == TaskState.canceled
