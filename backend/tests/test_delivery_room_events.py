"""Room event log: sequencing, idempotency, range reads, hole healing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from delivery.room_events import InMemoryRoomEventStore

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def _payload(marker: str) -> dict:
    return {"marker": marker}


@pytest.mark.asyncio
async def test_append_allocates_contiguous_per_room_sequences():
    store = InMemoryRoomEventStore()
    first = await store.append(
        room_id="room-1",
        kind="task_update",
        payload_public=_payload("a"),
        event_id="evt-a",
    )
    second = await store.append(
        room_id="room-1",
        kind="task_update",
        payload_public=_payload("b"),
        event_id="evt-b",
    )
    other_room = await store.append(
        room_id="room-2",
        kind="task_update",
        payload_public=_payload("c"),
        event_id="evt-c",
    )

    assert (first.room_seq, first.room_event_id) == (1, "evt-a")
    assert (second.room_seq, second.room_event_id) == (2, "evt-b")
    assert (other_room.room_seq, other_room.room_event_id) == (1, "evt-c")
    assert await store.latest_seq("room-1") == 2
    assert await store.latest_seq("room-2") == 1


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_reuses_persisted_sequence():
    store = InMemoryRoomEventStore()
    first = await store.append(
        room_id="room-1",
        kind="processing_status",
        payload_public=_payload("a"),
        event_id="logical-1",
        idempotency_key="terminal:room-1:msg-1:completed",
    )
    # A delivery retry re-appends with the same deterministic key.
    retry = await store.append(
        room_id="room-1",
        kind="processing_status",
        payload_public=_payload("a"),
        event_id="logical-1",
        idempotency_key="terminal:room-1:msg-1:completed",
    )

    assert first == retry
    assert await store.latest_seq("room-1") == 1
    records = await store.read_range("room-1")
    assert len(records) == 1


@pytest.mark.asyncio
async def test_read_range_orders_by_seq_and_respects_after_and_limit():
    store = InMemoryRoomEventStore()
    for marker in ("a", "b", "c", "d"):
        await store.append(
            room_id="room-1",
            kind="run_event",
            payload_public=_payload(marker),
            event_id=f"evt-{marker}",
        )

    records = await store.read_range("room-1", after=1, limit=2)
    assert [record["room_seq"] for record in records] == [2, 3]
    assert [record["event_id"] for record in records] == ["evt-b", "evt-c"]
    records = await store.read_range("room-1")
    assert [record["room_seq"] for record in records] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_append_records_parent_and_run_links():
    store = InMemoryRoomEventStore()
    appended = await store.append(
        room_id="room-1",
        kind="run_event",
        payload_public=_payload("decision"),
        event_id="public:run-1:orchestrator_decision:1",
        run_id="run-1",
    )
    child = await store.append(
        room_id="room-1",
        kind="task_submitted",
        payload_public=_payload("dispatch"),
        event_id="task-1",
        parent_event_id=appended.room_event_id,
        run_id="run-1",
    )

    records = await store.read_range("room-1")
    assert records[1]["parent_event_id"] == appended.room_event_id
    assert records[0]["run_id"] == "run-1"
    assert child.room_seq == 2


@pytest.mark.asyncio
async def test_skipped_tombstones_are_hidden_from_replay_but_foldable():
    store = InMemoryRoomEventStore()
    await store.append(
        room_id="room-1",
        kind="run_event",
        payload_public=_payload("a"),
        event_id="evt-a",
    )
    await store.append(
        room_id="room-1",
        kind="run_event",
        payload_public=_payload("b"),
        event_id="evt-b",
    )
    await store.append(
        room_id="room-1",
        kind="skipped",
        payload_public={},
        event_id=None,
        idempotency_key="skip:room-1:2",
    )

    replayed = await store.read_range("room-1", include_skipped=False)
    assert [record["room_seq"] for record in replayed] == [1, 2]

    folded = await store.read_range("room-1", include_skipped=True)
    assert [record["room_seq"] for record in folded] == [1, 2, 3]
    assert folded[2]["kind"] == "skipped"
