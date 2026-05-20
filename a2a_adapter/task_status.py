from __future__ import annotations

from uuid import uuid4

from typing import Any

from a2a.types import Message, Role, TaskState, TaskStatus, TextPart


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


def coerce_task_state(state: Any) -> Any:
    if isinstance(state, str):
        return TaskState(state)
    return state


__all__ = ["build_failed_task_status", "build_task_status", "coerce_task_state"]
