"""A2A task builders for tests that need realistic remote task payloads."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from common.types import Message, MessageRole, Task, TaskState, TaskStatus, TextPart
from common.utils.time import utcnow


def build_task_status(state: Any) -> TaskStatus:
    value = getattr(state, "value", state)
    return TaskStatus(state=TaskState(value) if isinstance(value, str) else state)


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
        role=MessageRole.AGENT,
        parts=[TextPart(text=text)],
        context_id=task_context_id,
        metadata=metadata or {},
    )
    return Task(
        id=task_id,
        context_id=task_context_id,
        status=TaskStatus(
            state=TaskState.completed,
            timestamp=utcnow().isoformat(),
            message=message,
        ),
        history=[message],
    )


def build_failed_text_task(
    *,
    task_id: str,
    error_text: str,
    context_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Task:
    task_context_id = context_id or task_id
    message = Message(
        message_id=uuid4().hex,
        role=MessageRole.AGENT,
        parts=[TextPart(text=error_text)],
        context_id=task_context_id,
        metadata=metadata or {},
    )
    return Task(
        id=task_id,
        context_id=task_context_id,
        status=TaskStatus(
            state=TaskState.failed,
            timestamp=utcnow().isoformat(),
            message=message,
        ),
    )
