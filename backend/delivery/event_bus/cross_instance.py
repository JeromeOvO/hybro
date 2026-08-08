import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from common.observability import get_current_trace_id, trace_id_context
from common.protocols import RedisPubSub
from delivery.config import DeliveryConfig
from delivery.types import RoomSubscriptionLimitExceeded, TaskRunner


class CrossInstanceEventBus:
    def __init__(
        self,
        *,
        redis_pubsub: RedisPubSub | None,
        config: DeliveryConfig,
        instance_id: str,
        task_runner: TaskRunner,
        now: Callable[[], datetime],
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.redis_pubsub = redis_pubsub
        self.config = config
        self.instance_id = instance_id
        self._task_runner = task_runner
        self._now = now
        self._sleeper = sleeper
        self._sse_callback: Callable[[str, dict[str, Any]], Any] | None = None
        self._room_tasks: dict[str, asyncio.Task] = {}
        self._room_channels: dict[str, str] = {}
        self._room_readiness: dict[str, asyncio.Future[None]] = {}
        self._channel_generations: dict[str, object] = {}
        self._active_generations: dict[str, object] = {}
        self._active_channels: set[str] = set()
        self._stopped = False
        self._redis_reachable = False

    @property
    def desired_room_channels(self) -> set[str]:
        return set(self._room_tasks)

    @property
    def is_connected(self) -> bool:
        if self.redis_pubsub is None:
            return False
        return self._redis_reachable and set(self._room_tasks).issubset(
            self._active_channels
        )

    def set_sse_callback(self, callback: Callable[[str, dict[str, Any]], Any]) -> None:
        self._sse_callback = callback

    async def start(self) -> None:
        self._stopped = False
        if self.redis_pubsub is None:
            self._redis_reachable = False
            return
        await self.refresh_health()

    async def stop(self) -> None:
        self._stopped = True
        tasks = list(self._room_tasks.values())
        self._room_tasks.clear()
        self._room_channels.clear()
        readiness = list(self._room_readiness.values())
        self._room_readiness.clear()
        self._channel_generations.clear()
        self._active_generations.clear()
        for future in readiness:
            if not future.done():
                future.cancel()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._active_channels.clear()
        self._redis_reachable = False
        if self.redis_pubsub is not None:
            await self.redis_pubsub.close()

    async def refresh_health(self) -> None:
        if self.redis_pubsub is None:
            self._redis_reachable = False
            return
        try:
            self._redis_reachable = await self.redis_pubsub.ping()
        except Exception:
            self._redis_reachable = False

    async def publish_sse(self, room_id: str, frame: dict[str, Any]) -> None:
        if self.redis_pubsub is None:
            return
        envelope = {
            "kind": "sse_event",
            "origin": self.instance_id,
            "room_id": room_id,
            "type": frame.get("type"),
            "data": frame.get("data"),
            "frame": frame,
            "trace_id": get_current_trace_id(),
        }
        await self.redis_pubsub.publish(
            self._room_channel(room_id),
            json.dumps(envelope),
        )

    async def publish_dead_letter(self, envelope: dict[str, Any]) -> None:
        if self.redis_pubsub is None:
            return
        await self.redis_pubsub.publish(
            self.config.redis_dead_letter_channel,
            json.dumps(envelope),
        )

    async def subscribe_room(self, room_id: str) -> None:
        if self._stopped:
            raise RuntimeError("Cross-instance event bus is stopped")
        if self.redis_pubsub is None:
            return
        channel = self._room_channel(room_id)
        readiness = self._room_readiness.get(channel)
        if readiness is None:
            if (
                len(self._room_tasks)
                >= self.config.redis_room_subscription_production_limit
            ):
                raise RoomSubscriptionLimitExceeded(
                    "active room subscription limit exceeded"
                )
            readiness = asyncio.get_running_loop().create_future()
            generation = object()
            self._room_channels[room_id] = channel
            self._room_readiness[channel] = readiness
            self._channel_generations[channel] = generation
            self._room_tasks[channel] = self._task_runner(
                self._subscription_loop(
                    channel,
                    "sse",
                    generation=generation,
                    readiness=readiness,
                    room_id=room_id,
                ),
                name=f"delivery-redis-room-{room_id}",
            )

        await asyncio.shield(readiness)

    async def unsubscribe_room(self, room_id: str) -> None:
        channel = self._room_channels.get(room_id, self._room_channel(room_id))
        readiness = self._room_readiness.get(channel)
        await self._cleanup_room_subscription(room_id, channel, readiness)

    async def handle_sse_message(self, message: str) -> None:
        envelope = self._decode(message, "sse_event")
        if not envelope or envelope.get("origin") == self.instance_id:
            return
        room_id = envelope.get("room_id")
        if not isinstance(room_id, str):
            return
        frame = envelope.get("frame")
        if not isinstance(frame, dict):
            event_type = envelope.get("type")
            data = envelope.get("data")
            if not isinstance(event_type, str):
                return
            frame = {
                "type": event_type,
                "timestamp": self._now().isoformat(),
                "room_id": room_id,
                "data": data,
            }
        if self._sse_callback is not None:
            with trace_id_context(envelope.get("trace_id")):
                await self._call(self._sse_callback, room_id, frame)

    async def _subscription_loop(
        self,
        channel: str,
        kind: str,
        *,
        generation: object,
        readiness: asyncio.Future[None] | None = None,
        room_id: str | None = None,
    ) -> None:
        delay = self.config.redis_reconnect_delay
        while not self._stopped and self._subscription_desired(channel, generation):
            messages: Any | None = None
            try:
                messages = await asyncio.wait_for(
                    self.redis_pubsub.subscribe(channel),  # type: ignore[union-attr]
                    timeout=self.config.redis_room_subscription_ready_timeout_seconds,
                )
                self._mark_channel_active(channel, generation)
                if readiness is not None and not readiness.done():
                    readiness.set_result(None)
                delay = self.config.redis_reconnect_delay
                async for message in messages:
                    await self._handle_subscription_message(kind, message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mark_channel_inactive(channel, generation)
                if readiness is not None and not readiness.done():
                    readiness.set_exception(exc)
                    readiness.exception()
                    if room_id is not None:
                        await self._cleanup_room_subscription(
                            room_id, channel, readiness
                        )
                    return
                await self._sleeper(delay)
                delay = min(delay * 2, self.config.redis_reconnect_max_delay)
            finally:
                self._mark_channel_inactive(channel, generation)
                await self._close_subscription_iterator(messages)

        if readiness is not None and not readiness.done():
            readiness.set_exception(
                RuntimeError("Room subscription stopped before becoming ready")
            )
            readiness.exception()
            if room_id is not None:
                await self._cleanup_room_subscription(room_id, channel, readiness)

    async def _handle_subscription_message(self, kind: str, message: str) -> None:
        if kind == "sse":
            await self.handle_sse_message(message)

    async def _close_subscription_iterator(self, messages: Any | None) -> None:
        close = getattr(messages, "aclose", None)
        if not callable(close):
            return
        try:
            await asyncio.wait_for(
                close(),
                timeout=self.config.redis_room_subscription_ready_timeout_seconds,
            )
        except Exception:
            pass

    async def _cleanup_room_subscription(
        self,
        room_id: str,
        channel: str,
        readiness: asyncio.Future[None] | None,
    ) -> None:
        if readiness is not None and self._room_readiness.get(channel) is not readiness:
            return
        if readiness is not None and not readiness.done():
            readiness.cancel()
        self._room_channels.pop(room_id, None)
        self._room_readiness.pop(channel, None)
        task = self._room_tasks.pop(channel, None)
        generation = self._channel_generations.pop(channel, None)
        if generation is not None:
            self._mark_channel_inactive(channel, generation)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _subscription_desired(self, channel: str, generation: object) -> bool:
        return (
            self._channel_generations.get(channel) is generation
            and channel in self._room_tasks
        )

    def _mark_channel_active(self, channel: str, generation: object) -> None:
        if self._channel_generations.get(channel) is not generation:
            return
        self._active_generations[channel] = generation
        self._active_channels.add(channel)

    def _mark_channel_inactive(self, channel: str, generation: object) -> None:
        if self._active_generations.get(channel) is not generation:
            return
        self._active_generations.pop(channel, None)
        self._active_channels.discard(channel)

    def _room_channel(self, room_id: str) -> str:
        return f"{self.config.redis_sse_channel_prefix}{room_id}"

    def _decode(self, message: str, expected_kind: str) -> dict[str, Any] | None:
        try:
            envelope = json.loads(message)
        except Exception:
            return None
        if not isinstance(envelope, dict) or envelope.get("kind") != expected_kind:
            return None
        return envelope

    async def _call(self, callback: Callable[..., Any], *args: Any) -> None:
        result = callback(*args)
        if inspect.isawaitable(result):
            await result


__all__ = ["CrossInstanceEventBus"]
