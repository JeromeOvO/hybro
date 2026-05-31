"""Unit tests for RunLifecycleService shadow persistence."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_record_processing_status_skips_when_dual_write_disabled(monkeypatch):
    monkeypatch.setenv("FEATURE_RUN_DUAL_WRITE", "0")
    import services.run_command_handler as handler_mod
    import services.run_lifecycle_service as mod

    fake = MagicMock()
    with patch.object(handler_mod, "mongodb", fake):
        await mod.run_lifecycle_service.record_processing_status(
            room_id="room-1",
            status="processing",
            message_id="msg-1",
        )
    fake.runs_collection.find_one.assert_not_called()


@pytest.mark.asyncio
async def test_record_processing_status_dual_write_default_allows_calls(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import services.run_command_handler as handler_mod
    import services.run_lifecycle_service as mod
    from services.a2a_constants import SSEProcessingStatus

    fake_runs = MagicMock()
    fake_runs.find_one = AsyncMock(return_value=None)
    fake_runs.insert_one = AsyncMock()
    fake_runs.update_one = AsyncMock()
    fake_events = MagicMock()
    fake_events.insert_one = AsyncMock()
    fake_mongo = MagicMock()
    fake_mongo.runs_collection = fake_runs
    fake_mongo.run_events_collection = fake_events

    with patch.object(handler_mod, "mongodb", fake_mongo):
        await mod.run_lifecycle_service.record_processing_status(
            room_id="room-1",
            status=SSEProcessingStatus.PROCESSING,
            message_id="msg-1",
        )
    fake_runs.find_one.assert_called()


@pytest.mark.asyncio
async def test_record_processing_status_returns_handler_payload(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import services.run_lifecycle_service as mod

    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 1,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
    }
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(return_value=payload),
    )

    result = await mod.run_lifecycle_service.record_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        client_request_id="cr-1",
        details="starting",
    )

    assert result is payload


@pytest.mark.asyncio
async def test_record_and_maybe_broadcast_run_event_records_before_broadcast(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import services.run_lifecycle_service as mod

    calls: list[str] = []
    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
    }

    async def fake_record(**kwargs):
        calls.append("record")
        return payload

    class FakeSSE:
        async def broadcast_to_room(self, *args, **kwargs):
            calls.append("broadcast")

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(mod.run_command_handler, "record_processing_status", fake_record)

    result = await mod.record_and_maybe_broadcast_run_event(
        "room-1",
        "processing",
        "msg-1",
        client_request_id="cr-1",
        details="starting",
        sse=FakeSSE(),
    )

    assert result is payload
    assert calls == ["record", "broadcast"]


@pytest.mark.asyncio
async def test_record_and_maybe_broadcast_run_event_uses_provided_sse(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import services.run_lifecycle_service as mod

    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
    }
    sse = MagicMock()
    sse.broadcast_to_room = AsyncMock()

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(return_value=payload),
    )

    await mod.record_and_maybe_broadcast_run_event(
        "room-1",
        "processing",
        "msg-1",
        client_request_id="cr-1",
        sse=sse,
    )

    sse.broadcast_to_room.assert_awaited_once_with(
        "room-1",
        "run_event",
        {
            "event_id": "evt-1",
            "run_id": "msg-1",
            "seq": 2,
            "type": "RUN_STARTED",
            "payload": {"status": "processing"},
            "correlation_id": "cr-1",
        },
    )


def test_build_run_event_sse_payload_includes_correlation_id():
    import services.run_lifecycle_service as mod

    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
    }

    assert mod.build_run_event_sse_payload(
        payload, client_request_id="cr-1"
    ) == {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
        "correlation_id": "cr-1",
    }


@pytest.mark.asyncio
async def test_record_and_maybe_broadcast_run_event_skips_when_flag_disabled(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import services.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 1, "type": "RUN_STARTED"}
    sse = MagicMock()
    sse.broadcast_to_room = AsyncMock()
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "0")
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(return_value=payload),
    )

    await mod.record_and_maybe_broadcast_run_event(
        "room-1", "processing", "msg-1", sse=sse
    )

    sse.broadcast_to_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_and_maybe_broadcast_run_event_skips_when_payload_none(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import services.run_lifecycle_service as mod

    sse = MagicMock()
    sse.broadcast_to_room = AsyncMock()
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(return_value=None),
    )

    await mod.record_and_maybe_broadcast_run_event(
        "room-1", "completed", "msg-1", sse=sse
    )

    sse.broadcast_to_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_terminal_payload_none_does_not_emit_second_run_event(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import services.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 3, "type": "RUN_COMPLETED"}
    sse = MagicMock()
    sse.broadcast_to_room = AsyncMock()
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(side_effect=[payload, None]),
    )

    await mod.record_and_maybe_broadcast_run_event(
        "room-1", "completed", "msg-1", sse=sse
    )
    await mod.record_and_maybe_broadcast_run_event(
        "room-1", "completed", "msg-1", sse=sse
    )

    sse.broadcast_to_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_run_event_payload_does_not_record(monkeypatch):
    import services.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 3, "type": "RUN_FAILED"}
    sse = MagicMock()
    sse.broadcast_to_room = AsyncMock()
    record = AsyncMock()
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(mod.run_command_handler, "record_processing_status", record)

    await mod.broadcast_run_event_payload(
        "room-1", payload, client_request_id="cr-1", sse=sse
    )

    record.assert_not_awaited()
    sse.broadcast_to_room.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_run_event_payload_requires_explicit_sse(monkeypatch):
    import services.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 3, "type": "RUN_FAILED"}
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")

    with pytest.raises(TypeError):
        await mod.broadcast_run_event_payload("room-1", payload)
