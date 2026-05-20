from __future__ import annotations

from typing import Any

from a2a.types import Message, TextPart


def build_user_text_message(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> Message:
    return Message(role="user", parts=[TextPart(text=text)], metadata=metadata)


__all__ = ["build_user_text_message"]
