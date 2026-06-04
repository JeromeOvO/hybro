"""Unit tests for RunLifecycleService shadow persistence."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeEventPublisher:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.events = []
        self._calls = calls

    async def emit(self, event) -> None:
        if self._calls is not None:
            self._calls.append("emit")
        self.events.append(event)


@pytest.mark.asyncio
async def test_record_processing_status_skips_when_dual_write_disabled(monkeypatch):
    monkeypatch.setenv("FEATURE_RUN_DUAL_WRITE", "0")
    import execution.run_command_handler as handler_mod
    import execution.run_lifecycle_service as mod

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
    import execution.run_command_handler as handler_mod
    import execution.run_lifecycle_service as mod
    from common.a2a_constants import SSEProcessingStatus

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
    import execution.run_lifecycle_service as mod

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
async def test_bind_run_lifecycle_service_uses_injected_handler(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import execution.run_lifecycle_service as mod

    original = mod.run_command_handler
    payload = {
        "event_id": "evt-bound",
        "run_id": "msg-1",
        "seq": 1,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
    }
    bound = MagicMock()
    bound.record_processing_status = AsyncMock(return_value=payload)

    try:
        mod.bind_run_lifecycle_service(bound)
        result = await mod.run_lifecycle_service.record_processing_status(
            room_id="room-1",
            status="processing",
            message_id="msg-1",
            client_request_id="cr-1",
        )
    finally:
        mod.bind_run_lifecycle_service(original)

    assert result is payload
    bound.record_processing_status.assert_awaited_once_with(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
        client_request_id="cr-1",
        details=None,
    )


@pytest.mark.asyncio
async def test_record_and_maybe_emit_run_event_records_before_emit(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import execution.run_lifecycle_service as mod

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

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(mod.run_command_handler, "record_processing_status", fake_record)
    event_publisher = FakeEventPublisher(calls)

    result = await mod.record_and_maybe_emit_run_event(
        "room-1",
        "processing",
        "msg-1",
        client_request_id="cr-1",
        details="starting",
        event_publisher=event_publisher,
    )

    assert result is payload
    assert calls == ["record", "emit"]
    assert event_publisher.events[0].event_type == "run_event"


@pytest.mark.asyncio
async def test_record_and_maybe_emit_run_event_uses_event_publisher(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import execution.run_lifecycle_service as mod

    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
    }
    event_publisher = FakeEventPublisher()

    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(return_value=payload),
    )

    await mod.record_and_maybe_emit_run_event(
        "room-1",
        "processing",
        "msg-1",
        client_request_id="cr-1",
        event_publisher=event_publisher,
    )

    event = event_publisher.events[0]
    assert event.event_type == "run_event"
    assert event.event_id == "evt-1"
    assert event.run_id == "msg-1"
    assert event.seq == 2
    assert event.run_event_type == "RUN_STARTED"
    assert event.payload == {"status": "processing"}
    assert event.correlation_id == "cr-1"


def test_build_run_event_payload_includes_correlation_id():
    import execution.run_lifecycle_service as mod

    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
    }

    assert mod.build_run_event_payload(
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
async def test_record_and_maybe_emit_run_event_skips_when_flag_disabled(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import execution.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 1, "type": "RUN_STARTED"}
    event_publisher = FakeEventPublisher()
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "0")
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(return_value=payload),
    )

    await mod.record_and_maybe_emit_run_event(
        "room-1", "processing", "msg-1", event_publisher=event_publisher
    )

    assert event_publisher.events == []


@pytest.mark.asyncio
async def test_record_and_maybe_emit_run_event_skips_when_payload_none(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import execution.run_lifecycle_service as mod

    event_publisher = FakeEventPublisher()
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(return_value=None),
    )

    await mod.record_and_maybe_emit_run_event(
        "room-1", "completed", "msg-1", event_publisher=event_publisher
    )

    assert event_publisher.events == []


@pytest.mark.asyncio
async def test_duplicate_terminal_payload_none_does_not_emit_second_run_event(monkeypatch):
    monkeypatch.delenv("FEATURE_RUN_DUAL_WRITE", raising=False)
    import execution.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 3, "type": "RUN_COMPLETED"}
    event_publisher = FakeEventPublisher()
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(
        mod.run_command_handler,
        "record_processing_status",
        AsyncMock(side_effect=[payload, None]),
    )

    await mod.record_and_maybe_emit_run_event(
        "room-1", "completed", "msg-1", event_publisher=event_publisher
    )
    await mod.record_and_maybe_emit_run_event(
        "room-1", "completed", "msg-1", event_publisher=event_publisher
    )

    assert len(event_publisher.events) == 1


@pytest.mark.asyncio
async def test_emit_run_event_payload_does_not_record(monkeypatch):
    import execution.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 3, "type": "RUN_FAILED"}
    event_publisher = FakeEventPublisher()
    record = AsyncMock()
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")
    monkeypatch.setattr(mod.run_command_handler, "record_processing_status", record)

    await mod.emit_run_event_payload(
        "room-1", payload, client_request_id="cr-1", event_publisher=event_publisher
    )

    record.assert_not_awaited()
    assert len(event_publisher.events) == 1


@pytest.mark.asyncio
async def test_emit_run_event_payload_requires_explicit_event_publisher(monkeypatch):
    import execution.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 3, "type": "RUN_FAILED"}
    monkeypatch.setenv("FEATURE_RUN_EVENT_SSE", "1")

    with pytest.raises(TypeError):
        await mod.emit_run_event_payload("room-1", payload)
