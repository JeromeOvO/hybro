"""
A2A Webhook Handler

This module handles webhook callbacks from A2A agents for long-running tasks.

Per A2A spec section 4.3.3, push notifications use StreamResponse format:
- {"task": Task} - Full task with artifacts (most common)
- {"statusUpdate": TaskStatusUpdateEvent} - Status-only update
- {"artifactUpdate": TaskArtifactUpdateEvent} - Artifact streaming
- {"message": Message} - Direct message response
"""

from typing import Any

from a2a.types import (
    Artifact,
    Message,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from common.utils.a2a_helpers import extract_parts_from_artifacts, extract_text_from_artifacts
from common.utils.logger import get_logger
from modules.RoomMessageCenter import room_message_center
from services.a2a_constants import INTERACTIVE_STATES, is_terminal_state
from services.database_service import db_service
from services.task_notification_service import notify_task_update

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
    try:
        resumed = await room_message_center.resume_queue_from_continuation(
            message_id, task_result_text
        )
        if resumed:
            logger.info(f"Successfully resumed queue processing for task {message_id}")
    except Exception as e:
        logger.error(
            f"Failed to resume queue for task {message_id}: {e}",
            exc_info=True,
        )


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
    task_result_parts = None
    if is_terminal_state(new_state):
        if new_state == TaskState.completed and updated_task.artifacts:
            extracted = extract_parts_from_artifacts(updated_task.artifacts)
            task_result_text = extracted.text if extracted.text else None
            task_result_parts = (
                (extracted.file_parts + extracted.data_parts)
                if extracted.has_non_text
                else None
            )

    if should_notify:
        background_tasks.add_task(
            notify_task_update,
            message_id=message_id,
            state=new_state,
            room_id=current_msg.room_id,
            user_id=current_msg.user_id or "",
            send_processing_status=True,
            parts=task_result_parts,
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
            id=status_event.task_id,
            context_id=status_event.context_id,
            status=status_event.status,
        )

    # Handle "message" variant - convert to completed task
    if "message" in payload:
        logger.debug(
            f"Webhook for task {message_id}: Received message in StreamResponse"
        )
        import uuid

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
