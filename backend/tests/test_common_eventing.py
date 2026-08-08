import asyncio
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import BaseModel

from common.eventing import (
    BoundedInternalEventBus,
    EventEnvelope,
    EventingConfig,
    EventModelRegistry,
)
from common.observability import get_current_trace_id, trace_id_context
from container import RelayReadyHubInternalHandler


class ExampleEvent(BaseModel):
    event_type: Literal["example"] = "example"
    value: int


class RecordingTransport:
    def __init__(self) -> None:
        self.messages = []
        self.dead_letters = []
        self.callback = None
        self.is_connected = False
        self.stopped = False
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self, callback) -> None:
        self.start_calls += 1
        self.callback = callback
        self.is_connected = True

    async def publish(self, message: str) -> None:
        self.messages.append(message)

    async def publish_dead_letter(self, message: str) -> None:
        self.dead_letters.append(message)

    async def refresh_health(self) -> None:
        return None

    async def stop_ingress(self) -> None:
        self.is_connected = False

    async def stop(self) -> None:
        self.stop_calls += 1
        self.stopped = True
        self.is_connected = False


def test_registry_rejects_non_models_and_mismatched_event_discriminators():
    registry = EventModelRegistry()

    class WrongDefault(BaseModel):
        event_type: Literal["other"] = "other"

    class NonLiteral(BaseModel):
        event_type: str = "example"

    with pytest.raises(TypeError, match="BaseModel"):
        registry.register("example", object)
    with pytest.raises(ValueError, match="default"):
        registry.register("example", WrongDefault)
    with pytest.raises(ValueError, match="discriminator"):
        registry.register("example", NonLiteral)


def make_bus(*, transport=None, queue_size=1000, enqueue_timeout=0.05):
    registry = EventModelRegistry()
    registry.register("example", ExampleEvent)
    return BoundedInternalEventBus(
        registry=registry,
        instance_id="instance-a",
        now=lambda: datetime(2025, 1, 1, tzinfo=UTC),
        transport=transport,
        config=EventingConfig(
            handler_queue_maxsize=queue_size,
            enqueue_timeout_seconds=enqueue_timeout,
            shutdown_timeout_seconds=0.05,
            dead_letter_memory_maxlen=1000,
        ),
    )


@pytest.mark.asyncio
async def test_handler_fifo_order_and_wait_for_handlers():
    bus = make_bus()
    observed = []

    async def handler(event):
        await asyncio.sleep(0)
        observed.append(event.value)

    bus.register_handler("example", handler)
    await bus.start()
    await asyncio.gather(
        *(bus.publish(ExampleEvent(value=value), fanout=False) for value in range(5))
    )
    await bus.publish(ExampleEvent(value=5), wait_for_handlers=True, fanout=False)

    assert observed == list(range(6))
    await bus.stop()
    assert bus.worker_tasks == ()


@pytest.mark.asyncio
async def test_different_handlers_run_concurrently_with_one_worker_each():
    bus = make_bus()
    entered = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def first(_event):
        entered[0].set()
        await release.wait()

    async def second(_event):
        entered[1].set()
        await release.wait()

    bus.register_handler("example", first)
    bus.register_handler("example", second)
    await bus.start()
    await bus.publish(ExampleEvent(value=1), fanout=False)
    await asyncio.wait_for(
        asyncio.gather(*(event.wait() for event in entered)), timeout=0.2
    )
    release.set()
    await bus.stop()


@pytest.mark.asyncio
async def test_admitted_multi_handler_publish_completes_all_handlers_during_stop():
    transport = RecordingTransport()
    bus = make_bus(transport=transport)
    observed = [[], []]

    async def first(event):
        observed[0].append(event.value)

    async def second(event):
        observed[1].append(event.value)

    bus.register_handler("example", first)
    bus.register_handler("example", second)
    first_state = bus._handlers["example"][0]
    original_put = first_state.queue.put
    put_entered = asyncio.Event()
    release_put = asyncio.Event()

    async def paused_put(item):
        put_entered.set()
        await release_put.wait()
        await original_put(item)

    first_state.queue.put = paused_put
    await bus.start()
    publish_task = asyncio.create_task(
        bus.publish(ExampleEvent(value=1), wait_for_handlers=True, fanout=True)
    )
    await put_entered.wait()
    stop_task = asyncio.create_task(bus.stop())
    await asyncio.sleep(0)
    release_put.set()

    await publish_task
    await stop_task

    assert observed == [[1], [1]]
    assert len(transport.messages) == 1
    with pytest.raises(RuntimeError, match="not running"):
        await bus.publish(ExampleEvent(value=2), fanout=False)
    assert observed == [[1], [1]]


@pytest.mark.asyncio
async def test_trace_context_propagates_to_local_and_remote_handlers():
    transport = RecordingTransport()
    bus = make_bus(transport=transport)
    traces = []

    async def handler(_event):
        traces.append(get_current_trace_id())

    bus.register_handler("example", handler)
    await bus.start()
    with trace_id_context("trace-local"):
        await bus.publish(ExampleEvent(value=1), wait_for_handlers=True, fanout=True)
    remote = (
        transport.messages[0]
        .replace("instance-a", "instance-b")
        .replace("trace-local", "trace-remote")
    )
    await bus.handle_remote_message(remote)
    await asyncio.sleep(0)

    assert traces == ["trace-local", "trace-remote"]
    await bus.stop()


@pytest.mark.asyncio
async def test_queue_full_handler_failure_fanout_and_deserialization_are_dead_lettered():
    transport = RecordingTransport()
    bus = make_bus(transport=transport, queue_size=1, enqueue_timeout=0.01)
    release = asyncio.Event()

    async def blocked(event):
        if event.value == 1:
            await release.wait()
        else:
            raise ValueError("handler failed")

    bus.register_handler("example", blocked)
    await bus.start()
    await bus.publish(ExampleEvent(value=1), fanout=False)
    await asyncio.sleep(0)
    await bus.publish(ExampleEvent(value=2), fanout=False)
    await bus.publish(ExampleEvent(value=3), fanout=False)
    release.set()
    await bus.publish(ExampleEvent(value=4), wait_for_handlers=True, fanout=False)
    await bus.handle_remote_message("not-json")

    async def fail_publish(_message):
        raise RuntimeError("redis unavailable")

    transport.publish = fail_publish
    await bus.publish(ExampleEvent(value=5))
    await asyncio.sleep(0)

    stages = {item.failure_stage for item in bus.dead_letters}
    assert {"queue_full", "handler", "deserialization", "fanout"} <= stages
    assert transport.dead_letters
    await bus.stop()


@pytest.mark.asyncio
async def test_concurrent_start_and_restart_keep_one_worker_per_handler():
    transport = RecordingTransport()
    bus = make_bus(transport=transport)
    bus.register_handler("example", lambda _event: None)

    await asyncio.gather(bus.start(), bus.start(), bus.start())

    assert transport.start_calls == 1
    assert len(bus.worker_tasks) == 1
    first_worker = bus.worker_tasks[0]
    await bus.stop()
    await bus.start()

    assert transport.start_calls == 2
    assert len(bus.worker_tasks) == 1
    assert bus.worker_tasks[0] is not first_worker
    await bus.stop()


@pytest.mark.asyncio
async def test_start_does_not_accept_until_transport_and_health_are_ready():
    class BlockingStartupTransport(RecordingTransport):
        def __init__(self):
            super().__init__()
            self.start_entered = asyncio.Event()
            self.release_start = asyncio.Event()
            self.health_entered = asyncio.Event()
            self.release_health = asyncio.Event()

        async def start(self, callback):
            self.start_calls += 1
            self.callback = callback
            self.start_entered.set()
            await self.release_start.wait()

        async def refresh_health(self):
            self.health_entered.set()
            await self.release_health.wait()
            self.is_connected = True

    transport = BlockingStartupTransport()
    bus = make_bus(transport=transport)
    handled = []
    bus.register_handler("example", lambda event: handled.append(event.value))
    start_task = asyncio.create_task(bus.start())
    await transport.start_entered.wait()

    with pytest.raises(RuntimeError, match="not running"):
        await asyncio.wait_for(
            bus.publish(ExampleEvent(value=1)),
            timeout=0.02,
        )
    assert handled == []
    assert transport.messages == []

    transport.release_start.set()
    await transport.health_entered.wait()
    with pytest.raises(RuntimeError, match="not running"):
        await asyncio.wait_for(
            bus.publish(ExampleEvent(value=2)),
            timeout=0.02,
        )
    assert handled == []
    assert transport.messages == []

    transport.release_health.set()
    await start_task
    await bus.publish(ExampleEvent(value=3), wait_for_handlers=True)
    assert handled == [3]
    assert len(transport.messages) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_remote_callback_during_start_waits_and_is_delivered_after_ready():
    class StartupRemoteTransport(RecordingTransport):
        def __init__(self):
            super().__init__()
            self.callback_launched = asyncio.Event()
            self.release_start = asyncio.Event()
            self.callback_task = None

        async def start(self, callback):
            self.start_calls += 1
            self.callback = callback
            envelope = EventEnvelope(
                origin="instance-b",
                event_type="example",
                event={"event_type": "example", "value": 41},
                timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            )
            self.callback_task = asyncio.create_task(
                callback(envelope.model_dump_json())
            )
            self.callback_launched.set()
            await self.release_start.wait()

        async def stop_ingress(self):
            if self.callback_task is not None:
                await self.callback_task

    transport = StartupRemoteTransport()
    bus = make_bus(transport=transport)
    handled = []
    handled_event = asyncio.Event()

    async def handler(event):
        handled.append(event.value)
        handled_event.set()

    bus.register_handler("example", handler)
    start_task = asyncio.create_task(bus.start())
    await transport.callback_launched.wait()
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="not running"):
        await bus.publish(ExampleEvent(value=1))
    assert handled == []
    assert not transport.callback_task.done()

    transport.release_start.set()
    await start_task
    await asyncio.wait_for(handled_event.wait(), timeout=0.1)
    await transport.callback_task

    assert handled == [41]
    await bus.stop()


@pytest.mark.asyncio
async def test_failed_transport_start_has_no_handler_or_fanout_side_effects():
    class FailingStartupTransport(RecordingTransport):
        async def start(self, callback):
            self.callback = callback
            raise RuntimeError("startup failed")

    transport = FailingStartupTransport()
    bus = make_bus(transport=transport)
    handled = []
    bus.register_handler("example", lambda event: handled.append(event.value))

    with pytest.raises(RuntimeError, match="startup failed"):
        await bus.start()
    with pytest.raises(RuntimeError, match="not running"):
        await bus.publish(ExampleEvent(value=1))

    assert handled == []
    assert transport.messages == []
    assert bus.worker_tasks == ()


@pytest.mark.asyncio
async def test_cancelled_stop_collects_workers_and_can_be_retried():
    bus = make_bus()
    entered = asyncio.Event()

    async def blocked(_event):
        entered.set()
        await asyncio.Event().wait()

    bus.register_handler("example", blocked)
    await bus.start()
    await bus.publish(ExampleEvent(value=1), fanout=False)
    await entered.wait()
    stop_task = asyncio.create_task(bus.stop())
    await asyncio.sleep(0)
    stop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert bus.worker_tasks == ()
    await bus.stop()
    await bus.start()
    assert len(bus.worker_tasks) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_cancelled_stop_with_full_queue_finishes_publisher_and_empties_queue():
    bus = make_bus(queue_size=1, enqueue_timeout=1.0)
    entered = asyncio.Event()

    async def blocked(_event):
        entered.set()
        await asyncio.Event().wait()

    bus.register_handler("example", blocked)
    await bus.start()
    await bus.publish(ExampleEvent(value=1), fanout=False)
    await entered.wait()
    await bus.publish(ExampleEvent(value=2), fanout=False)
    blocked_publish = asyncio.create_task(
        bus.publish(
            ExampleEvent(value=3),
            wait_for_handlers=True,
            fanout=False,
        )
    )
    await asyncio.sleep(0)

    stop_task = asyncio.create_task(bus.stop())
    await asyncio.sleep(0)
    stop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_task
    await asyncio.wait_for(blocked_publish, timeout=0.2)

    assert bus.worker_tasks == ()
    assert all(state.queue.empty() for state in bus._handlers["example"])


@pytest.mark.asyncio
async def test_business_cancel_and_dead_letter_failure_do_not_kill_worker(monkeypatch):
    bus = make_bus()
    observed = []

    async def handler(event):
        if event.value == 1:
            raise asyncio.CancelledError("business cancellation")
        observed.append(event.value)

    async def broken_dead_letter(*_args, **_kwargs):
        raise RuntimeError("DLT unavailable")

    bus.register_handler("example", handler)
    monkeypatch.setattr(bus, "_dead_letter", broken_dead_letter)
    await bus.start()
    await bus.publish(ExampleEvent(value=1), wait_for_handlers=True, fanout=False)
    await bus.publish(ExampleEvent(value=2), wait_for_handlers=True, fanout=False)

    assert observed == [2]
    assert len(bus.worker_tasks) == 1
    assert not bus.worker_tasks[0].done()
    await bus.stop()


@pytest.mark.asyncio
async def test_worker_recovers_from_unexpected_queue_get_failure():
    bus = make_bus()
    observed = []
    bus.register_handler("example", lambda event: observed.append(event.value))
    state = bus._handlers["example"][0]
    original_get = state.queue.get
    failed = False

    async def flaky_get():
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("unexpected queue failure")
        return await original_get()

    state.queue.get = flaky_get
    await bus.start()
    await bus.publish(ExampleEvent(value=9), wait_for_handlers=True, fanout=False)

    assert observed == [9]
    assert any(item.failure_stage == "worker" for item in bus.dead_letters)
    await bus.stop()


@pytest.mark.asyncio
async def test_relay_ready_gate_preserves_startup_event_and_shutdown_drain():
    bus = make_bus()
    gate = RelayReadyHubInternalHandler()
    handled = []

    class Router:
        async def dispatch_hub_internal_response(self, event):
            handled.append(event.value)

    bus.register_handler("example", gate)
    await bus.start()
    await bus.publish(ExampleEvent(value=7), fanout=False)
    await asyncio.sleep(0)
    assert handled == []

    stop_task = asyncio.create_task(bus.stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    gate.bind(Router())
    await stop_task

    assert handled == [7]
    assert bus.worker_tasks == ()


@pytest.mark.asyncio
async def test_stop_has_bounded_cancel_and_freezes_registry_and_handlers():
    bus = make_bus()
    entered = asyncio.Event()

    async def forever(_event):
        entered.set()
        await asyncio.Event().wait()

    bus.register_handler("example", forever)
    await bus.start()
    await bus.publish(ExampleEvent(value=1), fanout=False)
    await entered.wait()
    with pytest.raises(RuntimeError, match="before start"):
        bus.register_handler("example", forever)
    with pytest.raises(RuntimeError, match="frozen"):
        bus.registry.register("other", ExampleEvent)
    await asyncio.wait_for(bus.stop(), timeout=0.2)

    assert bus.worker_tasks == ()
    assert not [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name().startswith("eventing-handler-")
    ]
