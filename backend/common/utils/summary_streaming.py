"""Private token collection for HYBRO AI summary / synthesis messages."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class SummaryDeliveryContext(Protocol):
    """Compatibility boundary for callers that pass public delivery context.

    Summary token streams are private. Callers publish only the persisted,
    completed summary through their terminal delivery path.
    """

    async def send_artifact_update(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        artifact: object,
        append: bool = False,
        last_chunk: bool = False,
        client_request_id: str | None = None,
    ) -> None: ...


SummarySSEEmitter = SummaryDeliveryContext
"""Backward-compatible alias; summary collection no longer emits SSE artifacts."""


def summary_stream_artifact_id(message_id: str) -> str:
    """Return the legacy streaming artifact id; new summary collection does not emit it."""
    return f"{message_id}-stream"


async def stream_summary_to_sse(
    sse_manager: SummaryDeliveryContext,
    *,
    room_id: str,
    message_id: str,
    agent_id: str,
    token_stream: AsyncIterator[str],
    client_request_id: str | None = None,
) -> str:
    """Collect private summary tokens; callers emit only completed summaries."""
    # Public delivery parameters are retained for call-site compatibility.
    _ = (sse_manager, room_id, message_id, agent_id, client_request_id)

    parts: list[str] = []
    async for token in token_stream:
        if not token:
            continue
        parts.append(token)

    return "".join(parts)
