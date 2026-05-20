"""
A2A Task API Endpoints

This module provides REST API endpoints for querying long-running A2A tasks.
Tasks are now stored on room_agent_messages (consolidated from separate a2a_tasks collection).
"""

from typing import Any

from a2a.types import TaskState
from fastapi import APIRouter, Depends, HTTPException
from fastapi.params import Depends as DependsParam

from app_shell.database_service import A2ATaskReader
from common.auth import ClerkUser, get_current_user
from common.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
db_service: A2ATaskReader | None = None

PENDING_STATES = {TaskState.submitted, TaskState.working}
INTERACTIVE_STATES = {TaskState.input_required, TaskState.auth_required}
TERMINAL_STATES = {
    TaskState.completed,
    TaskState.failed,
    TaskState.canceled,
    TaskState.rejected,
}
NON_TERMINAL_STATES = PENDING_STATES | INTERACTIVE_STATES


def get_retry_after_seconds(state: TaskState) -> int | None:
    if state in TERMINAL_STATES:
        return None
    if state in INTERACTIVE_STATES:
        return 60
    return 30


def bind_a2a_task_dependencies(database_service: A2ATaskReader) -> None:
    global db_service

    db_service = database_service


def get_db_service() -> A2ATaskReader:
    if db_service is None:
        raise RuntimeError("A2A task database dependency has not been bound")
    return db_service


def _resolve_dependency(value: Any, provider) -> Any:
    if isinstance(value, DependsParam):
        return provider()
    return value


@router.get("/a2a-tasks/{message_id}")
async def get_task_status(
    message_id: str,
    current_user: ClerkUser = Depends(get_current_user),
    db: A2ATaskReader = Depends(get_db_service),
) -> dict[str, Any]:
    """
    Get the status of a long-running A2A task.

    Args:
        message_id: The message ID (used for task tracking)
        current_user: Authenticated user

    Returns:
        Task status with optional retry_after_seconds hint
    """
    db = _resolve_dependency(db, get_db_service)
    msg = await db.get_room_agent_message_by_message_id(message_id)
    if not msg or not msg.has_task_tracking:
        raise HTTPException(status_code=404, detail="Task not found")

    # Verify user owns this task
    if msg.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    task = msg.message_content.message_task if msg.message_content else None
    if not task:
        raise HTTPException(status_code=404, detail="Task data not found")

    state = task.status.state
    state_value = state.value if hasattr(state, "value") else str(state)

    return {
        "message_id": message_id,
        "status": state_value,
        "task": task.model_dump(mode="json"),
        "agent_name": None,  # Can be looked up from agent_id if needed
        "agent_id": msg.agent_id,
        "related_message_id": msg.related_message_id,
        "created_at": msg.task_created_at.isoformat() if msg.task_created_at else None,
        "updated_at": msg.task_updated_at.isoformat() if msg.task_updated_at else None,
        "retry_after_seconds": get_retry_after_seconds(state),
    }


@router.get("/rooms/{room_id}/a2a-tasks")
async def list_room_tasks(
    room_id: str,
    limit: int = 50,
    current_user: ClerkUser = Depends(get_current_user),
    db: A2ATaskReader = Depends(get_db_service),
) -> dict[str, Any]:
    """
    List all A2A tasks for a room.

    Args:
        room_id: The room ID
        limit: Maximum number of tasks to return
        current_user: Authenticated user

    Returns:
        List of tasks for the room
    """
    # Get task messages for room
    db = _resolve_dependency(db, get_db_service)
    messages = await db.get_task_messages_for_room(room_id, limit=limit)

    # Filter to only tasks owned by this user (or room members in future)
    user_messages = [m for m in messages if m.user_id == current_user.user_id]

    return {
        "tasks": [
            {
                "message_id": m.message_id,
                "task_id": m.message_content.message_task.id
                if m.message_content and m.message_content.message_task
                else None,
                "agent_name": None,  # Can be looked up from agent_id if needed
                "agent_id": m.agent_id,
                "related_message_id": m.related_message_id,
                "status": (
                    m.message_content.message_task.status.state.value
                    if m.message_content
                    and m.message_content.message_task
                    and hasattr(m.message_content.message_task.status.state, "value")
                    else str(m.message_content.message_task.status.state)
                    if m.message_content and m.message_content.message_task
                    else "unknown"
                ),
                "created_at": m.task_created_at.isoformat()
                if m.task_created_at
                else None,
                "updated_at": m.task_updated_at.isoformat()
                if m.task_updated_at
                else None,
            }
            for m in user_messages
        ]
    }


@router.get("/users/me/a2a-tasks")
async def list_user_pending_tasks(
    current_user: ClerkUser = Depends(get_current_user),
    db: A2ATaskReader = Depends(get_db_service),
) -> dict[str, Any]:
    """
    List all pending A2A tasks for the current user.

    Args:
        current_user: Authenticated user

    Returns:
        List of pending tasks for the user
    """
    non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]
    db = _resolve_dependency(db, get_db_service)
    messages = await db.get_pending_task_messages_for_user(
        current_user.user_id, non_terminal_state_values
    )

    return {
        "tasks": [
            {
                "message_id": m.message_id,
                "task_id": m.message_content.message_task.id
                if m.message_content and m.message_content.message_task
                else None,
                "room_id": m.room_id,
                "agent_name": None,  # Can be looked up from agent_id if needed
                "related_message_id": m.related_message_id,
                "status": (
                    m.message_content.message_task.status.state.value
                    if m.message_content
                    and m.message_content.message_task
                    and hasattr(m.message_content.message_task.status.state, "value")
                    else str(m.message_content.message_task.status.state)
                    if m.message_content and m.message_content.message_task
                    else "unknown"
                ),
                "created_at": m.task_created_at.isoformat()
                if m.task_created_at
                else None,
                "updated_at": m.task_updated_at.isoformat()
                if m.task_updated_at
                else None,
            }
            for m in messages
        ]
    }
