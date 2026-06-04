import asyncio
from datetime import UTC, datetime

import pytest

from common.dto import (
    HubAgentResponseInternal,
    MessageCommitted,
    ProcessingStatusEvent,
    RunStateChanged,
)
from common.observability import get_current_trace_id, trace_id_context
from delivery.config import DeliveryConfig
from delivery.event_publisher import EventPublisherImpl

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def fixed_now():
    return NOW


class RecordingTaskRunner:
    def __init__(self):
        self.tasks: list[asyncio.Task] = []

    def __call__(self, coro, *, name=None):
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        return task


class FakeTransport:
    def __init__(self):
        self.frames: list[tuple[str, dict]] = []
        self.error: Exception | None = None

    async def broadcast_frame_to_room(self, room_id: str, frame: dict) -> None:
        if self.error is not None:
            raise self.error
        self.frames.append((room_id, frame))


class FakeBus:
    def __init__(self):
        self.sse: list[tuple[str, dict]] = []
        self.internal = []
        self.dead_letters: list[dict] = []
        self.sse_error: Exception | None = None
        self.internal_error: Exception | None = None
        self.dead_letter_error: Exception | None = None

    async def publish_sse(self, room_id: str, frame: dict) -> None:
        if self.sse_error is not None:
            raise self.sse_error
        self.sse.append((room_id, frame))

    async def publish_internal(self, event) -> None:
        if self.internal_error is not None:
            raise self.internal_error
        self.internal.append(event)

    async def publish_dead_letter(self, envelope: dict) -> None:
        if self.dead_letter_error is not None:
            raise self.dead_letter_error
        self.dead_letters.append(envelope)


class FakeDeduplicator:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    async def should_deliver(self, *, room_id, message_id, status):
        self.calls.append((room_id, message_id, status))
        return self.result


class FakeMetrics:
    def __init__(self):
        self.increments: list[tuple[str, float, dict | None]] = []

    def increment(self, name, value=1.0, tags=None):
        self.increments.append((name, value, tags))

    def gauge(self, name, value, tags=None):
        pass

    def timing(self, name, value_ms, tags=None):
        pass


def make_publisher(
    *,
    transport=None,
    bus=None,
    dedup=None,
    task_runner=None,
    metrics=None,
    config=None,
):
    return EventPublisherImpl(
        sse_transport=transport or FakeTransport(),
        event_bus=bus or FakeBus(),
        deduplicator=dedup or FakeDeduplicator(),
        config=config or DeliveryConfig(),
        now=fixed_now,
        instance_id="worker-1",
        task_runner=task_runner or RecordingTaskRunner(),
        metrics=metrics,
    )


@pytest.mark.asyncio
async def test_emit_processing_status_translates_local_and_fanout_with_metrics():
    transport = FakeTransport()
    bus = FakeBus()
    metrics = FakeMetrics()
    publisher = make_publisher(transport=transport, bus=bus, metrics=metrics)

    await publisher.emit(
        ProcessingStatusEvent(room_id="room-1", message_id="msg-1", status="processing")
    )

    frame = {
        "type": "processing_status",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "status": "processing",
            "message_id": "msg-1",
            "details": None,
        },
    }
    assert transport.frames == [("room-1", frame)]
    assert bus.sse == [("room-1", frame)]
    assert metrics.increments == [
        (
            "hybro_delivery_events_emitted_total",
            1.0,
            {"event_type": "processing_status"},
        )
    ]


@pytest.mark.asyncio
async def test_emit_suppresses_terminal_duplicate_and_counts_dedup():
    transport = FakeTransport()
    bus = FakeBus()
    dedup = FakeDeduplicator(result=False)
    metrics = FakeMetrics()
    publisher = make_publisher(
        transport=transport,
        bus=bus,
        dedup=dedup,
        metrics=metrics,
    )

    await publisher.emit(
        ProcessingStatusEvent(room_id="room-1", message_id="msg-1", status="completed")
    )

    assert transport.frames == []
    assert bus.sse == []
    assert dedup.calls == [("room-1", "msg-1", "completed")]
    assert metrics.increments == [
        (
            "hybro_delivery_events_deduplicated_total",
            1.0,
            {"event_type": "processing_status"},
        )
    ]


@pytest.mark.asyncio
async def test_local_delivery_failure_does_not_prevent_fanout_or_dead_letter():
    transport = FakeTransport()
    transport.error = RuntimeError("local disconnected")
    bus = FakeBus()
    publisher = make_publisher(transport=transport, bus=bus)

    await publisher.emit(
        ProcessingStatusEvent(room_id="room-1", message_id="msg-1", status="processing")
    )

    assert len(bus.sse) == 1
    assert bus.dead_letters == []
    assert list(publisher.dead_letters) == []


@pytest.mark.asyncio
async def test_fanout_failure_is_dead_lettered_without_raising():
    bus = FakeBus()
    bus.sse_error = RuntimeError("redis down")
    publisher = make_publisher(bus=bus)

    await publisher.emit(
        ProcessingStatusEvent(room_id="room-1", message_id="msg-1", status="processing")
    )

    assert len(bus.dead_letters) == 1
    assert publisher.dead_letters[0]["failure_stage"] == "sse_fanout"


@pytest.mark.asyncio
async def test_emit_internal_schedules_handlers_and_publishes_without_sse():
    transport = FakeTransport()
    bus = FakeBus()
    runner = RecordingTaskRunner()
    publisher = make_publisher(transport=transport, bus=bus, task_runner=runner)
    received = []

    async def handler(event):
        received.append(event)

    event = MessageCommitted(
        room_id="room-1",
        message_id="msg-1",
        message_type="user",
        timestamp=NOW,
    )
    publisher.register_internal_handler("message_committed", handler)

    await publisher.emit_internal(event)
    await asyncio.gather(*runner.tasks)

    assert received == [event]
    assert bus.internal == [event]
    assert transport.frames == []


@pytest.mark.asyncio
async def test_emit_does_not_dispatch_internal_handlers():
    runner = RecordingTaskRunner()
    publisher = make_publisher(task_runner=runner)
    called = []
    publisher.register_internal_handler("processing_status", lambda event: called.append(event))

    await publisher.emit(
        ProcessingStatusEvent(room_id="room-1", message_id="msg-1", status="processing")
    )

    assert called == []
    assert runner.tasks == []


@pytest.mark.asyncio
async def test_multiple_internal_handlers_and_handler_exception_dead_letter():
    bus = FakeBus()
    runner = RecordingTaskRunner()
    publisher = make_publisher(bus=bus, task_runner=runner)
    received = []

    async def good_handler(event):
        received.append(event.event_type)

    async def bad_handler(event):
        raise RuntimeError("handler failed")

    event = RunStateChanged(
        run_id="run-1",
        room_id="room-1",
        old_state="queued",
        new_state="processing",
        timestamp=NOW,
    )
    publisher.register_internal_handler("run_state_changed", good_handler)
    publisher.register_internal_handler("run_state_changed", bad_handler)

    await publisher.emit_internal(event)
    await asyncio.gather(*runner.tasks, return_exceptions=True)

    assert received == ["run_state_changed"]
    assert any(letter["failure_stage"] == "internal_handler" for letter in bus.dead_letters)


@pytest.mark.asyncio
async def test_remote_internal_event_restores_trace_context_and_rejects_mismatch():
    runner = RecordingTaskRunner()
    publisher = make_publisher(task_runner=runner)
    observed = []
    publisher.register_internal_handler(
        "message_committed",
        lambda event: observed.append(get_current_trace_id()),
    )

    await publisher.handle_remote_internal_event(
        {
            "kind": "internal_event",
            "origin": "worker-2",
            "event_type": "message_committed",
            "trace_id": "trace-remote",
            "event": {
                "event_type": "message_committed",
                "timestamp": NOW.isoformat(),
                "payload": {},
                "room_id": "room-1",
                "message_id": "msg-1",
                "message_type": "user",
                "agent_id": None,
            },
        }
    )
    await asyncio.gather(*runner.tasks)

    assert observed == ["trace-remote"]
    assert get_current_trace_id() is None

    await publisher.handle_remote_internal_event(
        {
            "kind": "internal_event",
            "origin": "worker-2",
            "event_type": "message_committed",
            "event": {
                "event_type": "run_state_changed",
                "timestamp": NOW.isoformat(),
                "payload": {},
                "run_id": "run-1",
                "room_id": "room-1",
                "old_state": "queued",
                "new_state": "processing",
            },
        }
    )
    assert len(runner.tasks) == 1


@pytest.mark.asyncio
async def test_trace_context_is_added_to_typed_frames():
    transport = FakeTransport()
    bus = FakeBus()
    publisher = make_publisher(transport=transport, bus=bus)

    with trace_id_context("trace-123"):
        await publisher.emit(
            ProcessingStatusEvent(room_id="room-1", message_id="msg-1", status="processing")
        )

    assert transport.frames[0][1]["data"]["trace_id"] == "trace-123"


@pytest.mark.asyncio
async def test_stop_cancels_blocked_handler_after_configured_timeout():
    runner = RecordingTaskRunner()
    publisher = make_publisher(
        task_runner=runner,
        config=DeliveryConfig(handler_shutdown_timeout_seconds=0.01),
    )

    async def blocked_handler(event):
        await asyncio.Event().wait()

    event = MessageCommitted(
        room_id="room-1",
        message_id="msg-1",
        message_type="user",
        timestamp=NOW,
    )
    publisher.register_internal_handler("message_committed", blocked_handler)
    await publisher.emit_internal(event)

    await publisher.stop()

    assert runner.tasks[0].cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        MessageCommitted(
            room_id="room-1",
            message_id="msg-1",
            message_type="user",
            timestamp=NOW,
        ),
        RunStateChanged(
            run_id="run-1",
            room_id="room-1",
            old_state="queued",
            new_state="processing",
            timestamp=NOW,
        ),
        HubAgentResponseInternal(
            hub_id="hub-1",
            agent_id="agent-1",
            task_id="task-1",
            room_id="room-1",
            is_terminal=False,
            payload={"delta": "hello"},
            timestamp=NOW,
        ),
    ],
)
async def test_all_internal_event_union_members_schedule_local_handlers(event):
    runner = RecordingTaskRunner()
    publisher = make_publisher(task_runner=runner)
    received = []

    publisher.register_internal_handler(event.event_type, lambda item: received.append(item))

    await publisher.emit_internal(event)
    await asyncio.gather(*runner.tasks)

    assert received == [event]


@pytest.mark.asyncio
async def test_remote_internal_events_cover_all_union_members_and_multiple_handlers():
    runner = RecordingTaskRunner()
    publisher = make_publisher(task_runner=runner)
    first = []
    second = []
    events = [
        MessageCommitted(
            room_id="room-1",
            message_id="msg-1",
            message_type="user",
            timestamp=NOW,
        ),
        RunStateChanged(
            run_id="run-1",
            room_id="room-1",
            old_state="queued",
            new_state="processing",
            timestamp=NOW,
        ),
        HubAgentResponseInternal(
            hub_id="hub-1",
            agent_id="agent-1",
            task_id="task-1",
            room_id="room-1",
            is_terminal=True,
            payload={"content": "done"},
            timestamp=NOW,
        ),
    ]

    for event in events:
        publisher.register_internal_handler(
            event.event_type,
            lambda item, bucket=first: bucket.append(item.event_type),
        )
        publisher.register_internal_handler(
            event.event_type,
            lambda item, bucket=second: bucket.append(item.event_type),
        )
        await publisher.handle_remote_internal_event(
            {
                "kind": "internal_event",
                "origin": "worker-2",
                "event_type": event.event_type,
                "event": event.model_dump(mode="json"),
            }
        )

    await asyncio.gather(*runner.tasks)

    assert first == [event.event_type for event in events]
    assert second == [event.event_type for event in events]


@pytest.mark.asyncio
async def test_emit_internal_with_no_subscribers_is_noop_for_handlers():
    runner = RecordingTaskRunner()
    bus = FakeBus()
    publisher = make_publisher(bus=bus, task_runner=runner)
    event = HubAgentResponseInternal(
        hub_id="hub-1",
        agent_id="agent-1",
        task_id="task-1",
        room_id="room-1",
        is_terminal=False,
        timestamp=NOW,
    )

    await publisher.emit_internal(event)

    assert runner.tasks == []
    assert bus.internal == [event]
