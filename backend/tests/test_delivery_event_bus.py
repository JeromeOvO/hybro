import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.errors import TransientError
from delivery.config import DeliveryConfig
from delivery.event_bus import CrossInstanceEventBus
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
        self.queues: dict[str, asyncio.Queue] = {}

    async def publish(self, channel: str, message: str) -> None:
        assert isinstance(message, str)
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append((channel, message))

    async def subscribe(self, channel: str):
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
        json.dumps({"kind": "cancellation", "origin": "worker-2", "message_id": "msg-2"})
    )

    channel, envelope = decode_publish(redis)
    assert channel == "custom:cancel"
    assert envelope["kind"] == "cancellation"
    assert envelope["origin"] == "worker-1"
    assert envelope["message_id"] == "msg-1"
    assert cancelled == ["msg-2"]


@pytest.mark.asyncio
async def test_publish_internal_and_dead_letter_use_configured_channels():
    redis = FakeRedisPubSub()
    config = DeliveryConfig(
        redis_internal_channel="custom:internal",
        redis_dead_letter_channel="custom:dead",
    )
    bus = make_bus(redis=redis, config=config)

    event = MagicMock()
    event.event_type = "message_committed"
    event.model_dump.return_value = {"event_type": "message_committed"}
    await bus.publish_internal(event)
    await bus.publish_dead_letter({"failure_stage": "fanout"})

    internal_channel, internal = decode_publish(redis, 0)
    dead_channel, dead = decode_publish(redis, 1)
    assert internal_channel == "custom:internal"
    assert internal == {
        "kind": "internal_event",
        "origin": "worker-1",
        "event_type": "message_committed",
        "event": {"event_type": "message_committed"},
        "trace_id": None,
    }
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
    await bus.handle_internal_message(json.dumps({"kind": "wrong"}))
