from __future__ import annotations

from typing import Any
from uuid import uuid4

from a2a.types import Message, Part, Role, Task, TextPart

from common import types as internal


def build_user_text_message(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> Message:
    return Message(
        message_id=uuid4().hex,
        role="user",
        parts=[TextPart(text=text)],
        metadata=metadata,
    )


def build_message_from_parts(
    *,
    role: Any,
    message_id: str | None,
    parts: list[Any],
    kind: str = "message",
) -> Message:
    return Message(
        kind=kind,
        role=role,
        message_id=message_id or uuid4().hex,
        parts=parts,
    )


def build_agent_text_message(
    text: str,
    *,
    message_id: str | None = None,
) -> Message:
    return Message(
        message_id=message_id or uuid4().hex,
        role=Role.agent,
        parts=[Part(root=TextPart(kind="text", text=text))],
    )


# ---------------------------------------------------------------------------
# Boundary converters: internal ↔ SDK
# ---------------------------------------------------------------------------


def to_sdk_message(msg: Any) -> Message:
    """Convert an internal common.types.Message (or dict) to an a2a-sdk Message.

    This is the canonical boundary conversion: internal models are serialized
    to dict then validated by the SDK so Pydantic type checks pass cleanly.
    """
    if isinstance(msg, Message):
        return msg
    if isinstance(msg, dict):
        data = msg
    elif hasattr(msg, "model_dump"):
        data = msg.model_dump(mode="json", by_alias=True)
    else:
        data = dict(msg)
    if "message_id" not in data and "messageId" not in data:
        data["message_id"] = uuid4().hex
    return Message.model_validate(data)


def to_sdk_task(task: Any) -> Task:
    """Convert an internal common.types.Task (or dict) to an a2a-sdk Task."""
    if isinstance(task, Task):
        return task
    if isinstance(task, dict):
        data = task
    elif hasattr(task, "model_dump"):
        data = task.model_dump(mode="json", by_alias=True)
    else:
        data = dict(task)
    return Task.model_validate(data)


def from_sdk_message(msg: Any) -> internal.Message:
    """Convert an a2a-sdk Message (or dict) to an internal Message."""
    if isinstance(msg, internal.Message):
        return msg
    if isinstance(msg, dict):
        data = msg
    elif hasattr(msg, "model_dump"):
        data = msg.model_dump(mode="json")
    else:
        data = dict(msg)
    return internal.Message.model_validate(data)


def from_sdk_task(task: Any) -> internal.Task:
    """Convert an a2a-sdk Task (or dict) to an internal Task."""
    if isinstance(task, internal.Task):
        return task
    if isinstance(task, dict):
        data = task
    elif hasattr(task, "model_dump"):
        data = task.model_dump(mode="json")
    else:
        data = dict(task)
    return internal.Task.model_validate(data)


__all__ = [
    "build_agent_text_message",
    "build_message_from_parts",
    "build_user_text_message",
    "from_sdk_message",
    "from_sdk_task",
    "to_sdk_message",
    "to_sdk_task",
]
