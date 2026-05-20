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


__all__ = ["build_user_text_message"]
