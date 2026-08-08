from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from common.eventing import RemoteEventCallback
from common.protocols import RedisPubSub


class RedisInternalEventTransport:
    """Redis transport for generic internal event envelopes and eventing DLTs."""

    def __init__(
        self,
        *,
        redis_pubsub: RedisPubSub,
        channel: str = "internal:global",
        dead_letter_channel: str = "eventing:dead_letter",
        reconnect_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
        subscription_ready_timeout: float = 5.0,
        io_timeout: float = 5.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not channel or not dead_letter_channel:
            raise ValueError("eventing Redis channels must be non-empty")
        if subscription_ready_timeout <= 0 or io_timeout <= 0:
            raise ValueError("eventing Redis timeouts must be greater than 0")
        self.redis_pubsub = redis_pubsub
        self.channel = channel
        self.dead_letter_channel = dead_letter_channel
        self.reconnect_delay = reconnect_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.subscription_ready_timeout = subscription_ready_timeout
        self.io_timeout = io_timeout
        self._sleeper = sleeper
        self._callback: RemoteEventCallback | None = None
        self._task: asyncio.Task[None] | None = None
        self._callback_task: asyncio.Task[None] | None = None
        self._stopped = True
        self._redis_reachable = False
        self._subscription_active = False
        self._closed = False
        self._readiness: asyncio.Event | None = None

    @property
    def is_connected(self) -> bool:
        return self._redis_reachable and self._subscription_active

    async def start(self, callback: RemoteEventCallback) -> None:
        if self._task is not None and not self._task.done():
            return
        self._callback = callback
        self._stopped = False
        self._closed = False
        self._readiness = asyncio.Event()
        await self.refresh_health()
        self._task = asyncio.create_task(
            self._subscription_loop(),
            name="eventing-redis-internal",
        )
        try:
            await asyncio.wait_for(
                self._readiness.wait(),
                timeout=self.subscription_ready_timeout,
            )
        except TimeoutError:
            pass

    async def publish(self, message: str) -> None:
        await asyncio.wait_for(
            self.redis_pubsub.publish(self.channel, message),
            timeout=self.io_timeout,
        )

    async def publish_dead_letter(self, message: str) -> None:
        await asyncio.wait_for(
            self.redis_pubsub.publish(self.dead_letter_channel, message),
            timeout=self.io_timeout,
        )

    async def refresh_health(self) -> None:
        try:
            self._redis_reachable = await asyncio.wait_for(
                self.redis_pubsub.ping(),
                timeout=self.io_timeout,
            )
        except Exception:
            self._redis_reachable = False

    async def stop_ingress(self) -> None:
        self._stopped = True
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._wait_for_callback()
        self._subscription_active = False
        self._readiness = None

    async def stop(self) -> None:
        if self._closed:
            return
        await self.stop_ingress()
        self._redis_reachable = False
        try:
            await asyncio.wait_for(
                self.redis_pubsub.close(),
                timeout=self.io_timeout,
            )
        except Exception:
            pass
        self._closed = True

    async def _subscription_loop(self) -> None:
        delay = self.reconnect_delay
        while not self._stopped:
            messages: Any | None = None
            try:
                messages = await asyncio.wait_for(
                    self.redis_pubsub.subscribe(self.channel),
                    timeout=self.io_timeout,
                )
                self._subscription_active = True
                self._redis_reachable = True
                if self._readiness is not None:
                    self._readiness.set()
                delay = self.reconnect_delay
                async for message in messages:
                    callback = self._callback
                    if callback is not None:
                        callback_task = asyncio.create_task(
                            callback(message),
                            name="eventing-redis-callback",
                        )
                        self._callback_task = callback_task
                        try:
                            await asyncio.shield(callback_task)
                        finally:
                            if callback_task.done():
                                self._callback_task = None
            except asyncio.CancelledError:
                raise
            except Exception:
                self._subscription_active = False
                await self.refresh_health()
                await self._sleeper(delay)
                delay = min(delay * 2, self.reconnect_max_delay)
            finally:
                self._subscription_active = False
                close = getattr(messages, "aclose", None)
                if callable(close):
                    try:
                        await asyncio.wait_for(
                            close(),
                            timeout=self.io_timeout,
                        )
                    except Exception:
                        pass

    async def _wait_for_callback(self) -> None:
        callback_task = self._callback_task
        if callback_task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(callback_task),
                timeout=self.io_timeout,
            )
        except TimeoutError:
            callback_task.cancel()
            await asyncio.gather(callback_task, return_exceptions=True)
        finally:
            if callback_task.done() and self._callback_task is callback_task:
                self._callback_task = None


__all__ = ["RedisInternalEventTransport"]
