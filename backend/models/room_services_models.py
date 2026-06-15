from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParseResult:
    """Result of ``parse_user_message``.

    Replaces the previous ``bool`` return so the caller can distinguish
    cancellation from failure without ``parse_user_message`` sending SSE
    events directly (Issue 20).
    """

    success: bool
    canceled: bool = False
