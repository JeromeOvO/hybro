import json
from datetime import UTC, datetime

import pytest

from delivery.sse.connection import SSEConnection

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def fixed_now():
    return NOW


def test_connection_exposes_legacy_public_attributes():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )

    assert connection.room_id == "room-1"
    assert connection.connection_id == "conn-1"
    assert connection.is_active is True


@pytest.mark.asyncio
async def test_send_frame_queues_dict_frame():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )

    frame = {"type": "update", "room_id": "room-1", "data": {"x": 1}}
    assert await connection.send_frame(frame) is True

    assert await connection.queue.get() == frame


@pytest.mark.asyncio
async def test_send_message_builds_legacy_frame_but_keeps_internal_dict():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )

    assert await connection.send_message("agent_response", {"content": "hi"}) is True

    frame = await connection.queue.get()
    assert frame == {
        "type": "agent_response",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {"content": "hi"},
    }


@pytest.mark.asyncio
async def test_send_methods_return_false_when_inactive():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )
    connection.close()

    assert await connection.send_frame({"type": "update"}) is False
    assert await connection.send_message("update", {}) is False


@pytest.mark.asyncio
async def test_get_message_serializes_queued_dict_for_legacy_adapter():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )
    await connection.send_message("ping", {"ok": True})

    raw = await connection.get_message(timeout=0.01)

    assert isinstance(raw, str)
    assert json.loads(raw) == {
        "type": "ping",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {"ok": True},
    }


@pytest.mark.asyncio
async def test_next_frame_returns_dict_for_transport_path():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )
    await connection.send_frame({"type": "custom", "room_id": "room-1"})

    assert await connection.next_frame(timeout=0.01) == {
        "type": "custom",
        "room_id": "room-1",
    }


@pytest.mark.asyncio
async def test_timeout_returns_heartbeat_frame():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )

    frame = await connection.next_frame(timeout=0.01)

    assert frame == {
        "type": "heartbeat",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
    }


@pytest.mark.asyncio
async def test_get_message_serializes_heartbeat_on_timeout():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )

    raw = await connection.get_message(timeout=0.01)

    assert json.loads(raw) == {
        "type": "heartbeat",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
    }


def test_close_marks_inactive():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
    )

    connection.close()

    assert connection.is_active is False
