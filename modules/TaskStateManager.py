"""TaskStateManager — single-responsibility module for task state transitions.

All state transitions (persist to MongoDB + notify frontend via SSE) go
through this class.  The key invariant is that ``transition_task`` persists
**by default**, so a developer must *actively opt out* — inverting the old
failure mode where "forgot to persist" was the common bug.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING
from uuid import uuid4

from a2a.types import AgentCard, Message, Role, Task, TaskState, TaskStatus, TextPart

from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.processing import ProcessingContext
from models.request import RoomCenterAgentMessageRequest
from models.room import RoomAgentMessage
from services.a2a_constants import is_terminal_state

if TYPE_CHECKING:
    from services.notification_service import NotificationService
    from services.room_services import RoomServices

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def get_task(msg: RoomAgentMessage) -> Task | None:
    """Safely access ``msg.message_content.message_task``, returning None on any miss."""
    if msg.message_content and msg.message_content.message_task:
        return msg.message_content.message_task
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
        room_services: RoomServices,
        notification_service: NotificationService,
    ) -> None:
        self.room_services = room_services
        self.notification_service = notification_service

    # ------------------------------------------------------------------
    # Core primitives
    # ------------------------------------------------------------------

    async def persist_message(self, message: RoomAgentMessage) -> bool:
        """Persist a RoomAgentMessage to the database. Returns True on success."""
        resp = await self.room_services.update_agent_message_by_message_id(
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
        await self.notification_service.send_task_update(
            room_id=ctx.room_id,
            message_id=ctx.tracked_message_id,
            status=status,
            agent_card=ctx.agent_card,
            agent_id=ctx.current_message.agent_id,
            created_at=ctx.created_at,
            step_number=ctx.step_number,
            total_steps=ctx.total_steps,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Unified state transition (A-1)
    # ------------------------------------------------------------------

    async def transition_task(
        self,
        message: RoomAgentMessage,
        new_state: TaskState,
        *,
        ctx: ProcessingContext | None = None,
        error: str | None = None,
        content: str | None = None,
        notify: bool = True,
        persist: bool = True,
    ) -> None:
        """Single entry point for all task state transitions.

        Always persists by default. Always notifies by default (when *ctx* is
        provided).  Callers opt out explicitly (e.g., ``notify=False`` for batch
        queue cleanup).

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

        # Update state
        task.status = TaskStatus(state=new_state)
        if error:
            task.status.message = Message(
                message_id=uuid4().hex,
                role=Role.agent,
                parts=[TextPart(text=error)],
            )
        message.task_updated_at = utcnow()

        if persist:
            await self.persist_message(message)

        if notify and ctx:
            await self.notify_task(ctx, new_state, content=content, error=error)

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------

    async def fail_task_and_notify(
        self,
        *,
        room_id: str,
        message: RoomAgentMessage,
        error_text: str,
        agent_id: str | None,
        agent_card: AgentCard | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> None:
        """Persist a failed TaskStatus on *message* and send the failure notification.

        Delegates state persistence to ``transition_task`` (which includes the
        terminal-state guard) and then sends the notification via
        ``notification_service`` with the full set of display parameters.

        *step_number* / *total_steps* default to the values stored on *message*
        when not supplied explicitly.  *agent_card* is forwarded to the
        notification service so it can resolve the agent display-name.
        """
        await self.transition_task(
            message, TaskState.failed, error=error_text, persist=True, notify=False
        )
        await self.notification_service.send_task_update(
            room_id=room_id,
            message_id=message.message_id,
            status=TaskState.failed,
            error=error_text,
            agent_id=agent_id,
            agent_card=agent_card,
            step_number=step_number if step_number is not None else message.step_number,
            total_steps=total_steps if total_steps is not None else message.total_steps,
            task_content=message.task_content,
        )

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
                msg, TaskState.canceled, persist=True, notify=False
            )
