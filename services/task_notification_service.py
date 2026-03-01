"""Canonical, idempotent task notification service.

``notify_task_update`` is the **single entry point** for all ``task_update``
SSE events.  Every code path that transitions a task to a terminal or
interactive state should call this function after persisting the new state
to the DB.

Idempotency is provided by ``db_service.update_last_notified_state``:
calling the function twice with the same state for the same message is a
safe no-op.
"""

from __future__ import annotations

import asyncio
import uuid

from a2a.types import (
    Artifact,
    Part,
    Task,
    TaskState,
    TextPart,
)

from common.utils.a2a_helpers import (
    extract_error_message,
    extract_status_message,
    extract_text_from_artifacts,
)
from common.utils.logger import get_logger
from services.a2a_constants import is_terminal_state
from services.database_service import db_service
from services.notification_service import notification_service
from services.sse_services import sse_manager

logger = get_logger(__name__)


async def notify_task_update(
    message_id: str,
    state: TaskState,
    room_id: str,
    user_id: str,
    error: str | None = None,
    send_processing_status: bool = False,
) -> bool:
    """Idempotent, DB-backed SSE notification for task state changes.

    Single canonical entry point for all ``task_update`` SSE events.
    Safe to call multiple times with the same state (no-op on duplicate).

    **PREREQUISITE:** The task must already be persisted to the DB with its
    current state, artifacts, and status message BEFORE calling this function.
    The function reads the full Task object from the DB to extract content,
    error messages, and status messages.

    Parameters
    ----------
    message_id : str
        The agent-message ID that carries the task.
    state : TaskState
        The state to notify about.  Must match what is persisted in the DB.
    room_id : str
        Room to broadcast the SSE to.
    user_id : str
        Owner of the task (currently unused but kept for future audit).
    error : str | None
        Optional override for the error text.  When *None*, the function
        extracts the error from the persisted Task's ``status.message``.
    send_processing_status : bool
        When *True*, also broadcasts a ``processing_status`` SSE for
        terminal states.  Set to *True* only from the webhook path which
        lacks a queue-level sender; defaults to *False* for inline callers
        that already have a queue-level ``send_processing_status`` call.

    Returns
    -------
    bool
        *True* if the notification was sent; *False* if it was a duplicate
        or the message has no task tracking.
    """
    state_value = state.value if hasattr(state, "value") else str(state)

    # --- Idempotency check ------------------------------------------------
    # If update_last_notified_state fails (DB error), we proceed with the
    # notification anyway — a duplicate SSE is harmless whereas a missed SSE
    # causes a stuck bubble in the UI.
    try:
        is_new = await db_service.update_last_notified_state(
            message_id, state_value
        )
    except Exception:
        logger.warning(
            "notify_task_update: update_last_notified_state failed for %s; "
            "proceeding with notification to avoid stuck bubble",
            message_id,
            exc_info=True,
        )
        is_new = True

    if not is_new:
        logger.debug(
            "Skipping duplicate notification for task %s state %s",
            message_id,
            state_value,
        )
        return False

    # --- Load the message from DB -----------------------------------------
    room_agent_message = None
    for attempt in range(3):
        room_agent_message = (
            await db_service.get_room_agent_message_by_message_id(message_id)
        )
        if room_agent_message and room_agent_message.has_task_tracking:
            break
        if attempt < 2:
            await asyncio.sleep(0.5)

    if not room_agent_message or not room_agent_message.has_task_tracking:
        logger.debug(
            "notify_task_update: message %s has no task tracking; skipping",
            message_id,
        )
        return False

    task: Task | None = (
        room_agent_message.message_content.message_task
        if room_agent_message.message_content
        else None
    )

    # --- Extract content / error / flags from the persisted task ----------
    content = None
    resolved_error = error
    requires_input = False
    requires_auth = False
    status_message = None

    if task:
        if state == TaskState.completed and task.artifacts:
            content = extract_text_from_artifacts(task.artifacts)
            if not content:
                for i, artifact in enumerate(task.artifacts):
                    logger.warning(
                        "Artifact %d: parts=%d",
                        i,
                        len(artifact.parts) if artifact.parts else 0,
                    )

        elif state == TaskState.failed:
            if not resolved_error:
                resolved_error = extract_error_message(task) or "Task failed"

        elif state == TaskState.rejected:
            if not resolved_error:
                resolved_error = (
                    extract_error_message(task)
                    or "Task was rejected by the agent"
                )

        elif state == TaskState.canceled:
            if not resolved_error:
                resolved_error = "Task was canceled"

        elif state == TaskState.input_required:
            requires_input = True
            status_message = extract_status_message(task)

        elif state == TaskState.auth_required:
            requires_auth = True
            status_message = (
                extract_status_message(task) or "Authentication required"
            )

    # --- Write-side: artifact backfill + message_text backfill ------------
    if room_agent_message.message_content and task:
        existing_text = room_agent_message.message_content.message_text
        if (
            state == TaskState.completed
            and existing_text
            and (not task.artifacts or len(task.artifacts) == 0)
        ):
            task = Task(
                id=task.id,
                context_id=task.context_id,
                status=task.status,
                history=task.history,
                metadata=task.metadata,
                artifacts=[
                    Artifact(
                        artifact_id=str(uuid.uuid4()),
                        name="response",
                        parts=[Part(root=TextPart(text=existing_text))],
                    )
                ],
            )
            room_agent_message.message_content.message_task = task
            content = extract_text_from_artifacts(task.artifacts)
            logger.info(
                "Task %s: Populated artifacts from message_text for A2A compliance",
                message_id,
            )

        if not room_agent_message.message_content.message_text:
            if content:
                room_agent_message.message_content.message_text = content
            elif resolved_error:
                room_agent_message.message_content.message_text = resolved_error
            elif status_message:
                room_agent_message.message_content.message_text = status_message

        update_ok = await db_service.update_room_agent_message_by_message_id(
            room_agent_message.message_id, room_agent_message
        )
        if not update_ok:
            logger.error(
                "Failed to update room agent message %s for task",
                room_agent_message.message_id,
            )

    # --- Resolve agent_name from room's agent set -------------------------
    agent_name: str | None = None
    agent_id = room_agent_message.agent_id
    if agent_id:
        try:
            room = await db_service.get_room_by_room_id(room_id)
            if room and room.room_agent_set:
                agent_name = room.room_agent_set.get(agent_id)
        except Exception:
            logger.warning(
                "notify_task_update: failed to resolve agent_name for %s",
                message_id,
                exc_info=True,
            )

    # --- Resolve remaining metadata from the message ----------------------
    created_at: str | None = None
    if room_agent_message.task_created_at:
        created_at = room_agent_message.task_created_at.isoformat()

    task_content = room_agent_message.task_content

    # --- Send the SSE -----------------------------------------------------
    await notification_service.send_task_update(
        room_id=room_id,
        message_id=message_id,
        status=state,
        content=content,
        error=resolved_error,
        requires_input=requires_input,
        requires_auth=requires_auth,
        status_message=status_message,
        agent_name=agent_name,
        agent_id=agent_id,
        related_message_id=room_agent_message.related_message_id,
        created_at=created_at,
        step_number=room_agent_message.step_number,
        total_steps=room_agent_message.total_steps,
        task_content=task_content,
    )

    logger.info("Sent SSE notification for task %s state %s", message_id, state)

    if send_processing_status and is_terminal_state(state):
        await sse_manager.send_processing_status(room_id, state, message_id)

    return True
