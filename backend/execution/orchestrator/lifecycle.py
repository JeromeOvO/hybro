"""Private in-process lifecycle events for the unbound Plan 2 session."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

from .models import ContractModel

SessionEventType = Literal[
    "session_started",
    "run_started",
    "turn_started",
    "model_attempt_started",
    "model_retry_scheduled",
    "model_attempt_failed",
    "message_completed",
    "tool_execution_started",
    "tool_execution_completed",
    "turn_completed",
    "run_waiting_external",
    "run_awaiting_user",
    "run_final_answer_ready",
    "run_budget_exhausted",
    "run_failed",
    "run_canceled",
    "session_idle",
]


class SessionEvent(ContractModel):
    event_type: SessionEventType
    session_id: str
    run_id: str
    causation_id: str
    sequence: int
    timestamp: datetime
    payload: dict[str, object]


SessionEventListener = Callable[[SessionEvent], Awaitable[None] | None]


class LifecycleEmitter:
    def __init__(
        self,
        *,
        listener_timeout_seconds: float = 1.0,
        settlement_timeout_seconds: float = 5.0,
        error_hook: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._listeners: list[SessionEventListener] = []
        self._listener_timeout = listener_timeout_seconds
        self._settlement_timeout = settlement_timeout_seconds
        self._error_hook = error_hook or (lambda exc: None)

    def subscribe(self, listener: SessionEventListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def emit(self, event: SessionEvent, *, terminal: bool = False) -> None:
        listeners = list(self._listeners)
        if terminal:
            try:
                async with asyncio.timeout(self._settlement_timeout):
                    for listener in listeners:
                        await self._invoke(listener, event)
            except TimeoutError as exc:
                self._error_hook(exc)
            return
        for listener in listeners:
            asyncio.create_task(self._invoke(listener, event))

    async def _invoke(
        self, listener: SessionEventListener, event: SessionEvent
    ) -> None:
        try:
            async with asyncio.timeout(self._listener_timeout):
                result = listener(event)
                if inspect.isawaitable(result):
                    await result
        except BaseException as exc:  # listener failures never mutate Run state
            self._error_hook(exc)


__all__ = [
    "LifecycleEmitter",
    "SessionEvent",
    "SessionEventListener",
    "SessionEventType",
]
