from __future__ import annotations

from uuid import uuid4

from typing import Any

from a2a.types import Message, Role, Task, TaskState, TaskStatus, TextPart
from common.utils.time import utcnow


def build_task_status(state: Any, *, error_text: str | None = None) -> TaskStatus:
    status = TaskStatus(state=coerce_task_state(state))
    if error_text:
        status.message = Message(
            role=Role.agent,
            parts=[TextPart(text=error_text)],
            message_id=str(uuid4()),
        )
    return status


def build_failed_task_status(error_text: str) -> TaskStatus:
    return build_task_status(TaskState.failed, error_text=error_text)


def build_completed_text_task(
    *,
    task_id: str,
    text: str,
    context_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Task:
    task_context_id = context_id or task_id
    message = Message(
        message_id=task_id,
        role=Role.agent,
        parts=[TextPart(text=text)],
        context_id=task_context_id,
        metadata=metadata or {},
    )
    status = TaskStatus(
        state=TaskState.completed,
        timestamp=utcnow().isoformat(),
        message=message,
    )
    return Task(
        id=task_id,
        context_id=task_context_id,
        status=status,
        history=[message],
    )


def coerce_task_state(state: Any) -> Any:
    if isinstance(state, str):
        return TaskState(state)
    return state


__all__ = [
    "build_completed_text_task",
    "build_failed_task_status",
    "build_task_status",
    "coerce_task_state",
]
