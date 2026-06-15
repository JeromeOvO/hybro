import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from common.observability import get_current_trace_id
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
        self._cancellation_callback: Callable[[str], Any] | None = None
        self._internal_callback: Callable[[dict[str, Any]], Any] | None = None
        self._room_tasks: dict[str, asyncio.Task] = {}
        self._room_channels: dict[str, str] = {}
        self._global_tasks: dict[str, asyncio.Task] = {}
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
        desired_globals = {self.config.redis_cancel_channel, self.config.redis_internal_channel}
        desired = set(self._room_tasks) | desired_globals
        return self._redis_reachable and desired.issubset(self._active_channels)

    def set_sse_callback(self, callback: Callable[[str, dict[str, Any]], Any]) -> None:
        self._sse_callback = callback

    def set_cancellation_callback(self, callback: Callable[[str], Any]) -> None:
        self._cancellation_callback = callback

    def set_internal_callback(self, callback: Callable[[dict[str, Any]], Any]) -> None:
        self._internal_callback = callback

    async def start(self) -> None:
        if self.redis_pubsub is None:
            self._redis_reachable = False
            return
        self._stopped = False
        await self.refresh_health()
        self._ensure_global_subscription(self.config.redis_cancel_channel, "cancellation")
        self._ensure_global_subscription(self.config.redis_internal_channel, "internal")

    async def stop(self) -> None:
        self._stopped = True
        tasks = list(self._room_tasks.values()) + list(self._global_tasks.values())
        self._room_tasks.clear()
        self._room_channels.clear()
        self._global_tasks.clear()
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

    async def publish_cancellation(self, message_id: str) -> None:
        if self.redis_pubsub is None:
            return
        envelope = {
            "kind": "cancellation",
            "origin": self.instance_id,
            "message_id": message_id,
        }
        await self.redis_pubsub.publish(
            self.config.redis_cancel_channel,
            json.dumps(envelope),
        )

    async def publish_internal(self, event: Any) -> None:
        if self.redis_pubsub is None:
            return
        envelope = {
            "kind": "internal_event",
            "origin": self.instance_id,
            "event_type": event.event_type,
            "event": event.model_dump(mode="json"),
            "trace_id": get_current_trace_id(),
        }
        await self.redis_pubsub.publish(
            self.config.redis_internal_channel,
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
        if self.redis_pubsub is None:
            return
        channel = self._room_channel(room_id)
        if channel in self._room_tasks:
            return
        if len(self._room_tasks) >= self.config.redis_room_subscription_production_limit:
            raise RoomSubscriptionLimitExceeded(
                "active room subscription limit exceeded"
            )
        self._room_channels[room_id] = channel
        self._room_tasks[channel] = self._task_runner(
            self._subscription_loop(channel, "sse"),
            name=f"delivery-redis-room-{room_id}",
        )

    async def unsubscribe_room(self, room_id: str) -> None:
        channel = self._room_channels.pop(room_id, self._room_channel(room_id))
        task = self._room_tasks.pop(channel, None)
        self._active_channels.discard(channel)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

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
            await self._call(self._sse_callback, room_id, frame)

    async def handle_cancellation_message(self, message: str) -> None:
        envelope = self._decode(message, "cancellation")
        if not envelope or envelope.get("origin") == self.instance_id:
            return
        message_id = envelope.get("message_id")
        if isinstance(message_id, str) and self._cancellation_callback is not None:
            await self._call(self._cancellation_callback, message_id)

    async def handle_internal_message(self, message: str) -> None:
        envelope = self._decode(message, "internal_event")
        if not envelope or envelope.get("origin") == self.instance_id:
            return
        if self._internal_callback is not None:
            await self._call(self._internal_callback, envelope)

    async def _subscription_loop(self, channel: str, kind: str) -> None:
        delay = self.config.redis_reconnect_delay
        while not self._stopped and self._subscription_desired(channel):
            try:
                messages = await self.redis_pubsub.subscribe(channel)  # type: ignore[union-attr]
                self._active_channels.add(channel)
                delay = self.config.redis_reconnect_delay
                async for message in messages:
                    if kind == "sse":
                        await self.handle_sse_message(message)
                    elif kind == "cancellation":
                        await self.handle_cancellation_message(message)
                    else:
                        await self.handle_internal_message(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._active_channels.discard(channel)
                await self._sleeper(delay)
                delay = min(delay * 2, self.config.redis_reconnect_max_delay)
            finally:
                self._active_channels.discard(channel)

    def _ensure_global_subscription(self, channel: str, kind: str) -> None:
        if channel in self._global_tasks:
            return
        self._global_tasks[channel] = self._task_runner(
            self._subscription_loop(channel, kind),
            name=f"delivery-redis-{kind}",
        )

    def _subscription_desired(self, channel: str) -> bool:
        return channel in self._room_tasks or channel in self._global_tasks

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
