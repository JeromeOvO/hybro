"""Snapshot fold + incremental materialization (Room Stream Snapshot plan §5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from delivery.room_events import InMemoryRoomEventStore
from delivery.snapshot import RoomEventFold, SnapshotService

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def _record(kind: str, data: dict, *, room_seq: int = 0) -> dict:
    return {
        "room_seq": room_seq,
        "room_event_id": f"evt-{kind}-{room_seq}",
        "event_id": f"logical-{kind}-{room_seq}",
        "parent_event_id": None,
        "run_id": None,
        "kind": kind,
        "ts": NOW.isoformat(),
        "payload_public": data,
        "persist_state": "settled",
    }


def test_fold_projects_agent_response_and_partials():
    fold = RoomEventFold()
    fold.apply(
        _record(
            "agent_response_partial",
            {"message_id": "m1", "agent_id": "a1", "content_delta": "Hel"},
        )
    )
    fold.apply(
        _record(
            "agent_response_partial",
            {"message_id": "m1", "agent_id": "a1", "content_delta": "lo"},
        )
    )
    fold.apply(
        _record(
            "agent_response",
            {
                "message_id": "m1",
                "agent_id": "a1",
                "content": "Hello",
                "client_request_id": "cr-1",
            },
        )
    )

    state = fold.state(room_seq=3)
    assert state["room_seq"] == 3
    assert state["streaming"] == {}
    assert state["messages"][0] == {
        "message_id": "m1",
        "agent_id": "a1",
        "content": "Hello",
        "parts": None,
        "related_message_id": None,
        "client_request_id": "cr-1",
        "status": None,
        "task_status": None,
        "task_content": None,
        "task_error": None,
        "requires_input": False,
        "requires_auth": False,
        "step_number": None,
        "total_steps": None,
        "created_at": None,
        "ts": NOW.isoformat(),
        "artifacts": None,
        "status_logs": [],
    }


def test_fold_keeps_partial_streams_without_terminal_commit():
    fold = RoomEventFold()
    fold.apply(
        _record(
            "agent_response_partial",
            {"message_id": "m1", "agent_id": "a1", "content_delta": "Hel"},
        )
    )
    fold.apply(
        _record(
            "agent_response_partial",
            {"message_id": "m1", "agent_id": "a1", "content_delta": "lo"},
        )
    )

    state = fold.state(room_seq=2)
    assert state["messages"] == []
    assert state["streaming"]["m1"]["text"] == "Hello"
    assert state["streaming"]["m1"]["is_complete"] is False


def test_fold_projects_tasks_and_terminal_cleanup():
    fold = RoomEventFold()
    fold.apply(
        _record(
            "task_submitted",
            {
                "message_id": "m1",
                "task_id": "t1",
                "agent_name": "Weather Agent",
                "agent_id": "a1",
                "status": "working",
                "client_request_id": "cr-1",
            },
        )
    )
    fold.apply(
        _record(
            "task_update",
            {"message_id": "m1", "status": "completed", "content": "Sunny"},
        )
    )

    state = fold.state(room_seq=2)
    assert state["tasks"][0]["status"] == "completed"
    assert state["messages"][0]["task_status"] == "completed"
    assert state["messages"][0]["content"] == "Sunny"


def test_fold_projects_terminal_processing_status_and_log():
    fold = RoomEventFold()
    fold.apply(
        _record(
            "processing_status",
            {
                "message_id": "m1",
                "status": "processing",
                "details": {"message": "Thinking…", "turn_phase": "collecting"},
            },
        )
    )
    fold.apply(
        _record("processing_status", {"message_id": "m1", "status": "completed"})
    )

    state = fold.state(room_seq=2)
    message = state["messages"][0]
    assert message["status"] == "completed"
    assert message["status_logs"] == [
        {"message": "Thinking…", "timestamp": NOW.isoformat()}
    ]


def test_fold_projects_runs_and_trace_nodes():
    fold = RoomEventFold()
    fold.apply(
        _record(
            "run_event",
            {
                "run_id": "run-1",
                "event_id": "public:run-1:llm_call_completed:3",
                "seq": 3,
                "type": "llm_call_completed",
                "payload": {
                    "model": "gpt-4o",
                    "provider": "openai",
                    "attempt": 1,
                    "outcome": "completed",
                    "duration_ms": 812,
                    "usage": {"input": 100, "output": 20},
                    "finish_reason": "stop",
                },
                "correlation_id": "cr-1",
            },
        )
    )
    fold.apply(
        _record(
            "run_event",
            {
                "run_id": "run-1",
                "event_id": "public:run-1:tool_call_accepted:5",
                "seq": 5,
                "type": "tool_call_accepted",
                "payload": {
                    "tool_name": "weather_lookup",
                    "arg_summary": {"city": "SH"},
                },
            },
        )
    )
    fold.apply(
        _record(
            "run_event",
            {
                "run_id": "run-1",
                "event_id": "public:run-1:tool_call_completed:8",
                "seq": 8,
                "type": "tool_call_completed",
                "payload": {
                    "tool_name": "weather_lookup",
                    "result_summary": "Sunny",
                    "exit_code": 0,
                    "duration_ms": 120,
                },
            },
        )
    )
    fold.apply(
        _record(
            "run_event",
            {
                "run_id": "run-1",
                "event_id": "terminal-1",
                "seq": 10,
                "type": "run_completed",
                "payload": {},
                "correlation_id": "cr-1",
            },
        )
    )

    state = fold.state(room_seq=4)
    assert state["runs"] == [
        {
            "run_id": "run-1",
            "status": "completed",
            "client_request_id": "cr-1",
            "ts": NOW.isoformat(),
        }
    ]
    nodes = state["trace"]["run-1"]["nodes"]
    assert [node["kind"] for node in nodes] == ["llm_call", "tool_call"]
    assert nodes[1]["status"] == "completed"
    assert nodes[1]["result_summary"] == "Sunny"
    assert state["trace"]["run-1"]["duration_ms"] == 812


def test_fold_projects_hitl_requests_and_responses():
    fold = RoomEventFold()
    fold.apply(
        _record(
            "hitl_request",
            {
                "request_id": "h1",
                "message_id": "m1",
                "prompt": "Approve?",
                "prompt_type": "confirmation",
                "source": "agent",
            },
        )
    )
    fold.apply(
        _record(
            "hitl_response",
            {
                "request_id": "h1",
                "message_id": "m1",
                "source": "agent",
                "status": "responded",
                "interaction_id": "i1",
            },
        )
    )

    state = fold.state(room_seq=2)
    assert state["hitl"]["requests"][0]["status"] == "responded"
    assert len(state["hitl"]["resolved"]) == 1


async def _seeded_service() -> SnapshotService:
    store = InMemoryRoomEventStore()
    await store.append(
        room_id="room-1",
        kind="agent_response",
        payload_public={"message_id": "m1", "agent_id": "a1", "content": "one"},
        event_id="m1-final",
    )
    await store.append(
        room_id="room-1",
        kind="agent_response",
        payload_public={"message_id": "m2", "agent_id": "a1", "content": "two"},
        event_id="m2-final",
    )
    return SnapshotService(store=store)


@pytest.mark.asyncio
async def test_snapshot_watermark_tracks_contiguous_prefix():
    service = await _seeded_service()
    snapshot = await service.snapshot("room-1")
    assert snapshot["room_seq"] == 2
    assert len(snapshot["messages"]) == 2

    # Incremental: no new events, same watermark, same content.
    again = await service.snapshot("room-1")
    assert again["room_seq"] == 2
    assert again["messages"] == snapshot["messages"]


@pytest.mark.asyncio
async def test_snapshot_force_refolds_from_log():
    service = await _seeded_service()
    snapshot = await service.snapshot("room-1")
    assert snapshot["room_seq"] == 2

    forced = await service.snapshot("room-1", force=True)
    assert forced["room_seq"] == 2
    assert forced["messages"] == snapshot["messages"]


@pytest.mark.asyncio
async def test_snapshot_stops_at_first_gap():
    store = InMemoryRoomEventStore()
    await store.append(
        room_id="room-1",
        kind="agent_response",
        payload_public={"message_id": "m1", "content": "one"},
        event_id="m1-final",
    )
    await store.append(
        room_id="room-1",
        kind="agent_response",
        payload_public={"message_id": "m3", "content": "three"},
        event_id="m3-final",
    )
    # Simulate a permanent hole: seq 2 was burned and tombstoned as skipped
    # (InMemory tombstone takes the next free seq, mirroring the healed hole).
    await store.append(
        room_id="room-1",
        kind="skipped",
        payload_public={},
        idempotency_key="skip:room-1:2",
    )

    service = SnapshotService(store=store)
    snapshot = await service.snapshot("room-1", force=True)
    # The fold advanced past the tombstone and folded m3.
    assert snapshot["room_seq"] == 3
    assert [message["message_id"] for message in snapshot["messages"]] == ["m1", "m3"]
