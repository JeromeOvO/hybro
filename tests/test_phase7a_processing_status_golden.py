import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.RoomMessageCenter import RoomMessageCenter
from services.sse_services import SSEManager


async def _next_sse_type(conn) -> tuple[str, dict]:
    raw = await conn.queue.get()
    parsed = json.loads(raw)
    return parsed["type"], parsed["data"]


@pytest.mark.asyncio
async def test_golden_send_message_processing_status_order(monkeypatch):
    import services.room_services as room_services
    from services.a2a_constants import SSEProcessingStatus
    from services.run_lifecycle_service import record_and_maybe_broadcast_run_event

    manager = SSEManager()
    conn = await manager.add_connection("room-1")
    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])
    helper_spy = AsyncMock(wraps=record_and_maybe_broadcast_run_event)

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(
        "services.run_lifecycle_service.run_command_handler.record_processing_status",
        record,
    )
    monkeypatch.setattr(room_services, "sse_manager", manager)
    monkeypatch.setattr(
        room_services,
        "record_and_maybe_broadcast_run_event",
        helper_spy,
        raising=False,
    )

    svc = object.__new__(room_services.RoomServices)
    await svc._send_processing_status("room-1", "msg-1", "cr-1")

    first_type, first_data = await _next_sse_type(conn)
    second_type, second_data = await _next_sse_type(conn)

    assert helper_spy.await_count == 1
    assert first_type == "run_event"
    assert first_data["event_id"] == "evt-1"
    assert first_data["correlation_id"] == "cr-1"
    assert second_type == "processing_status"
    assert second_data["status"] == SSEProcessingStatus.PROCESSING
    assert second_data["message_id"] == "msg-1"
    assert second_data["client_request_id"] == "cr-1"


@pytest.mark.asyncio
async def test_golden_hitl_resolve_resume_completion_order(monkeypatch):
    import modules.RoomMessageCenter as rmc_mod
    from services.run_lifecycle_service import record_and_maybe_broadcast_run_event

    manager = SSEManager()
    conn = await manager.add_connection("room-1")
    payload = {
        "event_id": "evt-2",
        "run_id": "msg-1",
        "seq": 7,
        "type": "RUN_COMPLETED",
        "payload": {},
    }
    record = AsyncMock(side_effect=[payload, None])
    helper_spy = AsyncMock(wraps=record_and_maybe_broadcast_run_event)

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(
        "services.run_lifecycle_service.run_command_handler.record_processing_status",
        record,
    )
    monkeypatch.setattr(
        rmc_mod,
        "record_and_maybe_broadcast_run_event",
        helper_spy,
        raising=False,
    )

    rmc = object.__new__(RoomMessageCenter)
    rmc.sse_manager = manager
    rmc.database_service = SimpleNamespace(
        save_continuation_on_message=AsyncMock(),
        get_room_by_room_id=AsyncMock(return_value=SimpleNamespace(extend_info={})),
    )
    rmc.queue_executor = SimpleNamespace(
        resume_from_continuation=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                needs_completion=True,
                room_id="room-1",
                user_message_id="msg-1",
            )
        )
    )
    rmc._emit_unified_summary = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()

    result = await rmc._resume_continuation_locked(
        {"supervisor_v2": False, "room_id": "room-1"},
        "agent-msg-1",
        "answer",
    )

    first_type, first_data = await _next_sse_type(conn)
    second_type, second_data = await _next_sse_type(conn)

    assert result is True
    assert helper_spy.await_count == 1
    assert first_type == "run_event"
    assert first_data["event_id"] == "evt-2"
    assert second_type == "processing_status"
    assert second_data["status"] == "completed"
    assert second_data["message_id"] == "msg-1"
