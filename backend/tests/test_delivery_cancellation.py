import asyncio

import pytest

from common.errors import TransientError
from delivery.config import DeliveryConfig
from delivery.sse.cancellation_watcher import CancellationWatcher


class FakeTimer:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeRedisKV:
    def __init__(self):
        self.set_calls: list[tuple[str, str, int | None]] = []
        self.exists_calls: list[str] = []
        self.exists_result = False
        self.set_error: Exception | None = None
        self.exists_error: Exception | None = None

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self.set_calls.append((key, value, ttl))
        if self.set_error is not None:
            raise self.set_error

    async def exists(self, key: str) -> bool:
        self.exists_calls.append(key)
        if self.exists_error is not None:
            raise self.exists_error
        return self.exists_result


class FakeEventBus:
    def __init__(self):
        self.cancellations: list[str] = []
        self.error: Exception | None = None

    async def publish_cancellation(self, message_id: str) -> None:
        self.cancellations.append(message_id)
        if self.error is not None:
            raise self.error


class BlockingChangeStream:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.entered = asyncio.Event()
        self.exited = False

    async def __aenter__(self):
        self.entered.set()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is StopAsyncIteration:
            raise StopAsyncIteration
        if isinstance(item, Exception):
            raise item
        return item


class FakeCollection:
    def __init__(self, stream=None, error: Exception | None = None):
        self.stream = stream or BlockingChangeStream()
        self.error = error
        self.watch_calls: list[tuple[list[dict] | None, dict]] = []

    def watch(self, pipeline=None, **kwargs):
        self.watch_calls.append((pipeline, kwargs))
        if self.error is not None:
            raise self.error
        return self.stream


def task_runner(coro, *, name=None):
    return asyncio.create_task(coro, name=name)


def make_watcher(
    *,
    collection=None,
    redis=None,
    event_bus=None,
    config=None,
    timer=None,
):
    return CancellationWatcher(
        collection=collection or FakeCollection(),
        redis_kv=redis,
        event_bus=event_bus,
        config=config or DeliveryConfig(),
        task_runner=task_runner,
        timer=timer,
    )


def test_cancel_message_marks_l1_and_signals_existing_token():
    watcher = make_watcher()
    token = watcher.create_token("msg-1")

    watcher.cancel_message("msg-1")

    assert watcher.is_cancelled("msg-1") is True
    assert token.is_cancelled is True


def test_create_token_pre_signals_if_already_cancelled():
    watcher = make_watcher()
    watcher.cancel_message("msg-1")

    token = watcher.create_token("msg-1")

    assert token.is_cancelled is True


def test_cancellation_token_cache_uses_custom_ttl():
    timer = FakeTimer()
    watcher = make_watcher(
        config=DeliveryConfig(cancellation_ttl_seconds=11),
        timer=timer,
    )

    token = watcher.create_token("msg-1")
    assert watcher.get_token("msg-1") is token

    timer.advance(12)
    assert watcher.get_token("msg-1") is None


@pytest.mark.asyncio
async def test_mark_cancelled_writes_l2_and_publishes_with_custom_config():
    redis = FakeRedisKV()
    event_bus = FakeEventBus()
    watcher = make_watcher(
        redis=redis,
        event_bus=event_bus,
        config=DeliveryConfig(
            redis_cancel_key_prefix="cx:", cancellation_ttl_seconds=11
        ),
    )

    await watcher.mark_cancelled("msg-1")

    assert watcher.is_cancelled("msg-1") is True
    assert redis.set_calls == [("cx:msg-1", "1", 11)]
    assert event_bus.cancellations == ["msg-1"]


@pytest.mark.asyncio
async def test_mark_cancelled_keeps_local_state_when_redis_or_bus_fails():
    redis = FakeRedisKV()
    redis.set_error = TransientError("redis down")
    event_bus = FakeEventBus()
    event_bus.error = RuntimeError("pubsub down")
    watcher = make_watcher(redis=redis, event_bus=event_bus)

    await watcher.mark_cancelled("msg-1")

    assert watcher.is_cancelled("msg-1") is True
    assert event_bus.cancellations == ["msg-1"]


@pytest.mark.asyncio
async def test_check_cancelled_uses_l1_then_redis_l2_with_custom_prefix():
    redis = FakeRedisKV()
    redis.exists_result = True
    watcher = make_watcher(
        redis=redis,
        config=DeliveryConfig(redis_cancel_key_prefix="cx:"),
    )

    assert await watcher.check_cancelled("msg-1") is True
    assert redis.exists_calls == ["cx:msg-1"]
    assert await watcher.check_cancelled("msg-1") is True
    assert redis.exists_calls == ["cx:msg-1"]


@pytest.mark.asyncio
async def test_check_cancelled_redis_failure_returns_l1_result_without_raising():
    redis = FakeRedisKV()
    redis.exists_error = TransientError("redis down")
    watcher = make_watcher(redis=redis)

    assert await watcher.check_cancelled("msg-1") is False


@pytest.mark.asyncio
async def test_remote_cancellation_marks_l1_token_and_best_effort_l2():
    redis = FakeRedisKV()
    watcher = make_watcher(
        redis=redis,
        config=DeliveryConfig(redis_cancel_key_prefix="cx:"),
    )
    token = watcher.create_token("msg-1")

    await watcher.handle_remote_cancellation("msg-1")

    assert watcher.is_cancelled("msg-1") is True
    assert token.is_cancelled is True
    assert redis.set_calls == [("cx:msg-1", "1", 3600)]


def test_clear_cancellation_removes_l1_and_token():
    watcher = make_watcher()
    watcher.cancel_message("msg-1")
    watcher.create_token("msg-1")

    watcher.clear_cancellation("msg-1")

    assert watcher.is_cancelled("msg-1") is False
    assert watcher.get_token("msg-1") is None


@pytest.mark.asyncio
async def test_change_stream_insert_event_marks_cancellation():
    stream = BlockingChangeStream()
    collection = FakeCollection(stream=stream)
    watcher = make_watcher(collection=collection)

    await watcher.start()
    await stream.queue.put(
        {"_id": {"token": 1}, "fullDocument": {"message_id": "msg-1"}}
    )
    await asyncio.sleep(0)

    assert watcher.is_cancelled("msg-1") is True
    assert watcher.change_stream_connected is True

    await watcher.stop()
    assert watcher.change_stream_connected is False


@pytest.mark.asyncio
async def test_initial_watch_setup_failure_is_visible_to_startup_and_stop_resets():
    watcher = make_watcher(
        collection=FakeCollection(error=RuntimeError("watch failed"))
    )

    with pytest.raises(RuntimeError, match="watch failed"):
        await watcher.start()

    await watcher.stop()
    assert watcher.change_stream_connected is False


@pytest.mark.asyncio
async def test_change_stream_backoff_uses_custom_config_after_listen_failure():
    stream = BlockingChangeStream()
    collection = FakeCollection(stream=stream)
    sleep_calls: list[float] = []

    async def sleeper(delay: float):
        sleep_calls.append(delay)
        raise asyncio.CancelledError

    watcher = CancellationWatcher(
        collection=collection,
        redis_kv=None,
        event_bus=None,
        config=DeliveryConfig(
            cs_backoff_base=0.2,
            cs_backoff_max=0.8,
            cs_backoff_factor=3.0,
            cs_jitter_fraction=0.0,
        ),
        task_runner=task_runner,
        sleeper=sleeper,
    )

    await watcher.start()
    await stream.queue.put(RuntimeError("listen failed"))
    await asyncio.sleep(0)
    await watcher.stop()

    assert sleep_calls == [0.2]
