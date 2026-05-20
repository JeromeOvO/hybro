from __future__ import annotations

from typing import Any
from uuid import uuid4

from a2a.types import Message, TextPart


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


__all__ = ["build_message_from_parts", "build_user_text_message"]
