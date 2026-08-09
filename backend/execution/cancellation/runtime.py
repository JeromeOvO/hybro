from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cachetools import TTLCache

from common.protocols import MongoCollection, RedisKV
from common.utils.cancellation import CancellationToken
from execution.cancellation.config import CancellationConfig
from execution.cancellation.transport import RedisCancellationTransport
from execution.cancellation.watcher import CancellationWatcher


@dataclass(frozen=True, slots=True)
class CancellationPropagationResult:
    kv_configured: bool
    kv_succeeded: bool
    pubsub_configured: bool
    pubsub_succeeded: bool

    @property
    def succeeded(self) -> bool:
        return (not self.kv_configured or self.kv_succeeded) and (
            not self.pubsub_configured or self.pubsub_succeeded
        )


class CancellationRuntime:
    """Owns cancellation tombstones, active tokens, and external projections."""

    def __init__(
        self,
        *,
        collection: MongoCollection,
        redis_kv: RedisKV | None,
        transport: RedisCancellationTransport | None,
        config: CancellationConfig,
        task_runner: Callable[..., asyncio.Task],
        allow_degraded_change_stream: bool = False,
        timer: Callable[[], float] | None = None,
    ) -> None:
        cache_kwargs: dict[str, Any] = {
            "maxsize": config.cache_maxsize,
            "ttl": config.ttl_seconds,
        }
        if timer is not None:
            cache_kwargs["timer"] = timer
        self._tombstones: TTLCache[str, bool] = TTLCache(**cache_kwargs)
        self._tokens: dict[str, CancellationToken] = {}
        self._redis_kv = redis_kv
        self._transport = transport
        self.config = config
        self._allow_degraded_change_stream = allow_degraded_change_stream
        self._watcher = CancellationWatcher(
            collection=collection,
            signal_local=self.signal_local,
            config=config,
            task_runner=task_runner,
        )
        self._started = False
        self._kv_closed = False
        self._kv_connected = False

    @property
    def change_stream_connected(self) -> bool:
        return self._watcher.change_stream_connected

    @property
    def redis_connected(self) -> bool:
        if self._transport is None and self._redis_kv is None:
            return False
        transport_connected = self._transport is None or self._transport.is_connected
        kv_connected = self._redis_kv is None or self._kv_connected
        return transport_connected and kv_connected

    @property
    def active_token_count(self) -> int:
        return len(self._tokens)

    async def start(self) -> None:
        if self._started:
            return
        self._kv_closed = False
        try:
            if self._transport is not None:
                await self._transport.start(self.handle_remote_cancellation)
            try:
                await self._watcher.start()
            except Exception:
                if not self._allow_degraded_change_stream:
                    raise
            await self.refresh_health()
        except BaseException:
            try:
                await self._watcher.stop()
            except BaseException:
                pass
            if self._transport is not None:
                try:
                    await self._transport.stop()
                except BaseException:
                    pass
            raise
        self._started = True

    async def stop(self) -> None:
        first_error: BaseException | None = None
        try:
            await self._watcher.stop()
        except BaseException as exc:
            first_error = exc
        if self._transport is not None:
            try:
                await self._transport.stop()
            except BaseException as exc:
                first_error = first_error or exc
        if self._redis_kv is not None and not self._kv_closed:
            try:
                await asyncio.wait_for(
                    self._redis_kv.close(),
                    timeout=self.config.redis_io_timeout_seconds,
                )
            except BaseException as exc:
                first_error = first_error or exc
            self._kv_closed = True
        self._tokens.clear()
        self._kv_connected = False
        self._started = False
        if first_error is not None:
            raise first_error

    async def refresh_health(self) -> None:
        if self._transport is not None:
            await self._transport.refresh_health()
        if self._redis_kv is None:
            self._kv_connected = False
            return
        try:
            self._kv_connected = await asyncio.wait_for(
                self._redis_kv.ping(),
                timeout=self.config.redis_io_timeout_seconds,
            )
        except Exception:
            self._kv_connected = False

    def create_token(self, message_id: str) -> CancellationToken:
        existing = self._tokens.get(message_id)
        if existing is not None:
            return existing
        token = CancellationToken(message_id=message_id)
        if message_id in self._tombstones:
            token.cancel()
        self._tokens[message_id] = token
        return token

    def get_token(self, message_id: str) -> CancellationToken | None:
        return self._tokens.get(message_id)

    def release_token(
        self,
        message_id: str,
        token: CancellationToken | None,
    ) -> bool:
        if token is None or self._tokens.get(message_id) is not token:
            return False
        self._tokens.pop(message_id, None)
        return True

    def release_active_token(
        self,
        message_id: str,
        token: CancellationToken | None,
    ) -> bool:
        """Release only the observed active owner, retaining its tombstone."""
        return self.release_token(message_id, token)

    def clear_cancellation(self, message_id: str) -> None:
        """Clear only the expiring tombstone; active-token ownership is separate."""
        self._tombstones.pop(message_id, None)

    def is_cancelled(self, message_id: str) -> bool:
        return message_id in self._tombstones

    async def check_cancelled(self, message_id: str) -> bool:
        if message_id in self._tombstones:
            return True
        if self._redis_kv is None:
            return False
        try:
            if await asyncio.wait_for(
                self._redis_kv.exists(self._cancel_key(message_id)),
                timeout=self.config.redis_io_timeout_seconds,
            ):
                self.signal_local(message_id)
                return True
        except Exception:
            pass
        return False

    def signal_local(self, message_id: str) -> None:
        self._tombstones[message_id] = True
        token = self._tokens.get(message_id)
        if token is not None:
            token.cancel()

    async def signal(self, message_id: str) -> CancellationPropagationResult:
        self.signal_local(message_id)
        kv_succeeded = await self._write_l2(message_id)
        pubsub_succeeded = self._transport is None
        if self._transport is not None:
            try:
                await self._transport.publish(message_id)
                pubsub_succeeded = True
            except Exception:
                pubsub_succeeded = False
        return CancellationPropagationResult(
            kv_configured=self._redis_kv is not None,
            kv_succeeded=kv_succeeded,
            pubsub_configured=self._transport is not None,
            pubsub_succeeded=pubsub_succeeded,
        )

    async def cancel_message_and_broadcast(
        self, message_id: str
    ) -> CancellationPropagationResult:
        return await self.signal(message_id)

    async def handle_remote_cancellation(self, message_id: str) -> None:
        self.signal_local(message_id)
        await self._write_l2(message_id)

    async def _write_l2(self, message_id: str) -> bool:
        if self._redis_kv is None:
            return True
        try:
            await asyncio.wait_for(
                self._redis_kv.set(
                    self._cancel_key(message_id),
                    "1",
                    ttl=self.config.ttl_seconds,
                ),
                timeout=self.config.redis_io_timeout_seconds,
            )
            return True
        except Exception:
            return False

    def _cancel_key(self, message_id: str) -> str:
        return f"{self.config.redis_key_prefix}{message_id}"


__all__ = ["CancellationPropagationResult", "CancellationRuntime"]
