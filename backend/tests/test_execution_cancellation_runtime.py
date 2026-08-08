import asyncio
import json

import pytest

from common.errors import TransientError
from execution.cancellation import (
    CancellationConfig,
    CancellationRuntime,
    CancellationStartupPolicy,
)
from execution.cancellation.transport import RedisCancellationTransport
from execution.cancellation.watcher import CancellationWatcher


class FakeTimer:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeRedisKV:
    def __init__(self):
        self.set_calls = []
        self.exists_calls = []
        self.exists_result = False
        self.set_error = None
        self.exists_error = None
        self.ping_result = True
        self.ping_error = None
        self.closed = 0

    async def set(self, key, value, ttl=None):
        self.set_calls.append((key, value, ttl))
        if self.set_error:
            raise self.set_error

    async def exists(self, key):
        self.exists_calls.append(key)
        if self.exists_error:
            raise self.exists_error
        return self.exists_result

    async def ping(self):
        if self.ping_error:
            raise self.ping_error
        return self.ping_result

    async def close(self):
        self.closed += 1


class FakeTransport:
    def __init__(self):
        self.published = []
        self.callback = None
        self.is_connected = True
        self.started = 0
        self.stopped = 0

    async def start(self, callback):
        self.callback = callback
        self.started += 1

    async def stop(self):
        self.stopped += 1

    async def publish(self, message_id):
        self.published.append(message_id)

    async def refresh_health(self):
        return None


class BlockingChangeStream:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if isinstance(item, Exception):
            raise item
        return item


class FakeCollection:
    def __init__(self, stream=None, error=None):
        self.stream = stream or BlockingChangeStream()
        self.error = error

    def watch(self, pipeline=None, **kwargs):
        del pipeline, kwargs
        if self.error:
            raise self.error
        return self.stream


def task_runner(coro, *, name=None):
    return asyncio.create_task(coro, name=name)


def make_runtime(
    *, collection=None, redis=None, transport=None, config=None, timer=None
):
    return CancellationRuntime(
        collection=collection or FakeCollection(),
        redis_kv=redis,
        transport=transport,
        config=config or CancellationConfig(),
        task_runner=task_runner,
        timer=timer,
    )


def test_startup_policy_rejects_unsafe_degraded_mode():
    with pytest.raises(ValueError):
        CancellationStartupPolicy(
            redis_expected=True,
            multi_worker=False,
            allow_degraded_change_stream=True,
        )


def test_active_token_registry_does_not_fail_fast_on_capacity():
    runtime = make_runtime()
    first = runtime.create_token("msg-1")
    second = runtime.create_token("msg-2")
    assert runtime.get_token("msg-1") is first
    assert runtime.get_token("msg-2") is second
    assert runtime.active_token_count == 2


def test_same_message_id_reuses_active_token():
    runtime = make_runtime()
    first = runtime.create_token("msg-1")
    assert runtime.create_token("msg-1") is first
    assert runtime.active_token_count == 1


@pytest.mark.parametrize(
    "terminal_outcome", ["completed", "failed", "canceled", "rejected"]
)
def test_terminal_owner_release_clears_active_count(terminal_outcome):
    runtime = make_runtime()
    token = runtime.create_token(f"msg-{terminal_outcome}")
    assert runtime.active_token_count == 1
    assert runtime.release_token(f"msg-{terminal_outcome}", token) is True
    assert runtime.active_token_count == 0


def test_release_is_identity_safe_for_none_and_missing_keys():
    runtime = make_runtime()
    first = runtime.create_token("msg-1")
    assert runtime.release_token("missing", None) is False
    assert runtime.release_token("missing", object()) is False
    assert runtime.release_token("msg-1", None) is False
    assert runtime.release_token("msg-1", object()) is False
    assert runtime.get_token("msg-1") is first
    assert runtime.release_token("msg-1", first) is True
    assert runtime.get_token("msg-1") is None


def test_release_active_token_preserves_tombstone():
    runtime = make_runtime()
    token = runtime.create_token("msg-1")
    runtime.signal_local("msg-1")

    assert runtime.release_active_token("msg-1") is True
    assert runtime.get_token("msg-1") is None
    assert runtime.is_cancelled("msg-1") is True
    assert token.is_cancelled is True
    assert runtime.release_active_token("msg-1") is False


def test_cancel_before_create_pre_signals_token():
    runtime = make_runtime()
    runtime.signal_local("msg-1")
    assert runtime.create_token("msg-1").is_cancelled is True


def test_tombstone_ttl_is_separate_from_active_token():
    timer = FakeTimer()
    runtime = make_runtime(
        config=CancellationConfig(ttl_seconds=11),
        timer=timer,
    )
    token = runtime.create_token("msg-1")
    runtime.signal_local("msg-1")
    timer.advance(12)
    assert runtime.is_cancelled("msg-1") is False
    assert runtime.get_token("msg-1") is token
    assert token.is_cancelled is True


def test_clear_only_removes_tombstone():
    runtime = make_runtime()
    token = runtime.create_token("msg-1")
    runtime.signal_local("msg-1")
    runtime.clear_cancellation("msg-1")
    assert runtime.is_cancelled("msg-1") is False
    assert runtime.get_token("msg-1") is token


@pytest.mark.asyncio
async def test_signal_writes_l2_and_cross_instance_transport():
    redis = FakeRedisKV()
    transport = FakeTransport()
    runtime = make_runtime(
        redis=redis,
        transport=transport,
        config=CancellationConfig(ttl_seconds=11, redis_key_prefix="cx:"),
    )
    result = await runtime.signal("msg-1")
    assert result.succeeded is True
    assert result.kv_configured is True
    assert result.pubsub_configured is True
    assert redis.set_calls == [("cx:msg-1", "1", 11)]
    assert transport.published == ["msg-1"]


@pytest.mark.asyncio
async def test_signal_keeps_local_state_when_external_services_fail():
    redis = FakeRedisKV()
    redis.set_error = TransientError("redis down")
    transport = FakeTransport()

    async def fail(_message_id):
        raise RuntimeError("pubsub down")

    transport.publish = fail
    runtime = make_runtime(redis=redis, transport=transport)
    result = await runtime.signal("msg-1")
    assert result.succeeded is False
    assert result.kv_succeeded is False
    assert result.pubsub_succeeded is False
    assert runtime.is_cancelled("msg-1") is True


@pytest.mark.asyncio
async def test_signal_without_external_redis_is_successful_single_worker_propagation():
    runtime = make_runtime()

    result = await runtime.signal("msg-local")

    assert result.succeeded is True
    assert result.kv_configured is False
    assert result.pubsub_configured is False


@pytest.mark.asyncio
async def test_check_uses_redis_l2_and_populates_local_state():
    redis = FakeRedisKV()
    redis.exists_result = True
    runtime = make_runtime(
        redis=redis,
        config=CancellationConfig(redis_key_prefix="cx:"),
    )
    assert await runtime.check_cancelled("msg-1") is True
    assert redis.exists_calls == ["cx:msg-1"]
    assert await runtime.check_cancelled("msg-1") is True
    assert redis.exists_calls == ["cx:msg-1"]


@pytest.mark.asyncio
async def test_redis_tombstone_hydration_pre_signals_active_token():
    redis = FakeRedisKV()
    redis.exists_result = True
    runtime = make_runtime(redis=redis)
    token = runtime.create_token("msg-1")

    assert await runtime.check_cancelled("msg-1") is True
    assert token.is_cancelled is True


@pytest.mark.asyncio
async def test_remote_callback_signals_token_and_writes_l2():
    redis = FakeRedisKV()
    runtime = make_runtime(redis=redis)
    token = runtime.create_token("msg-1")
    await runtime.handle_remote_cancellation("msg-1")
    assert token.is_cancelled is True
    assert redis.set_calls == [("cancelled:msg-1", "1", 3600)]


@pytest.mark.asyncio
async def test_stop_clears_registry_and_restart_reowns_kv_close():
    redis = FakeRedisKV()
    transport = FakeTransport()
    runtime = make_runtime(redis=redis, transport=transport)

    await runtime.start()
    runtime.create_token("msg-1")
    await runtime.stop()
    assert runtime.active_token_count == 0
    assert redis.closed == 1

    await runtime.start()
    runtime.create_token("msg-2")
    await runtime.stop()
    assert runtime.active_token_count == 0
    assert redis.closed == 2
    assert transport.started == 2
    assert transport.stopped == 2


@pytest.mark.asyncio
async def test_start_base_exception_stops_watcher_and_transport_before_propagating():
    transport = FakeTransport()
    runtime = make_runtime(transport=transport)
    watcher_stopped = 0

    async def cancel_start():
        raise asyncio.CancelledError

    async def stop_watcher():
        nonlocal watcher_stopped
        watcher_stopped += 1

    runtime._watcher.start = cancel_start
    runtime._watcher.stop = stop_watcher

    with pytest.raises(asyncio.CancelledError):
        await runtime.start()

    assert watcher_stopped == 1
    assert transport.started == 1
    assert transport.stopped == 1


@pytest.mark.asyncio
async def test_redis_health_requires_kv_and_transport():
    redis = FakeRedisKV()
    transport = FakeTransport()
    runtime = make_runtime(redis=redis, transport=transport)

    await runtime.refresh_health()
    assert runtime.redis_connected is True

    redis.ping_result = False
    await runtime.refresh_health()
    assert runtime.redis_connected is False

    redis.ping_result = True
    transport.is_connected = False
    await runtime.refresh_health()
    assert runtime.redis_connected is False


@pytest.mark.asyncio
async def test_runtime_starts_and_stops_watcher_before_external_clients_close():
    stream = BlockingChangeStream()
    redis = FakeRedisKV()
    transport = FakeTransport()
    runtime = make_runtime(
        collection=FakeCollection(stream), redis=redis, transport=transport
    )
    await runtime.start()
    await stream.queue.put(
        {"_id": {"token": 1}, "fullDocument": {"message_id": "msg-1"}}
    )
    await asyncio.sleep(0)
    assert runtime.is_cancelled("msg-1") is True
    assert runtime.change_stream_connected is True
    await runtime.stop()
    assert runtime.change_stream_connected is False
    assert transport.stopped == 1
    assert redis.closed == 1


@pytest.mark.asyncio
async def test_watcher_preserves_resume_token_across_ordinary_failures():  # noqa: C901
    class FailingStream:
        def __init__(self, change=None):
            self.change = change

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.change is not None:
                change, self.change = self.change, None
                return change
            raise RuntimeError("consume failed")

    class ReconnectingCollection:
        def __init__(self):
            self.calls = []
            self.fourth = asyncio.Event()

        def watch(self, pipeline=None, **kwargs):
            del pipeline
            self.calls.append(kwargs)
            if len(self.calls) >= 4:
                self.fourth.set()
            change = None
            if len(self.calls) == 1:
                change = {
                    "_id": {"resume": 1},
                    "fullDocument": {"message_id": "msg-1"},
                }
            return FailingStream(change)

    async def no_delay(_delay):
        await asyncio.sleep(0)

    collection = ReconnectingCollection()
    watcher = CancellationWatcher(
        collection=collection,
        signal_local=lambda _message_id: None,
        config=CancellationConfig(change_stream_jitter_fraction=0),
        task_runner=task_runner,
        sleeper=no_delay,
    )
    await watcher.start()
    await asyncio.wait_for(collection.fourth.wait(), timeout=0.2)
    await watcher.stop()

    assert collection.calls[1]["resume_after"] == {"resume": 1}
    assert collection.calls[2]["resume_after"] == {"resume": 1}
    assert collection.calls[3]["resume_after"] == {"resume": 1}


@pytest.mark.asyncio
async def test_watcher_resets_resume_token_only_for_nonresumable_label():  # noqa: C901
    class NonResumableError(RuntimeError):
        def has_error_label(self, label):
            return label == "NonResumableChangeStreamError"

    class FailingStream:
        def __init__(self, change=None):
            self.change = change

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.change is not None:
                change, self.change = self.change, None
                return change
            raise NonResumableError("resume point expired")

    class ReconnectingCollection:
        def __init__(self):
            self.calls = []
            self.second = asyncio.Event()

        def watch(self, pipeline=None, **kwargs):
            del pipeline
            self.calls.append(kwargs)
            if len(self.calls) >= 2:
                self.second.set()
            change = None
            if len(self.calls) == 1:
                change = {
                    "_id": {"resume": 1},
                    "fullDocument": {"message_id": "msg-1"},
                }
            return FailingStream(change)

    async def no_delay(_delay):
        await asyncio.sleep(0)

    collection = ReconnectingCollection()
    watcher = CancellationWatcher(
        collection=collection,
        signal_local=lambda _message_id: None,
        config=CancellationConfig(change_stream_jitter_fraction=0),
        task_runner=task_runner,
        sleeper=no_delay,
    )
    await watcher.start()
    await asyncio.wait_for(collection.second.wait(), timeout=0.2)
    await watcher.stop()

    assert "resume_after" not in collection.calls[1]


class FakePubSub:
    def __init__(self):
        self.published = []

    async def publish(self, channel, message):
        self.published.append((channel, message))

    async def ping(self):
        return True

    async def subscribe(self, channel):
        del channel
        return BlockingMessages()

    async def close(self):
        return None


class BlockingMessages:
    def __init__(self):
        self.queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_redis_transport_start_readiness_is_timeout_bounded():
    class HangingPubSub(FakePubSub):
        async def subscribe(self, channel):
            del channel
            await asyncio.Event().wait()

    transport = RedisCancellationTransport(
        redis_pubsub=HangingPubSub(),
        config=CancellationConfig(
            redis_subscription_ready_timeout_seconds=0.02,
            redis_io_timeout_seconds=0.01,
            redis_reconnect_delay=0.01,
            redis_reconnect_max_delay=0.01,
        ),
        instance_id="worker-1",
    )
    await asyncio.wait_for(transport.start(lambda _message_id: None), timeout=0.1)
    await transport.stop()


@pytest.mark.asyncio
async def test_redis_transport_dispatches_cross_instance_callback():
    pubsub = FakePubSub()
    transport = RedisCancellationTransport(
        redis_pubsub=pubsub,
        config=CancellationConfig(),
        instance_id="worker-1",
    )
    observed = []

    async def callback(message_id):
        observed.append(message_id)

    transport._callback = callback
    await transport._handle_message(
        json.dumps(
            {
                "kind": "cancellation",
                "origin": "worker-2",
                "message_id": "msg-1",
            }
        )
    )
    await transport._handle_message(
        json.dumps(
            {
                "kind": "cancellation",
                "origin": "worker-1",
                "message_id": "self-message",
            }
        )
    )
    assert observed == ["msg-1"]


@pytest.mark.asyncio
async def test_redis_transport_preserves_cancellation_envelope_and_channel():
    pubsub = FakePubSub()
    transport = RedisCancellationTransport(
        redis_pubsub=pubsub,
        config=CancellationConfig(redis_channel="custom:cancel"),
        instance_id="worker-1",
    )
    await transport.publish("msg-1")
    channel, raw = pubsub.published[0]
    envelope = json.loads(raw)
    assert channel == "custom:cancel"
    assert envelope["kind"] == "cancellation"
    assert envelope["origin"] == "worker-1"
    assert envelope["message_id"] == "msg-1"
