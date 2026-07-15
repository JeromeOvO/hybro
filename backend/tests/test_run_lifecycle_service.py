"""Unit tests for RunLifecycleService shadow persistence."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from common.config import settings


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
    monkeypatch.setattr(settings, "feature_run_dual_write", False)
    import execution.run_lifecycle_service as mod

    fake = AsyncMock()
    fake.record_processing_status = AsyncMock(return_value=None)
    mod.run_command_handler = fake
    await mod.run_lifecycle_service.record_processing_status(
        room_id="room-1",
        status="processing",
        message_id="msg-1",
    )
    fake.record_processing_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_run_state_skips_when_dual_write_disabled(monkeypatch):
    monkeypatch.setattr(settings, "feature_run_dual_write", False)
    import execution.run_lifecycle_service as mod
    from models.run import RunState

    fake = AsyncMock()
    fake.project_run_state = AsyncMock(return_value={"run_id": "run-1"})
    mod.run_command_handler = fake

    result = await mod.run_lifecycle_service.project_run_state(
        room_id="room-1",
        run_id="run-1",
        trigger_message_id="msg-1",
        target_state=RunState.PROCESSING,
        terminal_reason=None,
        causation_id="orch-event-1",
        client_request_id="cr-1",
    )

    assert result is None
    fake.project_run_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_processing_status_dual_write_default_allows_calls(monkeypatch):
    monkeypatch.setattr(settings, "feature_run_dual_write", True)
    import execution.run_lifecycle_service as mod
    from common.a2a_constants import SSEProcessingStatus

    fake = AsyncMock()
    fake.record_processing_status = AsyncMock(return_value={"ok": True})
    mod.run_command_handler = fake

    await mod.run_lifecycle_service.record_processing_status(
        room_id="room-1",
        status=SSEProcessingStatus.PROCESSING,
        message_id="msg-1",
    )
    fake.record_processing_status.assert_awaited_once_with(
        room_id="room-1",
        status=SSEProcessingStatus.PROCESSING,
        message_id="msg-1",
        client_request_id=None,
        details=None,
    )


@pytest.mark.asyncio
async def test_record_processing_status_returns_handler_payload(monkeypatch):
    monkeypatch.setattr(settings, "feature_run_dual_write", True)
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
    monkeypatch.setattr(settings, "feature_run_dual_write", True)
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
    monkeypatch.setattr(settings, "feature_run_dual_write", True)
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

    monkeypatch.setattr(settings, "feature_run_event_sse", True)
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
    monkeypatch.setattr(settings, "feature_run_dual_write", True)
    import execution.run_lifecycle_service as mod

    payload = {
        "event_id": "evt-1",
        "run_id": "msg-1",
        "seq": 2,
        "type": "RUN_STARTED",
        "payload": {"status": "processing"},
    }
    event_publisher = FakeEventPublisher()

    monkeypatch.setattr(settings, "feature_run_event_sse", True)
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
    monkeypatch.setattr(settings, "feature_run_dual_write", True)
    import execution.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 1, "type": "RUN_STARTED"}
    event_publisher = FakeEventPublisher()
    monkeypatch.setattr(settings, "feature_run_event_sse", False)
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
    monkeypatch.setattr(settings, "feature_run_dual_write", True)
    import execution.run_lifecycle_service as mod

    event_publisher = FakeEventPublisher()
    monkeypatch.setattr(settings, "feature_run_event_sse", True)
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
    monkeypatch.setattr(settings, "feature_run_dual_write", True)
    import execution.run_lifecycle_service as mod

    payload = {"event_id": "evt-1", "run_id": "msg-1", "seq": 3, "type": "RUN_COMPLETED"}
    event_publisher = FakeEventPublisher()
    monkeypatch.setattr(settings, "feature_run_event_sse", True)
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
    monkeypatch.setattr(settings, "feature_run_event_sse", True)
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
    monkeypatch.setattr(settings, "feature_run_event_sse", True)

    with pytest.raises(TypeError):
        await mod.emit_run_event_payload("room-1", payload)
