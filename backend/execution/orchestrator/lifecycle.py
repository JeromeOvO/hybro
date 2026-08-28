"""Private in-process lifecycle events for the room session."""

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
    "model_turn_completed",
    "orchestrator_decision",
    "message_started",
    "message_updated",
    "message_completed",
    "tool_execution_started",
    "tool_execution_updated",
    "tool_execution_completed",
    "turn_completed",
    "model_decision",
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
    # Correlation fields carried from the Run so delivery listeners (SSE)
    # can address the right room/user-message without a store round trip.
    room_id: str | None = None
    user_message_id: str | None = None
    client_request_id: str | None = None
    lifecycle_family: Literal["legacy", "canonical"] = "legacy"


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
        durable = terminal or event.lifecycle_family == "canonical"
        if durable:
            # Canonical lifecycle publication is part of the state machine: an
            # acknowledged append must precede the next public state/offset.
            # Listener failures therefore propagate instead of being converted
            # into process-local best-effort diagnostics.
            async with asyncio.timeout(self._settlement_timeout):
                await asyncio.gather(
                    *(
                        self._invoke(listener, event, required=True)
                        for listener in listeners
                    )
                )
            return
        for listener in listeners:
            asyncio.create_task(self._invoke(listener, event))

    async def _invoke(
        self,
        listener: SessionEventListener,
        event: SessionEvent,
        *,
        required: bool = False,
    ) -> None:
        try:
            timeout = self._settlement_timeout if required else self._listener_timeout
            async with asyncio.timeout(timeout):
                result = listener(event)
                if inspect.isawaitable(result):
                    await result
        except BaseException as exc:
            self._error_hook(exc)
            if required:
                raise


__all__ = [
    "LifecycleEmitter",
    "orchestrator_lifecycle_log_message",
    "SessionEvent",
    "SessionEventListener",
    "SessionEventType",
]


def orchestrator_lifecycle_log_message(
    event: SessionEvent,
) -> tuple[str, str] | None:
    """Map a session lifecycle event to a (message, turn_phase) work-log entry.

    Returns None for events that should not surface in the user-facing work
    log (internal noise, terminal events handled by the projection listener).
    """
    payload = event.payload or {}
    label = payload.get("agent_label")
    label_text = label.strip() if isinstance(label, str) and label.strip() else None
    if event.event_type == "run_started":
        return "Planning the next actions", "collecting"
    if event.event_type == "turn_started":
        return "Thinking about the next step", "collecting"
    if event.event_type == "tool_execution_started" and label_text:
        return f"Delegating to {label_text}", "collecting"
    if event.event_type == "tool_execution_completed" and label_text:
        return f"{label_text} finished", "collecting"
    if (
        event.event_type == "message_completed"
        and payload.get("message_kind") == "tool_result"
        and label_text
    ):
        return f"{label_text} responded", "collecting"
    if event.event_type in {"run_waiting_external", "turn_completed"}:
        return "Waiting for agents to respond", "collecting"
    if event.event_type == "run_awaiting_user":
        return "Waiting for your input", "collecting"
    if event.event_type == "run_final_answer_ready":
        return "Preparing the final answer", "synthesizing"
    return None
