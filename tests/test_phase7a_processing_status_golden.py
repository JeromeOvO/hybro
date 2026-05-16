import json
from unittest.mock import AsyncMock

import pytest

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
