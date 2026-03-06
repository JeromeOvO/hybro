"""Tests for services.task_notification_service.notify_task_update."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import Artifact, Part, Task, TaskState, TaskStatus, TextPart

from models.room import MessageContent, Room, RoomAgentMessage
from services.task_notification_service import notify_task_update

FROZEN_TIME = datetime(2026, 1, 15, 12, 0, 0)

PATCH_DB = "services.task_notification_service.db_service"
PATCH_NOTIF = "services.task_notification_service.notification_service"
PATCH_SSE = "services.task_notification_service.sse_manager"
PATCH_EXTRACT_ERR = "services.task_notification_service.extract_error_message"
PATCH_EXTRACT_STATUS = "services.task_notification_service.extract_status_message"
PATCH_SLEEP = "services.task_notification_service.asyncio.sleep"
PATCH_EXTRACT_PARTS = "common.utils.a2a_helpers.extract_parts_from_artifacts"
PATCH_CONVERT_S3 = "common.utils.a2a_helpers.convert_inline_bytes_to_s3"


def _make_task(
    state: TaskState,
    artifacts: list[Artifact] | None = None,
    status_message_text: str | None = None,
) -> Task:
    status = TaskStatus(state=state)
    if status_message_text:
        status = TaskStatus(
            state=state,
            message=MagicMock(parts=[MagicMock(text=status_message_text)]),
        )
    return Task(
        id="task-1",
        contextId="ctx-1",
        status=status,
        artifacts=artifacts,
    )


def _make_message(
    task: Task | None = None,
    message_text: str | None = None,
    has_task_tracking: bool = True,
) -> RoomAgentMessage:
    return RoomAgentMessage(
        room_id="room-1",
        message_id="msg-1",
        agent_id="agent-1",
        related_message_id="umsg-1",
        message_content=MessageContent(
            message_text=message_text,
            message_task=task,
        ),
        has_task_tracking=has_task_tracking,
        task_created_at=FROZEN_TIME,
    )


def _make_room(agent_name: str = "TestAgent") -> Room:
    return Room(
        room_id="room-1",
        room_name="Test Room",
        room_owner_id="user-1",
        room_owner_name="User",
        room_agent_set={"agent-1": agent_name},
        room_created_at=FROZEN_TIME,
    )


def _extracted_parts_mock(text: str = "", has_non_text: bool = False):
    m = MagicMock()
    m.text = text
    m.has_non_text = has_non_text
    m.file_parts = []
    m.data_parts = []
    return m


def _setup_db_mock(db, *, msg=None, idempotency_return=True, idempotency_error=False):
    """Configure common db_service mock methods."""
    if idempotency_error:
        db.update_last_notified_state = AsyncMock(side_effect=RuntimeError("DB down"))
    else:
        db.update_last_notified_state = AsyncMock(return_value=idempotency_return)
    db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
    db.update_room_agent_message_by_message_id = AsyncMock(return_value=True)
    db.get_room_by_room_id = AsyncMock(return_value=None)


def _setup_notif_mock(notif):
    """Ensure notification_service methods are AsyncMock."""
    notif.send_task_update = AsyncMock()


def _setup_sse_mock(sse):
    """Ensure sse_manager methods are AsyncMock."""
    sse.send_processing_status = AsyncMock()


CALL_KWARGS = dict(
    message_id="msg-1",
    state=TaskState.completed,
    room_id="room-1",
    user_id="user-1",
)


class TestNotifyTaskUpdate:
    """Tests for the canonical notify_task_update function."""

    # --------------------------------------------------------------------- #
    # 1. Idempotent skip when state is not new
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_idempotent_skip_when_state_not_new(self):
        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
        ):
            db.update_last_notified_state = AsyncMock(return_value=False)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is False
            db.update_last_notified_state.assert_awaited_once_with("msg-1", "completed")
            notif.send_task_update.assert_not_called()
            sse.send_processing_status.assert_not_called()

    # --------------------------------------------------------------------- #
    # 2. Proceeds when idempotency DB check raises
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_proceeds_on_idempotency_db_error(self):
        task = _make_task(TaskState.completed, artifacts=[
            Artifact(
                artifactId="a1",
                name="response",
                parts=[Part(root=TextPart(text="Hello"))],
            ),
        ])
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_PARTS, return_value=_extracted_parts_mock(text="Hello")),
        ):
            _setup_db_mock(db, msg=msg, idempotency_error=True)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            notif.send_task_update.assert_awaited_once()

    # --------------------------------------------------------------------- #
    # 3. Retries message load 3 attempts
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_retry_message_load_3_attempts(self):
        task = _make_task(TaskState.completed)
        msg = _make_message(task=task, message_text="done")

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock) as mock_sleep,
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            db.get_room_agent_message_by_message_id = AsyncMock(
                side_effect=[None, None, msg],
            )
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            assert db.get_room_agent_message_by_message_id.await_count == 3
            assert mock_sleep.await_count == 2
            mock_sleep.assert_awaited_with(0.5)
            notif.send_task_update.assert_awaited_once()

    # --------------------------------------------------------------------- #
    # 4. Returns False when no task tracking after retries
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_returns_false_when_no_task_tracking_after_retries(self):
        msg_no_tracking = _make_message(has_task_tracking=False)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg_no_tracking)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is False
            assert db.get_room_agent_message_by_message_id.await_count == 3
            notif.send_task_update.assert_not_called()

    # --------------------------------------------------------------------- #
    # 5. Completed state extracts artifact text
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_completed_extracts_artifact_text(self):
        task = _make_task(
            TaskState.completed,
            artifacts=[
                Artifact(
                    artifactId="a1",
                    name="response",
                    parts=[Part(root=TextPart(text="Hello world"))],
                )
            ],
        )
        msg = _make_message(task=task)
        extracted = _extracted_parts_mock(text="Hello world")

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_PARTS, return_value=extracted) as mock_ep,
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            mock_ep.assert_called_once_with(task.artifacts)
            call_kw = notif.send_task_update.call_args.kwargs
            assert call_kw["content"] == "Hello world"

    # --------------------------------------------------------------------- #
    # 6. Completed backfills artifacts from message_text
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_completed_backfills_artifacts_from_message_text(self):
        task = _make_task(TaskState.completed, artifacts=[])
        msg = _make_message(task=task, message_text="Backfilled answer")

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            db.update_room_agent_message_by_message_id.assert_awaited_once()
            updated_msg = db.update_room_agent_message_by_message_id.call_args[0][1]
            backfilled_task = updated_msg.message_content.message_task
            assert backfilled_task.artifacts is not None
            assert len(backfilled_task.artifacts) == 1
            assert backfilled_task.artifacts[0].name == "response"

    # --------------------------------------------------------------------- #
    # 7. Failed state extracts error
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_failed_extracts_error(self):
        task = _make_task(TaskState.failed)
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_ERR, return_value=None) as mock_err,
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(
                message_id="msg-1",
                state=TaskState.failed,
                room_id="room-1",
                user_id="user-1",
            )

            assert result is True
            mock_err.assert_called_once_with(task)
            call_kw = notif.send_task_update.call_args.kwargs
            assert call_kw["error"] == "Task failed"

    # --------------------------------------------------------------------- #
    # 8. input_required sets requires_input flag
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_input_required_sets_requires_input_flag(self):
        task = _make_task(TaskState.input_required)
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_STATUS, return_value="Please provide input") as mock_st,
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(
                message_id="msg-1",
                state=TaskState.input_required,
                room_id="room-1",
                user_id="user-1",
                send_processing_status=True,
            )

            assert result is True
            mock_st.assert_called_once_with(task)
            call_kw = notif.send_task_update.call_args.kwargs
            assert call_kw["requires_input"] is True
            assert call_kw["status_message"] == "Please provide input"
            sse.send_processing_status.assert_not_called()

    # --------------------------------------------------------------------- #
    # 9. auth_required sets requires_auth flag
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_auth_required_sets_requires_auth_flag(self):
        task = _make_task(TaskState.auth_required)
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_STATUS, return_value=None),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(
                message_id="msg-1",
                state=TaskState.auth_required,
                room_id="room-1",
                user_id="user-1",
            )

            assert result is True
            call_kw = notif.send_task_update.call_args.kwargs
            assert call_kw["requires_auth"] is True
            assert call_kw["status_message"] == "Authentication required"

    # --------------------------------------------------------------------- #
    # 10. send_processing_status only for terminal states
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_send_processing_status_only_for_terminal_states(self):
        task_completed = _make_task(TaskState.completed)
        msg_completed = _make_message(task=task_completed, message_text="done")

        task_input = _make_task(TaskState.input_required)
        msg_input = _make_message(task=task_input)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_STATUS, return_value="Need input"),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            # --- completed + send_processing_status=True -> called
            _setup_db_mock(db, msg=msg_completed)
            await notify_task_update(
                message_id="msg-1",
                state=TaskState.completed,
                room_id="room-1",
                user_id="user-1",
                send_processing_status=True,
            )
            sse.send_processing_status.assert_awaited_once_with(
                "room-1", TaskState.completed, "msg-1",
            )

            sse.send_processing_status.reset_mock()
            notif.send_task_update.reset_mock()

            # --- input_required + send_processing_status=True -> NOT called
            _setup_db_mock(db, msg=msg_input)
            await notify_task_update(
                message_id="msg-1",
                state=TaskState.input_required,
                room_id="room-1",
                user_id="user-1",
                send_processing_status=True,
            )
            sse.send_processing_status.assert_not_called()

    # --------------------------------------------------------------------- #
    # 11. Agent name resolved from room
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_agent_name_resolved_from_room(self):
        task = _make_task(TaskState.completed, artifacts=[
            Artifact(
                artifactId="a1",
                name="response",
                parts=[Part(root=TextPart(text="Result"))],
            ),
        ])
        msg = _make_message(task=task)
        room = _make_room(agent_name="SuperAgent")
        extracted = _extracted_parts_mock(text="Result")

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIF) as notif,
            patch(PATCH_SSE) as sse,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_PARTS, return_value=extracted),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            db.get_room_by_room_id = AsyncMock(return_value=room)
            _setup_notif_mock(notif)
            _setup_sse_mock(sse)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            db.get_room_by_room_id.assert_awaited_once_with("room-1")
            call_kw = notif.send_task_update.call_args.kwargs
            assert call_kw["agent_name"] == "SuperAgent"
