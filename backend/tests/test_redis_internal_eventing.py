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
from dal.redis.internal_eventing import RedisInternalEventTransport


class RemoteExampleEvent(BaseModel):
    event_type: Literal["remote_example"] = "remote_example"
    value: int


class FakeRedisPubSub:
    def __init__(self) -> None:
        self.published = []
        self.queue = asyncio.Queue()
        self.closed = False
        self.pings = 0

    async def publish(self, channel, message):
        self.published.append((channel, message))

    async def subscribe(self, channel):
        async def messages():
            while True:
                yield await self.queue.get()

        return messages()

    async def ping(self):
        self.pings += 1
        return True

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_redis_internal_transport_publish_receive_health_and_independent_close():
    redis = FakeRedisPubSub()
    received = []
    transport = RedisInternalEventTransport(
        redis_pubsub=redis,
        channel="custom:internal",
        dead_letter_channel="custom:dlt",
    )

    async def receive(message):
        received.append(message)

    await transport.start(receive)
    await asyncio.sleep(0)
    await redis.queue.put("remote-envelope")
    for _ in range(5):
        if received:
            break
        await asyncio.sleep(0)
    await transport.publish("local-envelope")
    await transport.publish_dead_letter("dead-letter")

    assert received == ["remote-envelope"]
    assert transport.is_connected
    assert redis.published == [
        ("custom:internal", "local-envelope"),
        ("custom:dlt", "dead-letter"),
    ]

    await transport.stop()
    await transport.stop()
    assert redis.closed
    assert not transport.is_connected


class BlockingRedisPubSub:
    async def publish(self, _channel, _message):
        await asyncio.Event().wait()

    async def subscribe(self, _channel):
        await asyncio.Event().wait()

    async def ping(self):
        await asyncio.Event().wait()

    async def close(self):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_redis_io_and_readiness_timeouts_bound_blocking_client():
    redis = BlockingRedisPubSub()

    async def sleeper(_delay):
        await asyncio.sleep(0)

    async def receive(_message):
        return None

    transport = RedisInternalEventTransport(
        redis_pubsub=redis,
        subscription_ready_timeout=0.03,
        io_timeout=0.01,
        reconnect_delay=0.01,
        reconnect_max_delay=0.01,
        sleeper=sleeper,
    )
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    await transport.start(receive)
    assert loop.time() - started_at < 0.15
    assert not transport.is_connected

    with pytest.raises(TimeoutError):
        await transport.publish("event")
    with pytest.raises(TimeoutError):
        await transport.publish_dead_letter("dlt")
    await asyncio.wait_for(transport.refresh_health(), timeout=0.05)
    assert not transport.is_connected
    await asyncio.wait_for(transport.stop(), timeout=0.05)


@pytest.mark.asyncio
async def test_blocking_subscription_iterator_close_does_not_block_stop():
    close_started = asyncio.Event()

    class BlockingIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

        async def aclose(self):
            close_started.set()
            await asyncio.Event().wait()

    class RedisWithBlockingIteratorClose(FakeRedisPubSub):
        async def subscribe(self, _channel):
            return BlockingIterator()

    redis = RedisWithBlockingIteratorClose()
    transport = RedisInternalEventTransport(
        redis_pubsub=redis,
        io_timeout=0.01,
        subscription_ready_timeout=0.02,
    )

    async def receive(_message):
        return None

    await transport.start(receive)
    await asyncio.wait_for(transport.stop(), timeout=0.05)

    assert close_started.is_set()
    assert redis.closed


@pytest.mark.asyncio
async def test_stop_ingress_shields_admitted_remote_multi_handler_callback():
    redis = FakeRedisPubSub()
    transport = RedisInternalEventTransport(
        redis_pubsub=redis,
        io_timeout=0.2,
        subscription_ready_timeout=0.1,
    )
    registry = EventModelRegistry()
    registry.register("remote_example", RemoteExampleEvent)
    bus = BoundedInternalEventBus(
        registry=registry,
        instance_id="instance-a",
        now=lambda: datetime(2025, 1, 1, tzinfo=UTC),
        transport=transport,
        config=EventingConfig(
            handler_queue_maxsize=2,
            enqueue_timeout_seconds=0.1,
            shutdown_timeout_seconds=0.2,
            dead_letter_memory_maxlen=10,
        ),
    )
    observed = [[], []]
    bus.register_handler(
        "remote_example", lambda event: observed[0].append(event.value)
    )
    bus.register_handler(
        "remote_example", lambda event: observed[1].append(event.value)
    )
    first_state = bus._handlers["remote_example"][0]
    original_put = first_state.queue.put
    put_entered = asyncio.Event()
    release_put = asyncio.Event()

    async def paused_put(item):
        put_entered.set()
        await release_put.wait()
        await original_put(item)

    first_state.queue.put = paused_put
    await bus.start()
    envelope = EventEnvelope(
        origin="instance-b",
        event_type="remote_example",
        event={"event_type": "remote_example", "value": 17},
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
    )
    await redis.queue.put(envelope.model_dump_json())
    await put_entered.wait()

    stop_task = asyncio.create_task(bus.stop())
    await asyncio.sleep(0)
    release_put.set()
    await asyncio.wait_for(stop_task, timeout=0.5)

    assert observed == [[17], [17]]
    assert transport._task is None
    assert transport._callback_task is None
    assert bus.worker_tasks == ()
    assert not [
        task
        for task in asyncio.all_tasks()
        if not task.done() and task.get_name() == "eventing-redis-callback"
    ]


@pytest.mark.asyncio
async def test_redis_internal_transport_uses_independent_default_dlt():
    transport = RedisInternalEventTransport(redis_pubsub=FakeRedisPubSub())
    assert transport.dead_letter_channel == "eventing:dead_letter"
    await transport.stop()


@pytest.mark.asyncio
async def test_redis_internal_transport_reconnects_after_subscribe_failure():
    redis = FakeRedisPubSub()
    attempts = 0

    async def subscribe(_channel):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")

        async def messages():
            await asyncio.Event().wait()
            if False:
                yield ""

        return messages()

    redis.subscribe = subscribe
    sleeps = []

    async def sleeper(delay):
        sleeps.append(delay)
        await asyncio.sleep(0)

    transport = RedisInternalEventTransport(
        redis_pubsub=redis,
        reconnect_delay=0.01,
        reconnect_max_delay=0.02,
        sleeper=sleeper,
    )

    async def receive(_message):
        return None

    await transport.start(receive)
    for _ in range(10):
        if attempts >= 2:
            break
        await asyncio.sleep(0)

    assert attempts >= 2
    assert sleeps == [0.01]
    await transport.stop()
