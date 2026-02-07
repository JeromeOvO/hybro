"""
A2A Webhook Handler

This module handles webhook callbacks from A2A agents for long-running tasks.

Per A2A spec section 4.3.3, push notifications use StreamResponse format:
- {"task": Task} - Full task with artifacts (most common)
- {"statusUpdate": TaskStatusUpdateEvent} - Status-only update
- {"artifactUpdate": TaskArtifactUpdateEvent} - Artifact streaming
- {"message": Message} - Direct message response
"""

import asyncio
import uuid
from typing import Any

from a2a.types import Artifact, Part, Task, TaskStatusUpdateEvent, TextPart
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from common.utils.logger import get_logger
from services.a2a_constants import INTERACTIVE_STATES, is_terminal_state
from services.database_service import db_service
from services.sse_services import sse_manager

logger = get_logger(__name__)

router = APIRouter()


async def resume_queue_continuation(
    message_id: str,
    task_result_text: str | None = None,
) -> None:
    """
    Resume queue processing after a push notification task completes.

    This is called as a background task when a webhook indicates a task
    has reached a terminal state.

    Args:
        message_id: The message ID of the completed task
        task_result_text: The result text from the completed task
    """
    # Import here to avoid circular imports
    from api.orchestration_center import orchestration_center

    try:
        resumed = await orchestration_center.resume_queue_from_continuation(
            message_id, task_result_text
        )
        if resumed:
            logger.info(f"Successfully resumed queue processing for task {message_id}")
    except Exception as e:
        logger.error(
            f"Failed to resume queue for task {message_id}: {e}",
            exc_info=True,
        )


async def notify_task_update(
    message_id: str,
    task: Task,
    room_id: str,
    user_id: str,
    related_message_id: str | None = None,
    agent_name: str | None = None,
    agent_id: str | None = None,
    created_at: str | None = None,
    step_number: int | None = None,
    total_steps: int | None = None,
) -> None:
    """
    Send SSE notification when task state changes.
    Uses idempotency tracking to prevent duplicate notifications.

    Args:
        message_id: The message ID (used for task tracking)
        task: The updated A2A Task
        room_id: Room to notify
        user_id: User who owns the task
        created_at: Task creation timestamp (for consistent ordering)
        step_number: Current step number in the workflow (1-indexed)
        total_steps: Total number of steps in the workflow
    """
    state = task.status.state

    # Check if we already notified for this state (prevents duplicates)
    is_new_notification = await db_service.update_last_notified_state(
        message_id, state.value if hasattr(state, "value") else str(state)
    )
    if not is_new_notification:
        logger.debug(
            f"Skipping duplicate notification for task {message_id} state {state}"
        )
        return

    content = None
    error = None
    requires_input = False
    requires_auth = False
    status_message = None

    state_value = state.value if hasattr(state, "value") else str(state)

    if state_value == "completed" and task.artifacts:
        content = extract_text_from_artifacts(task.artifacts)
        logger.info(
            f"Task {message_id} completed. Artifacts count: {len(task.artifacts)}, "
            f"Extracted content length: {len(content) if content else 0}"
        )
        if not content:
            # Debug: log artifact structure
            for i, artifact in enumerate(task.artifacts):
                logger.warning(
                    f"Artifact {i}: parts={len(artifact.parts) if artifact.parts else 0}"
                )
                if artifact.parts:
                    for j, part in enumerate(artifact.parts):
                        logger.warning(
                            f"  Part {j}: type={type(part).__name__}, "
                            f"has_text={hasattr(part, 'text')}, "
                            f"has_root={hasattr(part, 'root')}"
                        )

    elif state_value == "failed":
        error = extract_error_message(task) or "Task failed"

    elif state_value == "rejected":
        error = extract_error_message(task) or "Task was rejected by the agent"

    elif state_value == "canceled":
        error = "Task was canceled"

    elif state_value == "input_required":
        requires_input = True
        status_message = extract_status_message(task)

    elif state_value == "auth_required":
        requires_auth = True
        status_message = extract_status_message(task) or "Authentication required"

    # Persist task updates into the room agent message (for refresh rendering)
    # Use retry logic to handle race condition where webhook arrives before
    # OrchestrationCenter finishes persisting task tracking to the message
    room_agent_message = None
    for attempt in range(3):
        room_agent_message = await db_service.get_room_agent_message_by_message_id(
            message_id
        )
        if room_agent_message and room_agent_message.has_task_tracking:
            break
        if attempt < 2:
            await asyncio.sleep(0.5)  # Wait 500ms before retry

    if room_agent_message and room_agent_message.message_content:
        # Ensure task has artifacts when completed (A2A compliance)
        # Some agents send statusUpdate without artifacts; we need to populate them
        # from message_text if available
        existing_text = room_agent_message.message_content.message_text
        if (
            state_value == "completed"
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
            logger.info(
                f"Task {message_id}: Populated artifacts from message_text for A2A compliance"
            )

        room_agent_message.message_content.message_task = task

        # Only backfill message_text if it's empty to avoid overwriting real content
        if not room_agent_message.message_content.message_text:
            if content:
                room_agent_message.message_content.message_text = content
            elif error:
                room_agent_message.message_content.message_text = error
            elif status_message:
                room_agent_message.message_content.message_text = status_message

        update_success = await db_service.update_room_agent_message_by_message_id(
            room_agent_message.message_id, room_agent_message
        )
        if not update_success:
            logger.error(
                "Failed to update room agent message %s for task",
                room_agent_message.message_id,
            )

    # Extract task_content from the room agent message for frontend display
    task_content = None
    if room_agent_message and room_agent_message.message_content:
        task_content = room_agent_message.message_content.message_text

    await sse_manager.send_task_update(
        room_id=room_id,
        message_id=message_id,
        status=state_value,
        content=content,
        error=error,
        requires_input=requires_input,
        requires_auth=requires_auth,
        status_message=status_message,
        agent_name=agent_name,
        agent_id=agent_id,
        related_message_id=related_message_id,
        created_at=created_at,
        step_number=step_number,
        total_steps=total_steps,
        task_content=task_content,
    )

    logger.info(f"Sent SSE notification for task {message_id} state {state_value}")

    # Clear room processing status when task reaches a terminal state via webhook.
    # This ensures the "Working... Processing your request..." bubble is dismissed
    # even when the task completes through the webhook path rather than streaming.
    if state_value in ("completed", "failed", "canceled", "rejected"):
        await sse_manager.send_processing_status(room_id, state_value, message_id)


def extract_text_from_artifacts(artifacts: list) -> str | None:
    """Extract text content from A2A artifacts with robust type handling."""
    texts = []
    for artifact in artifacts:
        if not artifact.parts:
            continue
        for part in artifact.parts:
            # Handle different part type structures
            text = None
            if hasattr(part, "text") and part.text:
                text = part.text
            elif hasattr(part, "root"):
                # Discriminated union wrapper
                root = part.root
                if hasattr(root, "text") and root.text:
                    text = root.text
            if text:
                texts.append(text)
    return "".join(texts) if texts else None


def extract_error_message(task: Task) -> str | None:
    """Extract error message from task status."""
    if not task.status.message:
        return None
    if not task.status.message.parts:
        return None
    for part in task.status.message.parts:
        if hasattr(part, "text") and part.text:
            return part.text
        if hasattr(part, "root") and hasattr(part.root, "text"):
            return part.root.text
    return None


def extract_status_message(task: Task) -> str | None:
    """Extract human-readable status message."""
    return extract_error_message(task)  # Same extraction logic


@router.post("/webhooks/a2a/{message_id}")
async def handle_a2a_webhook(
    request: Request,
    message_id: str,
    background_tasks: BackgroundTasks,
    authorization: str = Header(default=""),
) -> dict[str, Any]:
    """
    Receive task updates from A2A agents.

    Per A2A spec section 4.3.3, payload is StreamResponse format containing one of:
    - task: Full Task object with artifacts
    - statusUpdate: TaskStatusUpdateEvent for status-only updates
    - artifactUpdate: TaskArtifactUpdateEvent for streaming artifacts
    - message: Message object for direct responses

    Security: Validates Bearer token against stored hash.
    Idempotency: Safe to call multiple times with same status.

    Args:
        request: FastAPI request object
        message_id: The message ID (used for task tracking)
        background_tasks: FastAPI background tasks
        authorization: Authorization header with Bearer token

    Returns:
        Status response
    """
    # 1. Extract and validate token (hash-based, not plaintext comparison)
    token = authorization.replace("Bearer ", "") if authorization else ""

    if not token:
        logger.warning(f"Webhook for task {message_id}: Missing authorization token")
        raise HTTPException(status_code=401, detail="Missing authorization token")

    is_valid, error_reason = await db_service.verify_webhook_token_for_task(
        message_id, token
    )
    if not is_valid:
        if error_reason == "task_not_found":
            logger.warning(
                f"Webhook for task {message_id}: Task not found (may be race condition)"
            )
            raise HTTPException(
                status_code=404,
                detail="Task not found. The task may not have been created yet.",
            )
        elif error_reason == "invalid_token":
            logger.warning(f"Webhook for task {message_id}: Invalid token")
            raise HTTPException(status_code=401, detail="Invalid token")
        else:
            logger.error(
                f"Webhook for task {message_id}: Token verification error: {error_reason}"
            )
            raise HTTPException(status_code=500, detail="Token verification failed")

    # 2. Parse StreamResponse payload (A2A-compliant format)
    try:
        payload = await request.json()
        logger.info(
            f"Webhook for task {message_id}: Received payload keys: {list(payload.keys())}"
        )
        updated_task = parse_stream_response(payload, message_id)
        logger.info(
            f"Webhook for task {message_id}: Parsed task state={updated_task.status.state}, "
            f"artifacts={len(updated_task.artifacts) if updated_task.artifacts else 0}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Invalid webhook payload for task {message_id}: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}") from e

    # 3. Get current message to check state
    current_msg = await db_service.get_room_agent_message_by_message_id(message_id)
    if not current_msg or not current_msg.has_task_tracking:
        logger.warning(f"Webhook for unknown task {message_id}")
        raise HTTPException(status_code=404, detail="Task not found")

    # 4. Don't update if already terminal (idempotency)
    current_task = (
        current_msg.message_content.message_task
        if current_msg.message_content
        else None
    )
    if current_task:
        current_state = current_task.status.state
        if is_terminal_state(current_state):
            logger.debug(
                f"Webhook for task {message_id}: Already terminal ({current_state})"
            )
            return {
                "status": "already_terminal",
                "state": current_state.value
                if hasattr(current_state, "value")
                else str(current_state),
            }

    # 5. Update task on the message
    update_success = await db_service.update_task_on_message(
        message_id, updated_task.model_dump(mode="json")
    )
    if not update_success:
        logger.error(
            f"Webhook for task {message_id}: Failed to update task in database"
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to update task status in database",
        )
    logger.info(
        f"Webhook updated task {message_id} to state {updated_task.status.state}"
    )

    # 6. Notify frontend via SSE (with idempotency check)
    new_state = updated_task.status.state
    should_notify = is_terminal_state(new_state) or new_state in INTERACTIVE_STATES

    # Extract content for queue resumption (if task completed successfully)
    task_result_text = None
    if is_terminal_state(new_state):
        state_value = new_state.value if hasattr(new_state, "value") else str(new_state)
        if state_value == "completed" and updated_task.artifacts:
            task_result_text = extract_text_from_artifacts(updated_task.artifacts)

    if should_notify:
        # Get created_at from message for consistent ordering
        created_at = None
        if current_msg.task_created_at:
            created_at = current_msg.task_created_at.isoformat()

        # Look up agent name from room's agent set
        agent_name = None
        if current_msg.agent_id:
            room = await db_service.get_room_by_room_id(current_msg.room_id)
            if room and room.room_agent_set:
                agent_name = room.room_agent_set.get(current_msg.agent_id)

        background_tasks.add_task(
            notify_task_update,
            message_id=message_id,
            task=updated_task,
            room_id=current_msg.room_id,
            user_id=current_msg.user_id or "",
            related_message_id=current_msg.related_message_id,
            agent_name=agent_name,
            agent_id=current_msg.agent_id,
            created_at=created_at,
            step_number=current_msg.step_number,
            total_steps=current_msg.total_steps,
        )

    # 7. Resume queue processing if task reached terminal state and has continuation
    if is_terminal_state(new_state):
        background_tasks.add_task(
            resume_queue_continuation,
            message_id=message_id,
            task_result_text=task_result_text,
        )

    return {"status": "accepted"}


def parse_stream_response(payload: dict[str, Any], message_id: str) -> Task:
    """
    Parse A2A StreamResponse format into a Task object.

    Per A2A spec section 4.3.3, StreamResponse is a discriminated union with one of:
    - task: Full Task object (preferred, includes artifacts)
    - statusUpdate: TaskStatusUpdateEvent (status only, no artifacts)
    - artifactUpdate: TaskArtifactUpdateEvent (for streaming)
    - message: Message object

    Args:
        payload: The webhook payload in StreamResponse format
        message_id: The message ID (for logging)

    Returns:
        Task object parsed from the payload

    Raises:
        HTTPException: If payload format is invalid
    """
    # Handle "task" variant (full Task with artifacts) - preferred
    if "task" in payload:
        logger.debug(
            f"Webhook for task {message_id}: Received full Task in StreamResponse"
        )
        return Task.model_validate(payload["task"])

    # Handle "statusUpdate" variant (TaskStatusUpdateEvent - status only)
    if "statusUpdate" in payload:
        logger.debug(
            f"Webhook for task {message_id}: Received statusUpdate in StreamResponse"
        )
        status_event = TaskStatusUpdateEvent.model_validate(payload["statusUpdate"])
        # Convert to Task object (note: no artifacts in status-only update)
        return Task(
            id=status_event.taskId,
            context_id=status_event.contextId,
            status=status_event.status,
        )

    # Handle "message" variant - convert to completed task
    if "message" in payload:
        logger.debug(
            f"Webhook for task {message_id}: Received message in StreamResponse"
        )
        import uuid

        from a2a.types import Artifact, Message, TaskState, TaskStatus

        message = Message.model_validate(payload["message"])
        return Task(
            id=str(uuid.uuid4()),
            context_id=message.context_id or "",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="response",
                    parts=message.parts,
                )
            ],
        )

    # Handle "artifactUpdate" variant - not fully supported yet
    if "artifactUpdate" in payload:
        logger.warning(
            f"Webhook for task {message_id}: artifactUpdate not fully supported, "
            "client should poll for full task"
        )
        raise HTTPException(
            status_code=400,
            detail="artifactUpdate not supported; send full task or use polling",
        )

    # Fallback: Try parsing as raw Task (backwards compatibility)
    # This handles non-compliant agents that send Task directly
    if "id" in payload and "status" in payload:
        logger.warning(
            f"Webhook for task {message_id}: Received raw Task (not StreamResponse). "
            "Agent should send StreamResponse format per A2A spec."
        )
        return Task.model_validate(payload)

    raise HTTPException(
        status_code=400,
        detail="Invalid StreamResponse: expected 'task', 'statusUpdate', 'message', or 'artifactUpdate' key",
    )
