import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

from cachetools import TTLCache

from common.protocols import MongoCollection, RedisKV
from common.utils.cancellation import CancellationToken
from delivery.config import DeliveryConfig
from delivery.types import TaskRunner


class CancellationWatcher:
    def __init__(
        self,
        *,
        collection: MongoCollection,
        redis_kv: RedisKV | None,
        event_bus: Any | None,
        config: DeliveryConfig,
        task_runner: TaskRunner,
        timer: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        cache_kwargs = {
            "maxsize": config.cancellation_cache_maxsize,
            "ttl": config.cancellation_ttl_seconds,
        }
        token_cache_kwargs = {
            "maxsize": config.cancellation_token_cache_maxsize,
            "ttl": config.cancellation_ttl_seconds,
        }
        if timer is not None:
            cache_kwargs["timer"] = timer
            token_cache_kwargs["timer"] = timer

        self.cancelled_messages: TTLCache[str, bool] = TTLCache(**cache_kwargs)
        self._tokens: TTLCache[str, CancellationToken] = TTLCache(**token_cache_kwargs)
        self.collection = collection
        self.redis_kv = redis_kv
        self.event_bus = event_bus
        self.config = config
        self._task_runner = task_runner
        self._sleeper = sleeper
        self._task: asyncio.Task | None = None
        self._startup_future: asyncio.Future[None] | None = None
        self._shutdown = False
        self.change_stream_connected = False

    def cancel_message(self, message_id: str) -> None:
        self._set_cancelled_local(message_id)

    async def mark_cancelled(self, message_id: str) -> None:
        self._set_cancelled_local(message_id)
        await self._write_l2(message_id)
        if self.event_bus is not None:
            try:
                await self.event_bus.publish_cancellation(message_id)
            except Exception:
                pass

    def is_cancelled(self, message_id: str) -> bool:
        return message_id in self.cancelled_messages

    async def check_cancelled(self, message_id: str) -> bool:
        if message_id in self.cancelled_messages:
            return True
        if self.redis_kv is None:
            return False
        try:
            if await self.redis_kv.exists(self._cancel_key(message_id)):
                self._set_cancelled_local(message_id)
                return True
        except Exception:
            return False
        return False

    async def handle_remote_cancellation(self, message_id: str) -> None:
        self._set_cancelled_local(message_id)
        await self._write_l2(message_id)

    def clear_cancellation(self, message_id: str) -> None:
        self.cancelled_messages.pop(message_id, None)
        self._tokens.pop(message_id, None)

    def create_token(self, message_id: str) -> CancellationToken:
        token = CancellationToken(message_id=message_id)
        if message_id in self.cancelled_messages:
            token.cancel()
        self._tokens[message_id] = token
        return token

    def get_token(self, message_id: str) -> CancellationToken | None:
        return self._tokens.get(message_id)

    def remove_token(self, message_id: str) -> None:
        self._tokens.pop(message_id, None)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._shutdown = False
        self.change_stream_connected = False
        loop = asyncio.get_running_loop()
        self._startup_future = loop.create_future()
        self._task = self._task_runner(
            self._watch_cancellations(),
            name="delivery-cancellation-watcher",
        )
        await self._startup_future

    async def stop(self) -> None:
        self._shutdown = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._startup_future = None
        self.change_stream_connected = False
        self._shutdown = False

    async def _watch_cancellations(self) -> None:
        pipeline = [{"$match": {"operationType": "insert"}}]
        resume_token: dict | None = None
        backoff_delay = self.config.cs_backoff_base
        consecutive_failures = 0
        startup_reported = False

        while not self._shutdown:
            try:
                watch_kwargs: dict[str, Any] = {}
                if resume_token is not None:
                    watch_kwargs["resume_after"] = resume_token
                async with self.collection.watch(pipeline, **watch_kwargs) as stream:
                    self.change_stream_connected = True
                    if not startup_reported:
                        self._report_startup_ready()
                        startup_reported = True
                    backoff_delay = self.config.cs_backoff_base
                    consecutive_failures = 0
                    async for change in stream:
                        if self._shutdown:
                            break
                        resume_token = self._handle_change_event(change, resume_token)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.change_stream_connected = False
                if not startup_reported:
                    self._report_startup_failure(exc)
                    break

                consecutive_failures += 1
                if consecutive_failures >= 3 and resume_token is not None:
                    resume_token = None
                jitter = backoff_delay * self.config.cs_jitter_fraction
                delay = backoff_delay + random.uniform(-jitter, jitter)
                await self._sleeper(delay)
                backoff_delay = min(
                    backoff_delay * self.config.cs_backoff_factor,
                    self.config.cs_backoff_max,
                )
            finally:
                self.change_stream_connected = False

    def _handle_change_event(
        self,
        change: dict[str, Any],
        resume_token: dict | None,
    ) -> dict | None:
        try:
            message_id = change["fullDocument"]["message_id"]
        except KeyError:
            return resume_token
        self._set_cancelled_local(message_id)
        return change.get("_id", resume_token)

    def _set_cancelled_local(self, message_id: str) -> None:
        self.cancelled_messages[message_id] = True
        token = self._tokens.get(message_id)
        if token is not None:
            token.cancel()

    async def _write_l2(self, message_id: str) -> None:
        if self.redis_kv is None:
            return
        try:
            await self.redis_kv.set(
                self._cancel_key(message_id),
                "1",
                ttl=self.config.cancellation_ttl_seconds,
            )
        except Exception:
            pass

    def _cancel_key(self, message_id: str) -> str:
        return f"{self.config.redis_cancel_key_prefix}{message_id}"

    def _report_startup_ready(self) -> None:
        if self._startup_future is not None and not self._startup_future.done():
            self._startup_future.set_result(None)

    def _report_startup_failure(self, exc: Exception) -> None:
        if self._startup_future is not None and not self._startup_future.done():
            self._startup_future.set_exception(exc)


__all__ = ["CancellationWatcher"]
