"""Cooperative cancellation primitives.

Provides a ``CancellationToken`` that is threaded through the processing
pipeline and a ``CancellationError`` raised when a cancellation is detected.

Architecture note:
    This module contains only the cooperative primitive. Execution owns token
    registration, durable tombstone hydration, and lifecycle release. The
    cancel endpoint, Mongo watcher, or Redis callback signals the registered
    token, which can instantly unblock a coroutine waiting in ``token.race()``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TypeVar

from common.observability.tracing import traced_create_task

T = TypeVar("T")


class CancellationError(Exception):
    """Raised when a ``CancellationToken`` detects that processing has been
    cancelled by the user."""

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        super().__init__(f"Failed: Processing cancelled for message {message_id}")


@dataclass
class CancellationToken:
    """Cooperative cancellation with instant notification via ``asyncio.Event``.

    Lifecycle
    ---------
    1. Registered by the Execution cancellation runtime for one processing
       owner after durable admission succeeds.
    2. Threaded through processing contexts so sub-handlers share one token.
    3. Signalled by the Execution runtime when a cancellation request arrives.
    4. Released identity-safely by the owner on terminal completion or pause;
       continuation resume recreates it and hydrates durable Redis state.
    5. Consumed via ``check()`` or ``race()``.
    """

    message_id: str
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Signal cancellation.

        Called by the cancel endpoint / change-stream watcher.  Safe to call
        more than once (``asyncio.Event.set`` is idempotent).
        """
        self._event.set()

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    @property
    def is_cancelled(self) -> bool:
        """Non-raising check — useful for ``if`` guards that need custom
        teardown logic rather than exception-based control flow."""
        return self._event.is_set()

    def check(self) -> None:
        """Raise ``CancellationError`` if cancelled.

        Processing code catches ``CancellationError`` for cooperative teardown.
        """
        if self._event.is_set():
            raise CancellationError(self.message_id)

    async def race(self, coro) -> T:
        """Run *coro* concurrently with the cancellation event.

        If the cancellation event fires first, the *coro* is cancelled and
        ``CancellationError`` is raised.  This eliminates the TOCTOU gap
        between a checkpoint and the subsequent blocking call.

        Parameters
        ----------
        coro:
            An awaitable (coroutine or ``asyncio.Task``) to race against.

        Returns
        -------
        T
            The result of *coro* if it completes before cancellation.

        Raises
        ------
        CancellationError
            If the token is signalled before *coro* completes.
        """
        cancel_task = traced_create_task(
            self._event.wait(),
            name="cancellation-token-wait",
        )
        work_task = asyncio.ensure_future(coro)

        try:
            done, pending = await asyncio.wait(
                {cancel_task, work_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            # If the enclosing coroutine is cancelled (e.g. server shutdown)
            # clean up both tasks so they don't leak.
            cancel_task.cancel()
            work_task.cancel()
            raise

        for task in pending:
            task.cancel()
            # Suppress CancelledError from the losing task
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        if cancel_task in done:
            raise CancellationError(self.message_id)

        return work_task.result()

    def wait(self) -> asyncio.Future[None]:
        """Return a future that resolves when cancellation is signalled.

        Use this instead of accessing ``_event`` directly when you need
        to race cancellation against multiple tasks while retaining the
        ability to collect partial results.
        """
        return asyncio.ensure_future(self._event.wait())
