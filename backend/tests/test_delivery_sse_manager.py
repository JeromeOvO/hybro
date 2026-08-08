import asyncio
from datetime import UTC, datetime

import pytest

from delivery.config import DeliveryConfig
from delivery.sse.manager import SSETransportImpl
from delivery.types import RoomSubscriptionLimitExceeded

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def fixed_now():
    return NOW


def task_runner(coro, *, name=None):
    return asyncio.create_task(coro, name=name)


class FakeEventBus:
    def __init__(self):
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.subscribe_waiter: asyncio.Event | None = None
        self.subscribe_error: Exception | None = None
        self.unsubscribe_entered = asyncio.Event()
        self.unsubscribe_waiter: asyncio.Event | None = None
        self.unsubscribe_tasks: list[asyncio.Task | None] = []

    async def subscribe_room(self, room_id: str) -> None:
        self.subscribed.append(room_id)
        if self.subscribe_waiter is not None:
            await self.subscribe_waiter.wait()
        if self.subscribe_error is not None:
            raise self.subscribe_error

    async def unsubscribe_room(self, room_id: str) -> None:
        self.unsubscribed.append(room_id)
        self.unsubscribe_tasks.append(asyncio.current_task())
        self.unsubscribe_entered.set()
        if self.unsubscribe_waiter is not None:
            await self.unsubscribe_waiter.wait()


class FakeCancellationWatcher:
    def __init__(self):
        self.cancelled: set[str] = set()
        self.started = False

    def is_cancelled(self, message_id: str) -> bool:
        return message_id in self.cancelled

    async def mark_cancelled(self, message_id: str) -> None:
        self.cancelled.add(message_id)

    async def start(self) -> None:
        self.started = True


class FakeMetrics:
    def __init__(self):
        self.gauges: list[tuple[str, float, dict[str, str] | None]] = []

    def increment(self, name, value=1.0, tags=None):
        pass

    def gauge(self, name, value, tags=None):
        self.gauges.append((name, value, tags))

    def timing(self, name, value_ms, tags=None):
        pass


class IdFactory:
    def __init__(self):
        self.next_ids = ["conn-1", "conn-2", "conn-3"]
        self.calls = 0

    def __call__(self):
        value = self.next_ids[self.calls]
        self.calls += 1
        return value


def make_transport(
    *,
    event_bus: FakeEventBus | None = None,
    config: DeliveryConfig | None = None,
    metrics: FakeMetrics | None = None,
    id_factory: IdFactory | None = None,
):
    return SSETransportImpl(
        cancellation_watcher=FakeCancellationWatcher(),
        event_bus=event_bus or FakeEventBus(),
        config=config or DeliveryConfig(),
        now=fixed_now,
        id_factory=id_factory or IdFactory(),
        instance_id="worker-1",
        task_runner=task_runner,
        metrics=metrics,
    )


@pytest.mark.asyncio
async def test_open_connection_adds_room_maps_and_uses_id_factory():
    ids = IdFactory()
    transport = make_transport(id_factory=ids)

    connection = await transport.open_connection("room-1")

    assert ids.calls == 1
    assert connection.connection_id == "conn-1"
    assert transport.room_connections["room-1"]["conn-1"] is connection
    assert transport.connection_rooms["conn-1"] == "room-1"


@pytest.mark.asyncio
async def test_first_connection_subscribes_once_and_last_disconnect_unsubscribes():
    event_bus = FakeEventBus()
    transport = make_transport(event_bus=event_bus)

    first = await transport.open_connection("room-1")
    second = await transport.open_connection("room-1")

    assert event_bus.subscribed == ["room-1"]

    await transport.remove_connection("room-1", first.connection_id)
    assert event_bus.unsubscribed == []

    await transport.remove_connection("room-1", second.connection_id)
    await transport._drain_room_cleanup_tasks()
    assert event_bus.unsubscribed == ["room-1"]
    assert "room-1" not in transport.room_connections
    assert transport.connection_rooms == {}


@pytest.mark.asyncio
async def test_new_first_connection_after_empty_room_resubscribes():
    event_bus = FakeEventBus()
    transport = make_transport(event_bus=event_bus)

    first = await transport.open_connection("room-1")
    await transport.remove_connection("room-1", first.connection_id)
    await transport._drain_room_cleanup_tasks()
    await transport.open_connection("room-1")

    assert event_bus.subscribed == ["room-1", "room-1"]
    assert event_bus.unsubscribed == ["room-1"]


@pytest.mark.asyncio
async def test_subscription_failure_rejects_without_local_admission():
    event_bus = FakeEventBus()
    event_bus.subscribe_error = RoomSubscriptionLimitExceeded("too many rooms")
    metrics = FakeMetrics()
    transport = make_transport(event_bus=event_bus, metrics=metrics)

    with pytest.raises(ConnectionRefusedError):
        await transport.open_connection("room-1")
    await transport._drain_room_cleanup_tasks()

    assert transport.room_connections == {}
    assert transport.connection_rooms == {}
    assert event_bus.unsubscribed == ["room-1"]
    assert metrics.gauges == [
        ("hybro_delivery_sse_connections", 0, {"worker_id": "worker-1"})
    ]


@pytest.mark.asyncio
async def test_concurrent_first_connection_failure_has_no_partial_admission():
    event_bus = FakeEventBus()
    event_bus.subscribe_waiter = asyncio.Event()
    event_bus.subscribe_error = RuntimeError("redis down")
    metrics = FakeMetrics()
    transport = make_transport(event_bus=event_bus, metrics=metrics)

    first = asyncio.create_task(transport.open_connection("room-1"))
    second = asyncio.create_task(transport.open_connection("room-1"))
    await asyncio.sleep(0)

    assert transport.room_connections == {}
    assert transport.connection_rooms == {}
    assert metrics.gauges == [
        ("hybro_delivery_sse_connections", 0, {"worker_id": "worker-1"})
    ]

    event_bus.subscribe_waiter.set()
    with pytest.raises(ConnectionRefusedError):
        await first
    with pytest.raises(ConnectionRefusedError):
        await second
    await transport._drain_room_cleanup_tasks()

    assert event_bus.subscribed == ["room-1", "room-1"]
    assert event_bus.unsubscribed == ["room-1"]
    assert transport.room_connections == {}
    assert transport.connection_rooms == {}


@pytest.mark.asyncio
async def test_concurrent_first_connection_success_subscribes_once_then_admits_both():
    event_bus = FakeEventBus()
    event_bus.subscribe_waiter = asyncio.Event()
    metrics = FakeMetrics()
    transport = make_transport(event_bus=event_bus, metrics=metrics)

    first = asyncio.create_task(transport.open_connection("room-1"))
    second = asyncio.create_task(transport.open_connection("room-1"))
    await asyncio.sleep(0)

    assert transport.room_connections == {}
    assert transport.connection_rooms == {}

    event_bus.subscribe_waiter.set()
    first_conn, second_conn = await asyncio.gather(first, second)

    assert event_bus.subscribed == ["room-1"]
    assert set(transport.room_connections["room-1"]) == {
        first_conn.connection_id,
        second_conn.connection_id,
    }
    assert transport.connection_rooms == {
        first_conn.connection_id: "room-1",
        second_conn.connection_id: "room-1",
    }
    assert metrics.gauges[-1] == (
        "hybro_delivery_sse_connections",
        2,
        {"worker_id": "worker-1"},
    )


@pytest.mark.asyncio
async def test_protocol_connect_uses_caller_connection_id_and_disconnects_in_finally():
    transport = make_transport(config=DeliveryConfig(heartbeat_interval_seconds=0.01))

    iterator = transport.connect("room-1", "provided-conn")
    frame_task = asyncio.create_task(iterator.__anext__())
    await asyncio.sleep(0)

    assert "provided-conn" in transport.room_connections["room-1"]

    frame = await frame_task
    assert frame["type"] == "heartbeat"

    await iterator.aclose()
    assert transport.room_connections == {}
    assert transport.connection_rooms == {}


@pytest.mark.asyncio
async def test_custom_heartbeat_interval_is_passed_to_connection():
    transport = make_transport(config=DeliveryConfig(heartbeat_interval_seconds=3))

    connection = await transport.open_connection("room-1")

    assert connection.heartbeat_interval == 3


@pytest.mark.asyncio
async def test_broadcast_frame_to_room_preserves_order_and_empty_room_is_noop():
    transport = make_transport()
    connection = await transport.open_connection("room-1")

    await transport.broadcast_frame_to_room("missing", {"type": "noop"})
    await transport.broadcast_frame_to_room(
        "room-1", {"type": "one", "room_id": "room-1"}
    )
    await transport.broadcast_frame_to_room(
        "room-1", {"type": "two", "room_id": "room-1"}
    )

    assert await connection.next_frame(timeout=0.01) == {
        "type": "one",
        "room_id": "room-1",
    }
    assert await connection.next_frame(timeout=0.01) == {
        "type": "two",
        "room_id": "room-1",
    }


@pytest.mark.asyncio
async def test_slow_connection_overflow_does_not_block_fast_connection():
    config = DeliveryConfig(sse_connection_queue_maxsize=1)
    transport = make_transport(config=config)
    slow = await transport.open_connection("room-1")
    fast = await transport.open_connection("room-1")

    await transport.broadcast_frame_to_room("room-1", {"type": "first"})
    assert await fast.next_frame(timeout=0.01) == {"type": "first"}
    await transport.broadcast_frame_to_room("room-1", {"type": "second"})

    assert slow.is_active is False
    assert slow.connection_id not in transport.room_connections["room-1"]
    assert await fast.next_frame(timeout=0.01) == {"type": "second"}


@pytest.mark.asyncio
async def test_overflow_broadcast_does_not_wait_for_background_unsubscribe():
    event_bus = FakeEventBus()
    event_bus.unsubscribe_waiter = asyncio.Event()
    transport = make_transport(
        event_bus=event_bus,
        config=DeliveryConfig(sse_connection_queue_maxsize=1),
    )
    connection = await transport.open_connection("room-1")
    await transport.broadcast_frame_to_room("room-1", {"type": "first"})

    broadcast = asyncio.create_task(
        transport.broadcast_frame_to_room("room-1", {"type": "overflow"})
    )
    await asyncio.wait_for(broadcast, timeout=0.1)
    await event_bus.unsubscribe_entered.wait()

    assert connection.is_active is False
    assert transport.room_connections == {}
    assert transport._room_cleanup_tasks
    assert event_bus.unsubscribe_tasks != [broadcast]

    event_bus.unsubscribe_waiter.set()
    await transport._drain_room_cleanup_tasks()
    assert transport._room_cleanup_tasks == {}


@pytest.mark.asyncio
async def test_last_disconnect_unsubscribe_cannot_cancel_new_room_admission():
    event_bus = FakeEventBus()
    event_bus.unsubscribe_waiter = asyncio.Event()
    transport = make_transport(event_bus=event_bus)
    old = await transport.open_connection("room-1")

    removal = asyncio.create_task(
        transport.remove_connection("room-1", old.connection_id)
    )
    await event_bus.unsubscribe_entered.wait()
    admission = asyncio.create_task(transport.open_connection("room-1"))
    await asyncio.sleep(0)

    assert "room-1" not in transport.room_connections
    assert removal.done()
    event_bus.unsubscribe_waiter.set()
    new = await admission
    await removal
    await transport._drain_room_cleanup_tasks()

    assert event_bus.unsubscribed == ["room-1"]
    assert event_bus.subscribed == ["room-1", "room-1"]
    assert transport.room_connections["room-1"][new.connection_id] is new


@pytest.mark.asyncio
async def test_broadcast_cleanup_serializes_unsubscribe_with_new_admission():
    event_bus = FakeEventBus()
    event_bus.unsubscribe_waiter = asyncio.Event()
    transport = make_transport(event_bus=event_bus)
    old = await transport.open_connection("room-1")
    old.close()

    broadcast = asyncio.create_task(
        transport.broadcast_frame_to_room("room-1", {"type": "update"})
    )
    await event_bus.unsubscribe_entered.wait()
    admission = asyncio.create_task(transport.open_connection("room-1"))
    await asyncio.sleep(0)

    assert "room-1" not in transport.room_connections
    assert broadcast.done()
    assert event_bus.unsubscribe_tasks != [broadcast]
    event_bus.unsubscribe_waiter.set()
    new = await admission
    await broadcast
    await transport._drain_room_cleanup_tasks()

    assert event_bus.unsubscribed == ["room-1"]
    assert event_bus.subscribed == ["room-1", "room-1"]
    assert transport.room_connections["room-1"][new.connection_id] is new


@pytest.mark.asyncio
async def test_broadcast_cleans_dead_last_connection_and_unsubscribes():
    event_bus = FakeEventBus()
    metrics = FakeMetrics()
    transport = make_transport(event_bus=event_bus, metrics=metrics)
    connection = await transport.open_connection("room-1")
    connection.close()

    await transport.broadcast_frame_to_room("room-1", {"type": "update"})
    await transport._drain_room_cleanup_tasks()

    assert transport.room_connections == {}
    assert transport.connection_rooms == {}
    assert event_bus.unsubscribed == ["room-1"]
    assert metrics.gauges[-1] == (
        "hybro_delivery_sse_connections",
        0,
        {"worker_id": "worker-1"},
    )


@pytest.mark.asyncio
async def test_close_all_connections_drains_pending_room_cleanup_tasks():
    event_bus = FakeEventBus()
    event_bus.unsubscribe_waiter = asyncio.Event()
    transport = make_transport(event_bus=event_bus)
    connection = await transport.open_connection("room-1")
    await transport.remove_connection("room-1", connection.connection_id)
    await event_bus.unsubscribe_entered.wait()

    close_all = asyncio.create_task(transport.close_all_connections())
    await asyncio.sleep(0)

    assert not close_all.done()
    assert transport._room_cleanup_tasks
    event_bus.unsubscribe_waiter.set()
    await close_all
    assert transport._room_cleanup_tasks == {}


@pytest.mark.asyncio
async def test_close_all_connections_clears_maps_unsubscribes_and_is_idempotent():
    event_bus = FakeEventBus()
    metrics = FakeMetrics()
    transport = make_transport(event_bus=event_bus, metrics=metrics)
    first = await transport.open_connection("room-1")
    second = await transport.open_connection("room-2")

    await transport.close_all_connections()
    await transport.close_all_connections()

    assert first.is_active is False
    assert second.is_active is False
    assert transport.room_connections == {}
    assert transport.connection_rooms == {}
    assert event_bus.unsubscribed == ["room-1", "room-2"]
    assert metrics.gauges[-1] == (
        "hybro_delivery_sse_connections",
        0,
        {"worker_id": "worker-1"},
    )


@pytest.mark.asyncio
async def test_draining_rejects_new_connections():
    transport = make_transport()
    transport.set_draining(True)

    with pytest.raises(ConnectionRefusedError):
        await transport.open_connection("room-1")
