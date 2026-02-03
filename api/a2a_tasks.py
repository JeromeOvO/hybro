"""
A2A Task API Endpoints

This module provides REST API endpoints for querying long-running A2A tasks.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from common.auth import ClerkUser, get_current_user
from common.utils.logger import get_logger
from services.a2a_constants import get_retry_after_seconds
from services.a2a_task_service import get_a2a_task_service

logger = get_logger(__name__)

router = APIRouter()


@router.get("/a2a-tasks/{internal_id}")
async def get_task_status(
    internal_id: str,
    current_user: ClerkUser = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Get the status of a long-running A2A task.

    Args:
        internal_id: Our internal task ID
        current_user: Authenticated user

    Returns:
        Task status with optional retry_after_seconds hint
    """
    task_service = get_a2a_task_service()

    task_doc = await task_service.get_task(internal_id)
    if not task_doc:
        raise HTTPException(status_code=404, detail="Task not found")

    # Verify user owns this task
    if task_doc["user_id"] != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    task = task_doc["task"]
    state = task.status.state
    state_value = state.value if hasattr(state, "value") else str(state)

    return {
        "internal_id": internal_id,
        "status": state_value,
        "task": task.model_dump(mode="json"),
        "agent_name": task_doc.get("agent_name"),
        "agent_id": task_doc.get("agent_id"),
        "related_message_id": task_doc.get("related_message_id"),
        "created_at": task_doc["created_at"].isoformat(),
        "updated_at": task_doc["updated_at"].isoformat(),
        "retry_after_seconds": get_retry_after_seconds(state),
    }


@router.get("/rooms/{room_id}/a2a-tasks")
async def list_room_tasks(
    room_id: str,
    limit: int = 50,
    current_user: ClerkUser = Depends(get_current_user),
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
    task_service = get_a2a_task_service()

    # Get tasks for room
    tasks = await task_service.get_tasks_for_room(room_id, limit=limit)

    # Filter to only tasks owned by this user (or room members in future)
    user_tasks = [t for t in tasks if t["user_id"] == current_user.user_id]

    return {
        "tasks": [
            {
                "internal_id": t["internal_id"],
                "task_id": t["task"].id,
                "agent_name": t.get("agent_name"),
                "agent_id": t.get("agent_id"),
                "related_message_id": t.get("related_message_id"),
                "status": (
                    t["task"].status.state.value
                    if hasattr(t["task"].status.state, "value")
                    else str(t["task"].status.state)
                ),
                "created_at": t["created_at"].isoformat(),
                "updated_at": t["updated_at"].isoformat(),
            }
            for t in user_tasks
        ]
    }


@router.get("/users/me/a2a-tasks")
async def list_user_pending_tasks(
    current_user: ClerkUser = Depends(get_current_user),
) -> dict[str, Any]:
    """
    List all pending A2A tasks for the current user.

    Args:
        current_user: Authenticated user

    Returns:
        List of pending tasks for the user
    """
    task_service = get_a2a_task_service()

    tasks = await task_service.get_pending_tasks_for_user(current_user.user_id)

    return {
        "tasks": [
            {
                "internal_id": t["internal_id"],
                "task_id": t["task"].id,
                "room_id": t["room_id"],
                "agent_name": t.get("agent_name"),
                "related_message_id": t.get("related_message_id"),
                "status": (
                    t["task"].status.state.value
                    if hasattr(t["task"].status.state, "value")
                    else str(t["task"].status.state)
                ),
                "created_at": t["created_at"].isoformat(),
                "updated_at": t["updated_at"].isoformat(),
            }
            for t in tasks
        ]
    }
