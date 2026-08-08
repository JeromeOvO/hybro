from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

from common.protocols import MongoCollection
from execution.cancellation.config import CancellationConfig


class CancellationWatcher:
    """Projects durable Mongo cancellation markers into Execution runtime state."""

    def __init__(
        self,
        *,
        collection: MongoCollection,
        signal_local: Callable[[str], None],
        config: CancellationConfig,
        task_runner: Callable[..., asyncio.Task],
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.collection = collection
        self._signal_local = signal_local
        self.config = config
        self._task_runner = task_runner
        self._sleeper = sleeper
        self._task: asyncio.Task | None = None
        self._startup_future: asyncio.Future[None] | None = None
        self._shutdown = False
        self.change_stream_connected = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._shutdown = False
        self.change_stream_connected = False
        self._startup_future = asyncio.get_running_loop().create_future()
        self._task = self._task_runner(
            self._watch_cancellations(),
            name="execution-cancellation-watcher",
        )
        await self._startup_future

    async def stop(self) -> None:
        self._shutdown = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._task = None
        self._startup_future = None
        self.change_stream_connected = False
        self._shutdown = False

    async def _watch_cancellations(self) -> None:
        pipeline = [{"$match": {"operationType": "insert"}}]
        resume_token: dict | None = None
        backoff_delay = self.config.change_stream_backoff_base
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
                    backoff_delay = self.config.change_stream_backoff_base
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
                has_error_label = getattr(exc, "has_error_label", None)
                if callable(has_error_label) and has_error_label(
                    "NonResumableChangeStreamError"
                ):
                    resume_token = None
                jitter = backoff_delay * self.config.change_stream_jitter_fraction
                delay = backoff_delay + random.uniform(-jitter, jitter)
                await self._sleeper(delay)
                backoff_delay = min(
                    backoff_delay * self.config.change_stream_backoff_factor,
                    self.config.change_stream_backoff_max,
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
        if isinstance(message_id, str):
            self._signal_local(message_id)
        return change.get("_id", resume_token)

    def _report_startup_ready(self) -> None:
        if self._startup_future is not None and not self._startup_future.done():
            self._startup_future.set_result(None)

    def _report_startup_failure(self, exc: Exception) -> None:
        if self._startup_future is not None and not self._startup_future.done():
            self._startup_future.set_exception(exc)


__all__ = ["CancellationWatcher"]
