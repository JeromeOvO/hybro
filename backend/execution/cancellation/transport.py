from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from common.observability import get_current_trace_id, trace_id_context
from common.protocols import RedisPubSub
from execution.cancellation.config import CancellationConfig

CancellationCallback = Callable[[str], Awaitable[None]]


class RedisCancellationTransport:
    """Execution-owned cross-instance cancellation signal transport."""

    def __init__(
        self,
        *,
        redis_pubsub: RedisPubSub,
        config: CancellationConfig,
        instance_id: str,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.redis_pubsub = redis_pubsub
        self.config = config
        self.instance_id = instance_id
        self._sleeper = sleeper
        self._callback: CancellationCallback | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopped = True
        self._redis_reachable = False
        self._subscription_active = False
        self._readiness: asyncio.Event | None = None
        self._closed = False

    @property
    def is_connected(self) -> bool:
        return self._redis_reachable and self._subscription_active

    async def start(self, callback: CancellationCallback) -> None:
        if self._task is not None and not self._task.done():
            return
        self._callback = callback
        self._stopped = False
        self._closed = False
        self._readiness = asyncio.Event()
        await self.refresh_health()
        self._task = asyncio.create_task(
            self._subscription_loop(), name="execution-cancellation-redis"
        )
        try:
            await asyncio.wait_for(
                self._readiness.wait(),
                timeout=self.config.redis_subscription_ready_timeout_seconds,
            )
        except TimeoutError:
            pass

    async def stop(self) -> None:
        if self._closed:
            return
        self._stopped = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._subscription_active = False
        self._redis_reachable = False
        self._readiness = None
        try:
            await asyncio.wait_for(
                self.redis_pubsub.close(),
                timeout=self.config.redis_io_timeout_seconds,
            )
        except Exception:
            pass
        self._closed = True

    async def publish(self, message_id: str) -> None:
        envelope = {
            "kind": "cancellation",
            "origin": self.instance_id,
            "message_id": message_id,
            "trace_id": get_current_trace_id(),
        }
        await asyncio.wait_for(
            self.redis_pubsub.publish(
                self.config.redis_channel,
                json.dumps(envelope),
            ),
            timeout=self.config.redis_io_timeout_seconds,
        )

    async def refresh_health(self) -> None:
        try:
            self._redis_reachable = await asyncio.wait_for(
                self.redis_pubsub.ping(),
                timeout=self.config.redis_io_timeout_seconds,
            )
        except Exception:
            self._redis_reachable = False

    async def _subscription_loop(self) -> None:
        delay = self.config.redis_reconnect_delay
        while not self._stopped:
            messages: Any | None = None
            try:
                messages = await asyncio.wait_for(
                    self.redis_pubsub.subscribe(self.config.redis_channel),
                    timeout=self.config.redis_io_timeout_seconds,
                )
                self._subscription_active = True
                self._redis_reachable = True
                if self._readiness is not None:
                    self._readiness.set()
                delay = self.config.redis_reconnect_delay
                async for message in messages:
                    await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._subscription_active = False
                await self.refresh_health()
                await self._sleeper(delay)
                delay = min(delay * 2, self.config.redis_reconnect_max_delay)
            finally:
                self._subscription_active = False
                close = getattr(messages, "aclose", None)
                if callable(close):
                    try:
                        await asyncio.wait_for(
                            close(),
                            timeout=self.config.redis_io_timeout_seconds,
                        )
                    except Exception:
                        pass

    async def _handle_message(self, message: str) -> None:
        try:
            envelope = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(envelope, dict):
            return
        if envelope.get("kind") != "cancellation":
            return
        if envelope.get("origin") == self.instance_id:
            return
        message_id = envelope.get("message_id")
        callback = self._callback
        if not isinstance(message_id, str) or callback is None:
            return
        with trace_id_context(envelope.get("trace_id")):
            await callback(message_id)


__all__ = ["RedisCancellationTransport"]
