import asyncio
import inspect
import json
from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import BaseModel

from common.dto.internal_events import HubAgentResponseInternal
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


def make_bus(
    *,
    transport=None,
    queue_size=1000,
    enqueue_timeout=0.05,
    auxiliary_task_maxsize=128,
    shutdown_timeout=0.05,
):
    registry = EventModelRegistry()
    registry.register("example", ExampleEvent)
    return BoundedInternalEventBus(
        registry=registry,
        instance_id="instance-a",
        now=lambda: datetime(2025, 1, 1, tzinfo=UTC),
        transport=transport,
        config=EventingConfig(
            handler_queue_maxsize=queue_size,
            auxiliary_task_maxsize=auxiliary_task_maxsize,
            enqueue_timeout_seconds=enqueue_timeout,
            shutdown_timeout_seconds=shutdown_timeout,
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
async def test_failed_start_rollback_is_bounded_when_transport_stop_resists_cancel():
    class FailingStartupTransport(RecordingTransport):
        def __init__(self):
            super().__init__()
            self.release_stop = asyncio.Event()

        async def start(self, callback):
            self.callback = callback
            raise RuntimeError("startup failed")

        async def stop(self):
            self.stop_calls += 1
            while not self.release_stop.is_set():
                try:
                    await self.release_stop.wait()
                except asyncio.CancelledError:
                    continue

    transport = FailingStartupTransport()
    bus = make_bus(transport=transport, shutdown_timeout=0.02)
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    with pytest.raises(RuntimeError, match="startup failed"):
        await bus.start()

    assert loop.time() - started_at < 0.1
    assert transport.stop_calls == 1
    assert bus.worker_tasks == ()
    assert len(bus._auxiliary_tasks) == 1

    with pytest.raises(RuntimeError, match="cleanup is still pending"):
        await bus.start()
    assert transport.stop_calls == 1
    assert len(bus._auxiliary_tasks) == 1

    transport.release_stop.set()
    for _ in range(10):
        if not bus._auxiliary_tasks:
            break
        await asyncio.sleep(0)
    assert bus._auxiliary_tasks == {}


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
async def test_cancelled_publisher_collects_cancellable_transport_publish():
    transport = RecordingTransport()
    entered = asyncio.Event()
    canceled = asyncio.Event()

    async def blocking_publish(_message):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    transport.publish = blocking_publish
    bus = make_bus(transport=transport, enqueue_timeout=0.2)
    await bus.start()
    publisher = asyncio.create_task(bus.publish(ExampleEvent(value=1)))
    await entered.wait()
    publisher.cancel()
    with pytest.raises(asyncio.CancelledError):
        await publisher

    assert canceled.is_set()
    assert bus._auxiliary_tasks == {}
    await bus.stop()
    assert bus._auxiliary_tasks == {}


@pytest.mark.asyncio
async def test_auxiliary_task_is_registered_before_wait_and_survives_double_cancel():
    bus = make_bus()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def cancellation_resistant():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    caller = asyncio.create_task(
        bus._await_bounded(
            cancellation_resistant(),
            timeout=1.0,
            operation="double_cancel_test",
        )
    )
    await entered.wait()
    caller.cancel()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert len(bus._auxiliary_tasks) == 1
    assert next(iter(bus._auxiliary_tasks.values())) == "double_cancel_test"

    release.set()
    for _ in range(10):
        if not bus._auxiliary_tasks:
            break
        await asyncio.sleep(0)
    assert bus._auxiliary_tasks == {}


@pytest.mark.asyncio
async def test_auxiliary_capacity_closes_unstarted_coroutine_and_records_dlt():
    bus = make_bus(auxiliary_task_maxsize=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def cancellation_resistant():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    with pytest.raises(TimeoutError):
        await bus._await_bounded(
            cancellation_resistant(),
            timeout=0.001,
            operation="capacity_holder",
        )
    await entered.wait()
    assert len(bus._auxiliary_tasks) == 1

    invoked = False

    async def must_not_start():
        nonlocal invoked
        invoked = True

    rejected = must_not_start()
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        await bus._await_bounded(
            rejected,
            timeout=1.0,
            operation="capacity_rejected",
        )

    assert not invoked
    assert inspect.getcoroutinestate(rejected) == inspect.CORO_CLOSED
    assert any(
        item.failure_stage == "auxiliary_task_capacity"
        and item.metadata["capacity"] == 1
        for item in bus.dead_letters
    )

    release.set()
    for _ in range(10):
        if not bus._auxiliary_tasks:
            break
        await asyncio.sleep(0)
    assert bus._auxiliary_tasks == {}


@pytest.mark.asyncio
async def test_shutdown_lifecycle_bypasses_saturated_auxiliary_capacity():
    transport = RecordingTransport()
    ingress_calls = 0
    release = asyncio.Event()

    async def stop_ingress():
        nonlocal ingress_calls
        ingress_calls += 1

    async def cancellation_resistant():
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    transport.stop_ingress = stop_ingress
    bus = make_bus(
        transport=transport,
        auxiliary_task_maxsize=1,
        shutdown_timeout=0.03,
    )
    await bus.start()
    with pytest.raises(TimeoutError):
        await bus._await_bounded(
            cancellation_resistant(),
            timeout=0.001,
            operation="capacity_holder",
        )

    await bus.stop()

    assert ingress_calls == 1
    assert transport.stop_calls == 1
    release.set()
    for _ in range(10):
        if not bus._auxiliary_tasks:
            break
        await asyncio.sleep(0)


def test_eventing_config_rejects_non_positive_auxiliary_capacity():
    assert EventingConfig().auxiliary_task_maxsize == 128
    with pytest.raises(ValueError, match="auxiliary_task_maxsize"):
        EventingConfig(auxiliary_task_maxsize=0)


@pytest.mark.asyncio
async def test_malicious_transport_publish_remains_tracked_and_stop_is_bounded():
    transport = RecordingTransport()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def malicious_publish(_message):
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    transport.publish = malicious_publish
    bus = make_bus(transport=transport, enqueue_timeout=0.02)
    await bus.start()
    publisher = asyncio.create_task(bus.publish(ExampleEvent(value=1)))
    await entered.wait()
    await asyncio.wait_for(publisher, timeout=0.2)

    assert bus._auxiliary_tasks
    await asyncio.wait_for(bus.stop(), timeout=0.2)
    assert any(
        item.failure_stage == "auxiliary_task_timeout" for item in bus.dead_letters
    )
    assert bus._auxiliary_tasks

    release.set()
    for _ in range(10):
        if not bus._auxiliary_tasks:
            break
        await asyncio.sleep(0)
    assert bus._auxiliary_tasks == {}


@pytest.mark.asyncio
async def test_shutdown_is_bounded_when_handler_swallows_cancelled_error():
    bus = make_bus()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def malicious(_event):
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    bus.register_handler("example", malicious)
    await bus.start()
    worker = bus.worker_tasks[0]
    publisher = asyncio.create_task(
        bus.publish(ExampleEvent(value=1), wait_for_handlers=True, fanout=False)
    )
    await entered.wait()

    await asyncio.wait_for(bus.stop(), timeout=0.2)
    await asyncio.wait_for(publisher, timeout=0.1)

    assert bus.worker_tasks == (worker,)
    timeout_dlt = next(
        item
        for item in bus.dead_letters
        if item.failure_stage == "shutdown_handler_timeout"
    )
    assert timeout_dlt.metadata["handler"].endswith("malicious")
    assert timeout_dlt.payload["payload_size_bytes"] > 0

    release.set()
    await asyncio.wait_for(worker, timeout=0.1)
    await asyncio.sleep(0)
    assert bus.worker_tasks == ()


@pytest.mark.asyncio
async def test_cancellation_resistant_worker_ownership_prevents_duplicate_restart():
    bus = make_bus()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def malicious(_event):
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    bus.register_handler("example", malicious)
    await bus.start()
    worker = bus.worker_tasks[0]
    publisher = asyncio.create_task(
        bus.publish(ExampleEvent(value=1), wait_for_handlers=True, fanout=False)
    )
    await entered.wait()

    await bus.stop()
    await publisher
    assert bus.worker_tasks == (worker,)

    await bus.start()
    assert bus.worker_tasks == (worker,)
    await bus.stop()
    assert bus.worker_tasks == (worker,)

    release.set()
    await asyncio.wait_for(worker, timeout=0.1)
    await asyncio.sleep(0)
    assert bus.worker_tasks == ()


@pytest.mark.asyncio
async def test_many_malicious_handlers_and_auxiliary_tasks_share_shutdown_deadline():
    transport = RecordingTransport()
    transport_release = asyncio.Event()
    transport_entered = asyncio.Event()

    async def malicious_publish(_message):
        transport_entered.set()
        while not transport_release.is_set():
            try:
                await transport_release.wait()
            except asyncio.CancelledError:
                continue

    transport.publish = malicious_publish
    bus = make_bus(
        transport=transport,
        enqueue_timeout=0.005,
        auxiliary_task_maxsize=128,
        shutdown_timeout=0.05,
    )
    handler_release = asyncio.Event()
    handler_entered = [asyncio.Event() for _ in range(24)]

    for entered in handler_entered:

        async def malicious_handler(_event, *, entered=entered):
            entered.set()
            while not handler_release.is_set():
                try:
                    await handler_release.wait()
                except asyncio.CancelledError:
                    continue

        bus.register_handler("example", malicious_handler)

    await bus.start()
    publisher = asyncio.create_task(
        bus.publish(ExampleEvent(value=1), wait_for_handlers=True)
    )
    await asyncio.gather(*(entered.wait() for entered in handler_entered))
    await transport_entered.wait()

    loop = asyncio.get_running_loop()
    started = loop.time()
    await bus.stop()
    elapsed = loop.time() - started
    await asyncio.wait_for(publisher, timeout=0.1)

    workers = bus.worker_tasks
    assert len(workers) == len(handler_entered)
    assert elapsed < 0.09
    assert sum(
        item.failure_stage == "shutdown_handler_timeout" for item in bus.dead_letters
    ) == len(handler_entered)
    assert any(
        item.failure_stage == "auxiliary_task_timeout" for item in bus.dead_letters
    )

    handler_release.set()
    transport_release.set()
    await asyncio.wait_for(asyncio.gather(*workers), timeout=0.2)
    for _ in range(10):
        if not bus.worker_tasks and not bus._auxiliary_tasks:
            break
        await asyncio.sleep(0)
    assert bus.worker_tasks == ()
    assert bus._auxiliary_tasks == {}


@pytest.mark.asyncio
async def test_dead_letters_redact_hub_body_and_bound_redis_projection():
    transport = RecordingTransport()
    registry = EventModelRegistry()
    registry.register("hub_agent_response_internal", HubAgentResponseInternal)
    bus = BoundedInternalEventBus(
        registry=registry,
        instance_id="instance-a",
        now=lambda: datetime(2025, 1, 1, tzinfo=UTC),
        transport=transport,
        config=EventingConfig(
            handler_queue_maxsize=2,
            enqueue_timeout_seconds=0.02,
            shutdown_timeout_seconds=0.05,
            dead_letter_memory_maxlen=10,
        ),
    )
    secret = "PRIVATE_HUB_BODY_" + "x" * 100_000

    exception_secret = "PRIVATE_PROMPT https://example.test/?token=SECRET_TOKEN"

    async def fail(_event):
        raise RuntimeError(exception_secret + "e" * 10_000)

    bus.register_handler("hub_agent_response_internal", fail)
    await bus.start()
    event = HubAgentResponseInternal(
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        hub_id="hub-1",
        agent_id="agent-1",
        task_id="task-1",
        room_id="room-1",
        is_terminal=True,
        journal_id="journal-1",
        idempotency_key="idem-1",
        payload={
            "id": "PRIVATE_NESTED_ID",
            "request_id": "PRIVATE_NESTED_REQUEST_ID",
            "content": secret,
            "nested": {
                "id": "PRIVATE_DEEPLY_NESTED_ID",
                "prompt": secret,
            },
        },
    )
    await bus.publish(event, wait_for_handlers=True, fanout=False)
    malformed_secret = f'{{"content":"{secret}"'
    await bus.handle_remote_message(malformed_secret)
    await bus._dead_letter(
        "raw_payload",
        {
            "id": "PRIVATE_RAW_ID",
            "request_id": "PRIVATE_RAW_REQUEST_ID",
            "nested": {"room_id": "PRIVATE_RAW_NESTED_ROOM_ID"},
        },
        RuntimeError("raw payload failed"),
    )

    in_memory = "\n".join(item.model_dump_json() for item in bus.dead_letters)
    published = "\n".join(transport.dead_letters)
    assert secret not in in_memory
    assert secret not in published
    assert exception_secret not in in_memory
    assert exception_secret not in published
    assert "SECRET_TOKEN" not in in_memory
    assert "SECRET_TOKEN" not in published
    assert "PRIVATE_NESTED_ID" not in in_memory
    assert "PRIVATE_NESTED_REQUEST_ID" not in in_memory
    assert "PRIVATE_DEEPLY_NESTED_ID" not in in_memory
    assert "PRIVATE_RAW_ID" not in in_memory
    assert "PRIVATE_RAW_REQUEST_ID" not in in_memory
    assert "PRIVATE_RAW_NESTED_ROOM_ID" not in in_memory
    raw_dlt = next(
        item for item in bus.dead_letters if item.failure_stage == "raw_payload"
    )
    assert "identifiers" not in raw_dlt.payload
    assert all(
        len(item.model_dump_json().encode()) <= 8192 for item in bus.dead_letters
    )
    handler_dlt = next(
        item for item in bus.dead_letters if item.failure_stage == "handler"
    )
    assert handler_dlt.payload["identifiers"] == {
        "hub_id": "hub-1",
        "agent_id": "agent-1",
        "task_id": "task-1",
        "room_id": "room-1",
        "journal_id": "journal-1",
        "idempotency_key": "idem-1",
    }
    assert handler_dlt.exception_message.startswith("redacted:size_bytes=")
    assert "sha256=" in handler_dlt.exception_message
    assert "fingerprint=" in handler_dlt.exception_message
    await bus.stop()


def test_event_envelope_accepts_legacy_missing_timestamp():
    internal_timestamp = "2025-01-02T03:04:05Z"
    envelope = EventEnvelope.model_validate(
        {
            "origin": "legacy-worker",
            "event_type": "example",
            "event": {
                "event_type": "example",
                "value": 1,
                "timestamp": internal_timestamp,
            },
        }
    )
    assert envelope.timestamp == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert "timestamp" in json.loads(envelope.model_dump_json())

    fallback = EventEnvelope.model_validate(
        {
            "origin": "older-worker",
            "event_type": "example",
            "event": {"event_type": "example", "value": 2},
        }
    )
    assert fallback.timestamp.tzinfo is not None


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
