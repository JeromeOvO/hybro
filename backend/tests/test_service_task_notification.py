"""Tests for execution.dispatch.task_notifications.notify_task_update."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.a2a_constants import SSEProcessingStatus
from common.types import (
    Artifact,
    DataPart,
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from execution.dispatch.task_notifications import notify_task_update
from models.room import MessageContent, Room, RoomAgentMessage

FROZEN_TIME = datetime(2026, 1, 15, 12, 0, 0)

PATCH_DB = "execution.dispatch.task_notifications._notification_store"
PATCH_NOTIFIER = "execution.dispatch.task_notifications._task_notifier"
PATCH_DELIVERY = "execution.dispatch.task_notifications._delivery"
PATCH_EXTRACT_ERR = "execution.dispatch.task_notifications.extract_error_message"
PATCH_EXTRACT_STATUS = "execution.dispatch.task_notifications.extract_status_message"
PATCH_SLEEP = "execution.dispatch.task_notifications.asyncio.sleep"
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
            message=Message(
                role=MessageRole.AGENT,
                parts=[Part(root=TextPart(text=status_message_text))],
            ),
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


def _setup_notifier_mock(notifier):
    """Ensure task_notifier methods are AsyncMock."""
    notifier.send_task_update = AsyncMock()
    from execution.dispatch import task_notifications

    task_notifications._task_notifier = notifier


def _setup_delivery_mock(delivery):
    """Ensure delivery methods are AsyncMock."""
    delivery.send_processing_status = AsyncMock()
    from execution.dispatch import task_notifications

    task_notifications._delivery = delivery


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
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
        ):
            db.update_last_notified_state = AsyncMock(return_value=False)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is False
            db.update_last_notified_state.assert_awaited_once_with("msg-1", "completed")
            notifier.send_task_update.assert_not_called()
            delivery.send_processing_status.assert_not_called()

    # --------------------------------------------------------------------- #
    # 2. Proceeds when idempotency DB check raises
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_proceeds_on_idempotency_db_error(self):
        task = _make_task(
            TaskState.completed,
            artifacts=[
                Artifact(
                    artifactId="a1",
                    name="response",
                    parts=[Part(root=TextPart(text="Hello"))],
                ),
            ],
        )
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
            patch(
                PATCH_EXTRACT_PARTS, return_value=_extracted_parts_mock(text="Hello")
            ),
        ):
            _setup_db_mock(db, msg=msg, idempotency_error=True)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            notifier.send_task_update.assert_awaited_once()

    # --------------------------------------------------------------------- #
    # 3. Retries message load 3 attempts
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_retry_message_load_3_attempts(self):
        task = _make_task(TaskState.completed)
        msg = _make_message(task=task, message_text="done")

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock) as mock_sleep,
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            db.get_room_agent_message_by_message_id = AsyncMock(
                side_effect=[None, None, msg],
            )
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            assert db.get_room_agent_message_by_message_id.await_count == 3
            assert mock_sleep.await_count == 2
            mock_sleep.assert_awaited_with(0.5)
            notifier.send_task_update.assert_awaited_once()

    # --------------------------------------------------------------------- #
    # 4. Returns False when no task tracking after retries
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_returns_false_when_no_task_tracking_after_retries(self):
        msg_no_tracking = _make_message(has_task_tracking=False)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg_no_tracking)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is False
            assert db.get_room_agent_message_by_message_id.await_count == 3
            notifier.send_task_update.assert_not_called()

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
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_PARTS, return_value=extracted) as mock_ep,
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            assert mock_ep.call_count >= 1
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_completed_prefers_persisted_response_for_data_only_artifact(self):
        response_text = "The insurer returned an indicative quote."
        task = _make_task(
            TaskState.completed,
            artifacts=[
                Artifact(
                    artifactId="quote-1",
                    name="quote",
                    parts=[Part(root=DataPart(data={"premium": 59300}))],
                )
            ],
        )
        msg = _make_message(task=task, message_text=response_text)
        extracted = _extracted_parts_mock(has_non_text=True)
        extracted.data_parts = [{"kind": "data", "data": {"premium": 59300}}]

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_PARTS, return_value=extracted),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["content"] == response_text
            assert call_kw["parts"][0]["kind"] == "data"
            assert call_kw["parts"][0]["data"] == {"premium": 59300}

    # --------------------------------------------------------------------- #
    # 6. Completed tasks do not backfill artifacts from private history
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_completed_does_not_backfill_artifacts_from_agent_history(self):
        public_response = "Persisted public response"
        task = _make_task(TaskState.completed, artifacts=[])
        task.history = [
            Message(
                role=MessageRole.AGENT,
                parts=[Part(root=TextPart(text="Backfilled public answer"))],
            )
        ]
        msg = _make_message(task=task, message_text=public_response)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            db.update_room_agent_message_by_message_id.assert_not_awaited()
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["content"] == public_response
            assert "Backfilled public answer" not in repr(call_kw)

    # --------------------------------------------------------------------- #
    # 7. Failed state extracts error
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_failed_extracts_error(self):
        task = _make_task(TaskState.failed)
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_ERR, return_value=None) as mock_err,
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(
                message_id="msg-1",
                state=TaskState.failed,
                room_id="room-1",
                user_id="user-1",
            )

            assert result is True
            mock_err.assert_called_once()
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["error"] == "Task failed"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("state", "safe_error"),
        [
            (TaskState.failed, "Task failed"),
            (TaskState.rejected, "Task was rejected by the agent"),
        ],
    )
    async def test_failed_or_rejected_agent_status_message_is_not_public_error(
        self,
        state,
        safe_error,
    ):
        private_sentinel = f"PRIVATE_SENTINEL_{state.value}_agent_status_message"
        task = _make_task(
            state,
            status_message_text=private_sentinel,
        )
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_ERR, return_value=private_sentinel),
            patch(PATCH_EXTRACT_STATUS, return_value=private_sentinel),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)
            emitter = AsyncMock()
            from execution.dispatch import task_notifications as task_notifications_mod

            with patch.object(
                task_notifications_mod,
                "_processing_status_emitter",
                emitter,
            ):
                result = await notify_task_update(
                    message_id="msg-1",
                    state=state,
                    room_id="room-1",
                    user_id="user-1",
                )

            assert result is True
            payload = notifier.send_task_update.await_args.kwargs
            assert payload["error"] == safe_error
            assert payload["status_message"] is None
            assert private_sentinel not in repr(payload)
            emitter_payload = emitter.await_args.kwargs
            assert emitter_payload["details"] == {"message": safe_error}
            assert private_sentinel not in repr(emitter_payload)

    # --------------------------------------------------------------------- #
    # 7b. Failed state with artifacts suppresses partial content
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_failed_with_artifacts_suppresses_partial_content(self):
        """Failed tasks expose the safe error, not partial artifact content."""
        task = _make_task(
            TaskState.failed,
            artifacts=[
                Artifact(
                    artifactId="a1",
                    name="partial",
                    parts=[Part(root=TextPart(text="Partial result before failure"))],
                ),
            ],
        )
        msg = _make_message(task=task)
        extracted = _extracted_parts_mock(text="Partial result before failure")

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_PARTS, return_value=extracted) as mock_ep,
            patch(PATCH_EXTRACT_ERR, return_value="Agent error"),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(
                message_id="msg-1",
                state=TaskState.failed,
                room_id="room-1",
                user_id="user-1",
            )

            assert result is True
            mock_ep.assert_not_called()
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["content"] is None
            assert call_kw["error"] == "Task failed"

    # --------------------------------------------------------------------- #
    # 8. input_required sets requires_input flag
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_input_required_sets_requires_input_flag(self):
        private_sentinel = "PRIVATE_SENTINEL_notification_input_prompt"
        task = _make_task(
            TaskState.input_required,
            status_message_text=private_sentinel,
        )
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)
            emitter = AsyncMock()
            from execution.dispatch import task_notifications as task_notifications_mod

            with patch.object(
                task_notifications_mod,
                "_processing_status_emitter",
                emitter,
            ):
                result = await notify_task_update(
                    message_id="msg-1",
                    state=TaskState.input_required,
                    room_id="room-1",
                    user_id="user-1",
                )

            assert result is True
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["requires_input"] is True
            assert call_kw["status_message"] is None
            assert private_sentinel not in repr(call_kw)
            emitter.assert_awaited_once_with(
                room_id="room-1",
                status=SSEProcessingStatus.AWAITING_INPUT,
                message_id="msg-1",
                lifecycle_message_id="msg-1",
                record_lifecycle=True,
                client_request_id=None,
                details=None,
                error_message=None,
            )

    # --------------------------------------------------------------------- #
    # 9. auth_required sets requires_auth flag
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_auth_required_sets_requires_auth_flag(self):
        task = _make_task(TaskState.auth_required)
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_STATUS, return_value=None),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(
                message_id="msg-1",
                state=TaskState.auth_required,
                room_id="room-1",
                user_id="user-1",
            )

            assert result is True
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["requires_auth"] is True
            assert call_kw["status_message"] == "Authentication required"

    # --------------------------------------------------------------------- #
    # 10. completed-without-visible-content does not forward status_message
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_completed_without_visible_content_drops_status_message(self):
        private_sentinel = "PRIVATE_SENTINEL_completed_status_message"
        task = _make_task(TaskState.completed)
        msg = _make_message(task=task)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_STATUS, return_value=private_sentinel),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(
                message_id="msg-1",
                state=TaskState.completed,
                room_id="room-1",
                user_id="user-1",
            )

            assert result is True
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["status_message"] is None

    # --------------------------------------------------------------------- #
    # 10. lifecycle processing_status mapping for task states
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_send_processing_status_for_terminal_and_interactive_states(self):
        task_completed = _make_task(TaskState.completed)
        msg_completed = _make_message(task=task_completed, message_text="done")

        task_input = _make_task(TaskState.input_required)
        msg_input = _make_message(task=task_input)

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_STATUS, return_value="Need input"),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)
            emitter = AsyncMock()
            from execution.dispatch import task_notifications as task_notifications_mod

            # --- completed -> lifecycle completed emitted
            _setup_db_mock(db, msg=msg_completed)
            with patch.object(
                task_notifications_mod,
                "_processing_status_emitter",
                emitter,
            ):
                await notify_task_update(
                    message_id="msg-1",
                    state=TaskState.completed,
                    room_id="room-1",
                    user_id="user-1",
                )
            emitter.assert_awaited_once_with(
                room_id="room-1",
                status=SSEProcessingStatus.COMPLETED,
                message_id="msg-1",
                lifecycle_message_id="msg-1",
                record_lifecycle=True,
                client_request_id=None,
                details=None,
                error_message=None,
            )

            emitter.reset_mock()
            notifier.send_task_update.reset_mock()

            # --- input_required -> lifecycle awaiting_input emitted
            _setup_db_mock(db, msg=msg_input)
            with patch.object(
                task_notifications_mod,
                "_processing_status_emitter",
                emitter,
            ):
                await notify_task_update(
                    message_id="msg-1",
                    state=TaskState.input_required,
                    room_id="room-1",
                    user_id="user-1",
                )
        emitter.assert_awaited_once_with(
            room_id="room-1",
            status=SSEProcessingStatus.AWAITING_INPUT,
            message_id="msg-1",
            lifecycle_message_id="msg-1",
            record_lifecycle=True,
            client_request_id=None,
            details=None,
            error_message=None,
        )

    # --------------------------------------------------------------------- #
    # 11. Agent name resolved from room
    # --------------------------------------------------------------------- #
    @pytest.mark.asyncio
    async def test_agent_name_resolved_from_room(self):
        task = _make_task(
            TaskState.completed,
            artifacts=[
                Artifact(
                    artifactId="a1",
                    name="response",
                    parts=[Part(root=TextPart(text="Result"))],
                ),
            ],
        )
        msg = _make_message(task=task)
        room = _make_room(agent_name="SuperAgent")
        extracted = _extracted_parts_mock(text="Result")

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(PATCH_EXTRACT_PARTS, return_value=extracted),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            db.get_room_by_room_id = AsyncMock(return_value=room)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            db.get_room_by_room_id.assert_awaited_once_with("room-1")
            call_kw = notifier.send_task_update.call_args.kwargs
            assert call_kw["agent_name"] == "SuperAgent"

    @pytest.mark.asyncio
    async def test_legacy_notification_never_emits_private_persisted_dispatch_text(
        self,
    ):
        private_sentinel = "PRIVATE_SENTINEL_legacy_notification"
        task = _make_task(
            TaskState.completed,
            artifacts=[
                Artifact(
                    artifactId="a-public",
                    name="response",
                    parts=[Part(root=TextPart(text="Final public result"))],
                )
            ],
        )
        task.history = [
            Message(
                role=MessageRole.USER,
                parts=[Part(root=TextPart(text=private_sentinel))],
            ),
            Message(
                role=MessageRole.AGENT,
                parts=[Part(root=TextPart(text="Final public result"))],
            ),
        ]
        task.metadata = {
            "task_content": private_sentinel,
            "internal_task_payload": private_sentinel,
        }
        msg = _make_message(task=task, message_text=private_sentinel)
        msg.task_content = private_sentinel
        room = _make_room(agent_name="Claims Agent")

        with (
            patch(PATCH_DB) as db,
            patch(PATCH_NOTIFIER) as notifier,
            patch(PATCH_DELIVERY) as delivery,
            patch(PATCH_SLEEP, new_callable=AsyncMock),
            patch(
                PATCH_EXTRACT_PARTS,
                return_value=_extracted_parts_mock(text="Final public result"),
            ),
            patch(PATCH_CONVERT_S3, new_callable=AsyncMock),
        ):
            _setup_db_mock(db, msg=msg)
            db.get_room_by_room_id = AsyncMock(return_value=room)
            _setup_notifier_mock(notifier)
            _setup_delivery_mock(delivery)

            result = await notify_task_update(**CALL_KWARGS)

            assert result is True
            payload = notifier.send_task_update.await_args.kwargs
            assert payload["content"] == "Final public result"
            assert payload["task_content"] == "Requesting Claims Agent"
            assert private_sentinel not in repr(payload)
