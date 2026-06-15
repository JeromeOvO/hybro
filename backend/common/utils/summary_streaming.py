"""SSE streaming helpers for HYBRO AI summary / synthesis messages."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class SummarySSEEmitter(Protocol):
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


def summary_stream_artifact_id(message_id: str) -> str:
    return f"{message_id}-stream"


async def stream_summary_to_sse(
    sse_manager: SummarySSEEmitter,
    *,
    room_id: str,
    message_id: str,
    agent_id: str,
    token_stream: AsyncIterator[str],
    client_request_id: str | None = None,
) -> str:
    """Emit ``artifact_update`` chunks for a summary message; return full text."""
    artifact_id = summary_stream_artifact_id(message_id)
    sse_kw: dict = {}
    if client_request_id:
        sse_kw["client_request_id"] = client_request_id

    parts: list[str] = []
    async for token in token_stream:
        if not token:
            continue
        parts.append(token)
        await sse_manager.send_artifact_update(
            room_id=room_id,
            message_id=message_id,
            agent_id=agent_id,
            artifact={
                "artifact_id": artifact_id,
                "parts": [{"kind": "text", "text": token}],
            },
            append=True,
            last_chunk=False,
            **sse_kw,
        )

    full_text = "".join(parts)
    await sse_manager.send_artifact_update(
        room_id=room_id,
        message_id=message_id,
        agent_id=agent_id,
        artifact={
            "artifact_id": artifact_id,
            "parts": [{"kind": "text", "text": ""}],
        },
        append=True,
        last_chunk=True,
        **sse_kw,
    )
    return full_text
