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
async def test_full_bounded_queue_marks_resync_instead_of_closing():
    # Room Stream Snapshot plan §7: slow consumers resync instead of
    # disconnecting. QueueFull no longer closes the connection; the frame is
    # dropped, the connection stays alive, and gap detection recovers the
    # client via a snapshot re-request.
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        queue_maxsize=1,
        now=fixed_now,
    )

    assert await connection.send_frame({"type": "first"}) is True
    assert await connection.send_frame({"type": "overflow"}) is True
    assert connection.is_active is True
    assert connection.needs_resync is True
    assert connection.frames_dropped == 1
    assert connection.queue.qsize() == 1


@pytest.mark.asyncio
async def test_droppable_snapshot_frame_skipped_without_blocking_deltas():
    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        queue_maxsize=1,
        now=fixed_now,
    )

    # A droppable snapshot occupying the queue is evicted when a live delta
    # arrives; the delta is never policy-dropped.
    assert await connection.send_frame({"type": "snapshot", "data": {}}) is True
    assert (
        await connection.send_frame({"type": "task_update", "data": {"x": 1}}) is True
    )
    frame = await connection.queue.get()
    assert frame["type"] == "task_update"
    assert connection.is_active is True
    assert connection.needs_resync is True


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
        "data": {},
    }


@pytest.mark.asyncio
async def test_heartbeat_carries_latest_room_seq_when_reader_bound():
    async def reader(room_id):
        assert room_id == "room-1"
        return 42

    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        now=fixed_now,
        room_seq_reader=reader,
    )

    frame = await connection.next_frame(timeout=0.01)

    assert frame == {
        "type": "heartbeat",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {"room_seq": 42},
    }


@pytest.mark.asyncio
async def test_resync_enqueues_fresh_snapshot_from_provider():
    async def provider(room_id):
        return {"room_seq": 7, "messages": []}

    connection = SSEConnection(
        room_id="room-1",
        connection_id="conn-1",
        heartbeat_interval=3.0,
        queue_maxsize=2,
        now=fixed_now,
        snapshot_provider=provider,
    )

    # Fill the queue so the resync mark is set.
    assert await connection.send_frame({"type": "first"}) is True
    assert await connection.send_frame({"type": "second"}) is True
    assert await connection.send_frame({"type": "overflow"}) is True
    assert connection.needs_resync is True

    await connection.queue.get()
    first = await connection.next_frame(timeout=0.01)
    assert first["type"] == "second"
    frame = await connection.next_frame(timeout=0.01)

    assert frame["type"] == "snapshot"
    assert frame["data"] == {"room_seq": 7, "messages": []}
    assert connection.needs_resync is False


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
        "data": {},
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
