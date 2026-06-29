"""TaskStateManager — single-responsibility module for task state transitions.

All state transitions (persist to MongoDB) go through this class.
The key invariant is that ``transition_task`` persists **by default**, so a
developer must *actively opt out* — inverting the old failure mode where
"forgot to persist" was the common bug.

Terminal/interactive **notifications** are handled by
``execution.dispatch.task_notifications.notify_task_update`` (separate concern).
Non-terminal streaming progress notifications still use ``notify_task``.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from a2a_adapter.task_status import build_task_status, coerce_task_state
from common.a2a_constants import is_terminal_state
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.processing import ProcessingContext
from models.request import RoomCenterAgentMessageRequest
from models.room import RoomAgentMessage

if TYPE_CHECKING:
    from execution.ports import NotificationServicePort, RoomRuntimePort

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def get_task(msg: RoomAgentMessage) -> Any | None:
    """Safely access ``msg.message_content.message_task``, returning None on any miss.

    If the stored value is a raw dict (e.g. from a legacy round-trip
    through continuation serialization), it is coerced into a proper Task model
    and written back so downstream code can rely on attribute access.
    """
    if msg.message_content and msg.message_content.message_task:
        task = msg.message_content.message_task
        if isinstance(task, dict):
            from common.types import Task

            task = Task.model_validate(task)
            msg.message_content.message_task = task
        return task
    return None


def state_str(state) -> str:
    """Convert a TaskState enum (or string) to its string value."""
    return state.value if hasattr(state, "value") else str(state)


# ------------------------------------------------------------------
# TaskStateManager
# ------------------------------------------------------------------


class TaskStateManager:
    """All task state transitions: persist to DB + notify frontend.

    Dependencies are injected via constructor so the class is testable in
    isolation without requiring the full service graph.
    """

    def __init__(
        self,
        room_runtime: RoomRuntimePort,
        task_notifier: NotificationServicePort,
    ) -> None:
        self.room_runtime = room_runtime
        self.task_notifier = task_notifier

    # ------------------------------------------------------------------
    # Core primitives
    # ------------------------------------------------------------------

    async def persist_message(self, message: RoomAgentMessage) -> bool:
        """Persist a RoomAgentMessage to the database. Returns True on success."""
        resp = await self.room_runtime.update_agent_message_by_message_id(
            RoomCenterAgentMessageRequest(
                message_id=message.message_id, message=message
            )
        )
        if not resp.success:
            logger.error(
                "TaskStateManager: Failed to persist message %s: %s",
                message.message_id,
                resp.error,
            )
        return resp.success

    async def notify_task(self, ctx: ProcessingContext, status: str, **kwargs) -> None:
        """Send a task update notification using common fields from *ctx*."""
        await self.task_notifier.send_task_update(
            room_id=ctx.room_id,
            message_id=ctx.tracked_message_id,
            status=status,
            agent_card=ctx.agent_card,
            agent_id=ctx.current_message.agent_id,
            created_at=ctx.created_at,
            step_number=ctx.step_number,
            total_steps=ctx.total_steps,
            client_request_id=ctx.current_message.client_request_id,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Unified state transition (A-1)
    # ------------------------------------------------------------------

    async def transition_task(
        self,
        message: RoomAgentMessage,
        new_state: Any,
        *,
        error: str | None = None,
        persist: bool = True,
    ) -> None:
        """Single entry point for all task state transitions.

        Always persists by default.  Callers opt out explicitly
        (e.g., ``persist=False`` for batch queue cleanup).

        Terminal/interactive notifications are the caller's responsibility
        via ``execution.dispatch.task_notifications.notify_task_update``.

        The terminal-state guard prevents overwriting a ``completed``,
        ``failed``, ``canceled``, or ``rejected`` status — making double-
        transition bugs harmless instead of data-corrupting.
        """
        task = get_task(message)
        if not task:
            return

        # Guard: never overwrite a terminal state
        if task.status and is_terminal_state(task.status.state):
            logger.warning(
                "Attempted to transition already-terminal task %s from %s to %s",
                message.message_id,
                state_str(task.status.state),
                state_str(new_state),
            )
            return

        task.status = build_task_status(new_state, error_text=error)
        message.task_updated_at = utcnow()

        if persist:
            await self.persist_message(message)

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------

    async def cancel_remaining_queue(
        self,
        message_queue: deque,
        current_message: RoomAgentMessage | None = None,
    ) -> None:
        """Persist ``TaskState.canceled`` for *current_message* (if given)
        and every remaining message in *message_queue*.

        Already-terminal messages are skipped by ``transition_task``'s guard.
        """
        messages_to_cancel: list[RoomAgentMessage] = []
        if current_message is not None:
            messages_to_cancel.append(current_message)
        messages_to_cancel.extend(message_queue)

        for msg in messages_to_cancel:
            await self.transition_task(
                msg, coerce_task_state("canceled"), persist=True
            )
