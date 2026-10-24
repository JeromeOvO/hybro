"""The public HTTP and SSE contract names the conversation ``conversation_id``.

``room_id`` survives only as the stored Mongo key and internal transport key; it
must not appear as an API schema field, parameter, or SSE frame field.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from common.dto import (
    AgentMessageFinal,
    AgentMessagePartial,
    ErrorEvent,
    ProcessingStatusEvent,
    TaskSubmittedEvent,
)
from delivery.sse.connection import SSEConnection
from delivery.translator import to_sse_frame

NOW = datetime(2026, 1, 1, tzinfo=UTC)


_LEGACY_NAMES = frozenset({"room_id", "room_ids"})


def _room_id_paths(value: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _LEGACY_NAMES or (
                key == "name" and isinstance(item, str) and item in _LEGACY_NAMES
            ):
                hits.append(f"{path}.{key}")
            hits.extend(_room_id_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str) and item in _LEGACY_NAMES:
                hits.append(f"{path}[{index}]")
            hits.extend(_room_id_paths(item, f"{path}[{index}]"))
    return hits


def test_openapi_snapshot_has_no_room_id_or_room_ids_field_or_parameter() -> None:
    openapi = json.loads(Path("openapi.json").read_text())

    assert _room_id_paths(openapi) == []
    assert not [path for path in openapi["paths"] if "{room_id}" in path]


def test_sse_frames_name_the_conversation_conversation_id() -> None:
    events = [
        ProcessingStatusEvent(
            conversation_id="room-1", message_id="msg-1", status="processing"
        ),
        AgentMessagePartial(
            conversation_id="room-1",
            message_id="msg-1",
            agent_id="agent-1",
            content_delta="hi",
        ),
        AgentMessageFinal(
            conversation_id="room-1",
            message_id="msg-1",
            agent_id="agent-1",
            content={"content": "hi"},
        ),
        TaskSubmittedEvent(
            conversation_id="room-1",
            message_id="msg-1",
            task_id="task-1",
            agent_name="Agent",
        ),
        ErrorEvent(conversation_id="room-1", error="boom"),
    ]

    for event in events:
        frame = to_sse_frame(event, timestamp=NOW, room_seq=1)
        assert frame["conversation_id"] == "room-1"
        assert _room_id_paths(frame) == []


@pytest.mark.asyncio
async def test_connection_frames_name_the_conversation_conversation_id() -> None:
    connection = SSEConnection(
        conversation_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=0.01,
        now=lambda: NOW,
    )

    assert await connection.send_message("custom", {"x": 1})
    sent = await connection.next_frame()
    heartbeat = await connection.next_frame(timeout=0.01)

    for frame in (sent, heartbeat):
        assert frame["conversation_id"] == "room-1"
        assert _room_id_paths(frame) == []
