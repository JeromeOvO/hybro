"""Cooperative cancellation primitives.

Provides a ``CancellationToken`` that is threaded through the processing
pipeline and a ``CancellationError`` raised when a cancellation is detected.

Architecture note (A-3):
    Instead of polling ``SSEManager.is_cancelled(message_id)`` at discrete
    checkpoints, callers hold a reference to a ``CancellationToken``.  The
    cancel endpoint (or MongoDB change-stream watcher) sets the internal
    ``asyncio.Event`` on the token, which can instantly unblock any coroutine
    waiting in ``token.race()``.  The ``token.check()`` method works as a
    lightweight, synchronous checkpoint — identical semantics to the old
    ``is_cancelled()`` call, but with no dependency on the SSE manager.
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
    1. Created by ``SSEManager.create_token(message_id)`` at the start of
       a processing pipeline.
    2. Threaded through ``ProcessingContext`` so every sub-handler has access.
    3. Signalled by ``SSEManager.cancel_message()`` or the change-stream
       watcher when a cancellation request arrives.
    4. Consumed by processing code via ``check()`` (synchronous checkpoint)
       or ``race()`` (async — aborts a blocking coroutine immediately).
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

        Drop-in replacement for the old ``if sse_manager.is_cancelled(…):``
        pattern, but the caller catches ``CancellationError`` instead.
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
