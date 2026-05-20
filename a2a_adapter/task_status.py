from __future__ import annotations

from uuid import uuid4

from typing import Any

from a2a.types import Message, Role, TaskState, TaskStatus, TextPart


def build_failed_task_status(error_text: str) -> TaskStatus:
    return TaskStatus(
        state=TaskState.failed,
        message=Message(
            role=Role.agent,
            parts=[TextPart(text=error_text)],
            message_id=str(uuid4()),
        ),
    )


def coerce_task_state(state: Any) -> Any:
    if isinstance(state, str):
        return TaskState(state)
    return state


__all__ = ["build_failed_task_status", "coerce_task_state"]
