import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.errors import TransientError
from common.observability import get_current_trace_id, trace_id_context
from delivery.config import DeliveryConfig
from delivery.event_bus import CrossInstanceEventBus
from delivery.sse.manager import SSETransportImpl
from delivery.types import RoomSubscriptionLimitExceeded

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def fixed_now():
    return NOW


def task_runner(coro, *, name=None):
    return asyncio.create_task(coro, name=name)


class FakeRedisPubSub:
    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self.subscribed: list[str] = []
        self.closed = 0
        self.ping_result = True
        self.publish_error: Exception | None = None
        self.subscribe_error: Exception | None = None
        self.subscribe_waiter: asyncio.Event | None = None
        self.queues: dict[str, asyncio.Queue] = {}

    async def publish(self, channel: str, message: str) -> None:
        assert isinstance(message, str)
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append((channel, message))

    async def subscribe(self, channel: str):
        if self.subscribe_waiter is not None:
            await self.subscribe_waiter.wait()
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscribed.append(channel)
        queue = self.queues.setdefault(channel, asyncio.Queue())

        async def iterator():
            while True:
                message = await queue.get()
                if message is StopAsyncIteration:
                    return
                if isinstance(message, Exception):
                    raise message
                yield message

        return iterator()

    async def ping(self) -> bool:
        return self.ping_result

    async def close(self) -> None:
        self.closed += 1


def make_bus(redis=None, config=None):
    return CrossInstanceEventBus(
        redis_pubsub=redis,
        config=config or DeliveryConfig(),
        instance_id="worker-1",
        task_runner=task_runner,
        now=fixed_now,
        sleeper=AsyncMock(),
    )


def decode_publish(redis: FakeRedisPubSub, index: int = 0):
    channel, message = redis.published[index]
    return channel, json.loads(message)


@pytest.mark.asyncio
async def test_publish_sse_uses_json_envelope_and_configured_channel():
    redis = FakeRedisPubSub()
    config = DeliveryConfig(redis_sse_channel_prefix="custom:sse:")
    bus = make_bus(redis=redis, config=config)

    await bus.publish_sse("room-1", {"type": "agent_response", "data": {"x": 1}})

    channel, envelope = decode_publish(redis)
    assert channel == "custom:sse:room-1"
    assert envelope == {
        "kind": "sse_event",
        "origin": "worker-1",
        "room_id": "room-1",
        "type": "agent_response",
        "data": {"x": 1},
        "frame": {"type": "agent_response", "data": {"x": 1}},
        "trace_id": None,
    }


@pytest.mark.asyncio
async def test_incoming_sse_self_origin_ignored_and_other_origin_delivered():
    delivered: list[tuple[str, dict]] = []
    bus = make_bus(redis=FakeRedisPubSub())
    bus.set_sse_callback(lambda room_id, frame: delivered.append((room_id, frame)))

    await bus.handle_sse_message(
        json.dumps(
            {
                "kind": "sse_event",
                "origin": "worker-1",
                "room_id": "room-1",
                "frame": {"type": "update"},
            }
        )
    )
    await bus.handle_sse_message(
        json.dumps(
            {
                "kind": "sse_event",
                "origin": "worker-2",
                "room_id": "room-1",
                "frame": {"type": "update"},
            }
        )
    )

    assert delivered == [("room-1", {"type": "update"})]


@pytest.mark.asyncio
async def test_incoming_legacy_sse_envelope_is_reconstructed():
    delivered: list[tuple[str, dict]] = []
    bus = make_bus(redis=FakeRedisPubSub())
    bus.set_sse_callback(lambda room_id, frame: delivered.append((room_id, frame)))

    await bus.handle_sse_message(
        json.dumps(
            {
                "kind": "sse_event",
                "origin": "worker-2",
                "room_id": "room-1",
                "type": "agent_response",
                "data": {"content": "hi"},
            }
        )
    )

    assert delivered == [
        (
            "room-1",
            {
                "type": "agent_response",
                "timestamp": NOW.isoformat(),
                "room_id": "room-1",
                "data": {"content": "hi"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_publish_and_handle_cancellation_use_configured_channel():
    redis = FakeRedisPubSub()
    config = DeliveryConfig(redis_cancel_channel="custom:cancel")
    bus = make_bus(redis=redis, config=config)
    cancelled: list[str] = []
    bus.set_cancellation_callback(lambda message_id: cancelled.append(message_id))

    await bus.publish_cancellation("msg-1")
    await bus.handle_cancellation_message(
        json.dumps(
            {"kind": "cancellation", "origin": "worker-2", "message_id": "msg-2"}
        )
    )

    channel, envelope = decode_publish(redis)
    assert channel == "custom:cancel"
    assert envelope["kind"] == "cancellation"
    assert envelope["origin"] == "worker-1"
    assert envelope["message_id"] == "msg-1"
    assert envelope["trace_id"] is None
    assert cancelled == ["msg-2"]


@pytest.mark.asyncio
async def test_cross_instance_callbacks_replace_ambient_trace_context():
    bus = make_bus(redis=FakeRedisPubSub())
    observed: list[tuple[str, str | None]] = []

    async def cancellation_callback(message_id: str) -> None:
        observed.append((message_id, get_current_trace_id()))

    bus.set_cancellation_callback(cancellation_callback)

    with trace_id_context("ambient-trace"):
        await bus.handle_cancellation_message(
            json.dumps(
                {
                    "kind": "cancellation",
                    "origin": "worker-2",
                    "message_id": "msg-1",
                }
            )
        )
        assert get_current_trace_id() == "ambient-trace"

    assert observed == [("msg-1", None)]


@pytest.mark.asyncio
async def test_cancellation_envelope_propagates_trace_context():
    redis = FakeRedisPubSub()
    bus = make_bus(redis=redis)
    observed: list[str | None] = []
    bus.set_cancellation_callback(
        lambda _message_id: observed.append(get_current_trace_id())
    )

    with trace_id_context("trace-123"):
        await bus.publish_cancellation("msg-1")

    _, envelope = decode_publish(redis)
    await bus.handle_cancellation_message(
        json.dumps(
            {
                **envelope,
                "origin": "worker-2",
            }
        )
    )

    assert envelope["trace_id"] == "trace-123"
    assert observed == ["trace-123"]


@pytest.mark.asyncio
async def test_cross_instance_callbacks_reject_malformed_trace_context():
    bus = make_bus(redis=FakeRedisPubSub())
    observed: list[str | None] = []
    bus.set_cancellation_callback(
        lambda _message_id: observed.append(get_current_trace_id())
    )

    with trace_id_context("ambient-trace"):
        for trace_id in ({"PRIVATE_TRACE": "payload"}, "x" * 129):
            await bus.handle_cancellation_message(
                json.dumps(
                    {
                        "kind": "cancellation",
                        "origin": "worker-2",
                        "message_id": "msg-1",
                        "trace_id": trace_id,
                    }
                )
            )
        assert get_current_trace_id() == "ambient-trace"

    assert observed == [None, None]


@pytest.mark.asyncio
async def test_publish_dead_letter_uses_configured_channel():
    redis = FakeRedisPubSub()
    config = DeliveryConfig(redis_dead_letter_channel="custom:dead")
    bus = make_bus(redis=redis, config=config)

    await bus.publish_dead_letter({"failure_stage": "fanout"})

    dead_channel, dead = decode_publish(redis)
    assert dead_channel == "custom:dead"
    assert dead == {"failure_stage": "fanout"}


@pytest.mark.asyncio
async def test_publish_dead_letter_propagates_pubsub_failure():
    redis = FakeRedisPubSub()
    redis.publish_error = TransientError("redis down")
    bus = make_bus(redis=redis)

    with pytest.raises(TransientError):
        await bus.publish_dead_letter({"failure_stage": "fanout"})


@pytest.mark.asyncio
async def test_no_redis_mode_is_noop():
    bus = make_bus(redis=None)

    await bus.start()
    await bus.publish_sse("room-1", {"type": "update"})
    await bus.publish_cancellation("msg-1")
    await bus.publish_dead_letter({"x": 1})
    await bus.subscribe_room("room-1")
    await bus.unsubscribe_room("room-1")
    await bus.stop()

    assert bus.is_connected is False


@pytest.mark.asyncio
async def test_subscribe_room_rejects_immediately_after_stop():
    bus = make_bus(redis=FakeRedisPubSub())
    await bus.stop()

    with pytest.raises(RuntimeError, match="stopped"):
        await asyncio.wait_for(bus.subscribe_room("room-1"), timeout=0.1)

    assert bus.desired_room_channels == set()
    assert bus._room_readiness == {}


@pytest.mark.asyncio
async def test_stop_racing_pending_subscribe_wakes_waiter_and_cleans_listener():
    redis = FakeRedisPubSub()
    redis.subscribe_waiter = asyncio.Event()
    bus = make_bus(redis=redis)

    subscriber = asyncio.create_task(bus.subscribe_room("room-1"))
    await asyncio.sleep(0)
    listener = bus._room_tasks["sse:room:room-1"]
    await asyncio.wait_for(bus.stop(), timeout=0.1)
    result = await asyncio.gather(subscriber, return_exceptions=True)

    assert isinstance(result[0], asyncio.CancelledError)
    assert listener.done()
    assert bus.desired_room_channels == set()
    assert bus._room_readiness == {}


@pytest.mark.asyncio
async def test_subscription_loop_stopped_before_start_resolves_readiness_and_cleans():
    bus = make_bus(redis=FakeRedisPubSub())
    channel = "sse:room:room-1"
    readiness = asyncio.get_running_loop().create_future()
    generation = object()
    bus._stopped = True
    bus._channel_generations[channel] = generation
    bus._room_channels["room-1"] = channel
    bus._room_readiness[channel] = readiness
    listener = asyncio.create_task(
        bus._subscription_loop(
            channel,
            "sse",
            generation=generation,
            readiness=readiness,
            room_id="room-1",
        )
    )
    bus._room_tasks[channel] = listener

    await listener

    with pytest.raises(RuntimeError, match="stopped before becoming ready"):
        await readiness
    assert bus.desired_room_channels == set()
    assert bus._room_readiness == {}


@pytest.mark.asyncio
async def test_subscribe_room_waits_for_shared_redis_readiness():
    redis = FakeRedisPubSub()
    redis.subscribe_waiter = asyncio.Event()
    bus = make_bus(redis=redis)

    first = asyncio.create_task(bus.subscribe_room("room-1"))
    second = asyncio.create_task(bus.subscribe_room("room-1"))
    await asyncio.sleep(0)

    assert not first.done()
    assert not second.done()
    assert len(bus.desired_room_channels) == 1

    redis.subscribe_waiter.set()
    await asyncio.gather(first, second)
    assert redis.subscribed == ["sse:room:room-1"]
    await bus.stop()


@pytest.mark.asyncio
async def test_subscribe_room_timeout_cleans_shared_task_and_allows_retry():
    redis = FakeRedisPubSub()
    redis.subscribe_waiter = asyncio.Event()
    config = DeliveryConfig(redis_room_subscription_ready_timeout_seconds=0.01)
    bus = make_bus(redis=redis, config=config)

    first = asyncio.create_task(bus.subscribe_room("room-1"))
    second = asyncio.create_task(bus.subscribe_room("room-1"))
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(result, TimeoutError) for result in results)
    assert bus.desired_room_channels == set()
    assert bus._room_readiness == {}

    redis.subscribe_waiter.set()
    await bus.subscribe_room("room-1")
    assert redis.subscribed == ["sse:room:room-1"]
    await bus.stop()


@pytest.mark.asyncio
async def test_initial_subscribe_failure_cleans_room_but_ready_disconnect_reconnects():
    redis = FakeRedisPubSub()
    redis.subscribe_error = RuntimeError("redis down")
    bus = make_bus(redis=redis)

    with pytest.raises(RuntimeError, match="redis down"):
        await bus.subscribe_room("room-1")
    assert bus.desired_room_channels == set()
    assert bus._room_readiness == {}

    redis.subscribe_error = None
    await bus.subscribe_room("room-1")
    await redis.queues["sse:room:room-1"].put(RuntimeError("disconnected"))
    for _ in range(10):
        await asyncio.sleep(0)
        if len(redis.subscribed) == 2:
            break

    assert redis.subscribed == ["sse:room:room-1", "sse:room:room-1"]
    assert "sse:room:room-1" in bus.desired_room_channels
    await bus.stop()


@pytest.mark.asyncio
async def test_cancelled_shared_readiness_waiter_does_not_cancel_subscription():
    redis = FakeRedisPubSub()
    redis.subscribe_waiter = asyncio.Event()
    bus = make_bus(redis=redis)

    owner = asyncio.create_task(bus.subscribe_room("room-1"))
    waiter = asyncio.create_task(bus.subscribe_room("room-1"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    redis.subscribe_waiter.set()
    await owner
    assert redis.subscribed == ["sse:room:room-1"]
    await bus.stop()


@pytest.mark.asyncio
async def test_cancelled_first_readiness_waiter_does_not_cancel_subscription():
    redis = FakeRedisPubSub()
    redis.subscribe_waiter = asyncio.Event()
    bus = make_bus(redis=redis)

    first = asyncio.create_task(bus.subscribe_room("room-1"))
    second = asyncio.create_task(bus.subscribe_room("room-1"))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert not second.done()
    assert bus.desired_room_channels == {"sse:room:room-1"}
    redis.subscribe_waiter.set()
    await second
    assert redis.subscribed == ["sse:room:room-1"]
    await bus.stop()


@pytest.mark.asyncio
async def test_unsubscribe_pending_room_wakes_all_readiness_waiters_immediately():
    redis = FakeRedisPubSub()
    redis.subscribe_waiter = asyncio.Event()
    config = DeliveryConfig(redis_room_subscription_ready_timeout_seconds=30)
    bus = make_bus(redis=redis, config=config)

    first = asyncio.create_task(bus.subscribe_room("room-1"))
    second = asyncio.create_task(bus.subscribe_room("room-1"))
    await asyncio.sleep(0)
    await bus.unsubscribe_room("room-1")
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert bus.desired_room_channels == set()
    assert bus._room_readiness == {}
    await bus.stop()


@pytest.mark.asyncio
async def test_redis_callback_overflow_tracks_and_finishes_listener_cleanup():
    redis = FakeRedisPubSub()
    config = DeliveryConfig(sse_connection_queue_maxsize=1)
    bus = make_bus(redis=redis, config=config)
    transport = SSETransportImpl(
        cancellation_watcher=MagicMock(),
        event_bus=bus,
        config=config,
        now=fixed_now,
        id_factory=lambda: "conn-1",
        instance_id="worker-1",
        task_runner=task_runner,
    )
    bus.set_sse_callback(transport.broadcast_frame_to_room)
    connection = await transport.open_connection("room-1")
    listener = bus._room_tasks["sse:room:room-1"]
    await connection.send_frame({"type": "seed"})

    await redis.queues["sse:room:room-1"].put(
        json.dumps(
            {
                "kind": "sse_event",
                "origin": "worker-2",
                "room_id": "room-1",
                "frame": {"type": "overflow"},
            }
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
        if not bus.desired_room_channels and not transport._room_cleanup_tasks:
            break

    assert connection.is_active is False
    assert listener.done()
    assert bus.desired_room_channels == set()
    assert transport._room_cleanup_tasks == {}
    await transport.close_all_connections()
    await bus.stop()


@pytest.mark.asyncio
async def test_cancelled_first_admission_cleans_room_subscription_and_capacity():
    redis = FakeRedisPubSub()
    redis.subscribe_waiter = asyncio.Event()
    config = DeliveryConfig(
        redis_room_subscription_production_limit=1,
        redis_subscription_reserved_connections=1,
        redis_max_connections=2,
    )
    bus = make_bus(redis=redis, config=config)
    transport = SSETransportImpl(
        cancellation_watcher=MagicMock(),
        event_bus=bus,
        config=config,
        now=fixed_now,
        id_factory=lambda: "conn-1",
        instance_id="worker-1",
        task_runner=task_runner,
    )

    admission = asyncio.create_task(transport.open_connection("room-1"))
    await asyncio.sleep(0)
    listener = bus._room_tasks["sse:room:room-1"]
    admission.cancel()
    with pytest.raises(asyncio.CancelledError):
        await admission
    await transport._drain_room_cleanup_tasks()

    assert transport.room_connections == {}
    assert bus.desired_room_channels == set()
    assert bus._room_readiness == {}
    assert listener.done()

    redis.subscribe_waiter.set()
    connection = await transport.open_connection("room-2")
    assert connection.room_id == "room-2"
    await transport.close_all_connections()
    await bus.stop()


@pytest.mark.asyncio
async def test_room_subscription_limit_and_custom_prefix():
    redis = FakeRedisPubSub()
    config = DeliveryConfig(
        redis_sse_channel_prefix="custom:sse:",
        redis_room_subscription_production_limit=2,
        redis_subscription_reserved_connections=1,
        redis_max_connections=3,
    )
    bus = make_bus(redis=redis, config=config)

    await bus.subscribe_room("room-1")
    await bus.subscribe_room("room-1")
    await bus.subscribe_room("room-2")
    with pytest.raises(RoomSubscriptionLimitExceeded):
        await bus.subscribe_room("room-3")

    assert set(bus.desired_room_channels) == {"custom:sse:room-1", "custom:sse:room-2"}
    await bus.unsubscribe_room("room-1")
    await bus.subscribe_room("room-3")
    assert "custom:sse:room-3" in bus.desired_room_channels

    await bus.stop()


@pytest.mark.asyncio
async def test_stale_listener_generation_cannot_clear_rapid_resubscribe_health():
    redis = FakeRedisPubSub()
    bus = make_bus(redis=redis)
    await bus.start()
    await asyncio.sleep(0)
    await bus.subscribe_room("room-race")
    channel = "sse:room:room-race"
    stale_generation = bus._channel_generations[channel]

    await bus.unsubscribe_room("room-race")
    await bus.subscribe_room("room-race")
    current_generation = bus._channel_generations[channel]
    assert current_generation is not stale_generation
    assert channel in bus._active_channels

    # Emulate the old listener reaching its finally block after the new
    # subscription has already become active.
    bus._mark_channel_inactive(channel, stale_generation)

    assert channel in bus._active_channels
    assert bus._active_generations[channel] is current_generation
    await bus.stop()


@pytest.mark.asyncio
async def test_default_room_subscription_limit_is_40():
    redis = FakeRedisPubSub()
    bus = make_bus(redis=redis)

    for index in range(40):
        await bus.subscribe_room(f"room-{index}")

    with pytest.raises(RoomSubscriptionLimitExceeded):
        await bus.subscribe_room("room-40")

    await bus.stop()


@pytest.mark.asyncio
async def test_malformed_messages_are_dropped_without_raising():
    bus = make_bus(redis=FakeRedisPubSub())

    await bus.handle_sse_message("{not json")
    await bus.handle_cancellation_message(json.dumps({"kind": "wrong"}))
