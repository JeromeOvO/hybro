"""Event-based, bounded wait on a session request's run.

The durable room event log is the only source. Lifecycle events are records of
kind ``run_event`` whose ``payload_public`` carries the run's ``type``,
``run_id``, ``correlation_id`` (the request's ``client_request_id``) and
``payload``. Owner decisions arrive as ``hitl_response`` records carrying
``status`` and ``run_id``. The waiter matches on the correlation id, so a
retried request finds the earlier outcome.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from session.protocols import ConversationEventsPort

_RUN_EVENT = "run_event"
_HITL_RESPONSE = "hitl_response"
_SETTLED = "run_settled"
_WAITING_INPUT = "run_waiting_input"
_RESUMED = "run_resumed"


@dataclass(slots=True)
class CorrelatedOutcome:
    """What the event log says about one request's run."""

    seen: bool = False
    run_id: str | None = None
    last_type: str | None = None
    # Paused from run_waiting_input until run_resumed or settlement; the kernel
    # emits further tool events after the pause, so the last type is not enough.
    paused: bool = False
    settlement: dict[str, Any] | None = None
    rejected: bool = False
    last_seq: int = 0
    timed_out: bool = False
    not_started: bool = False
    _canceled_hitl_runs: set[str] = field(default_factory=set)

    @property
    def waiting_input(self) -> bool:
        return self.settlement is None and self.paused

    def absorb(self, record: dict[str, Any], correlation_id: str) -> None:
        self.last_seq = max(self.last_seq, int(record.get("room_seq") or 0))
        payload = record.get("payload_public")
        if not isinstance(payload, dict):
            return
        kind = record.get("kind")
        if kind == _HITL_RESPONSE:
            run_id = payload.get("run_id")
            if payload.get("status") == "canceled" and isinstance(run_id, str):
                self._canceled_hitl_runs.add(run_id)
            return
        if kind != _RUN_EVENT or payload.get("correlation_id") != correlation_id:
            return
        event_type = payload.get("type")
        if not isinstance(event_type, str):
            return
        run_id = payload.get("run_id")
        self.seen = True
        self.run_id = run_id if isinstance(run_id, str) else self.run_id
        self.last_type = event_type
        if event_type == _WAITING_INPUT:
            self.paused = True
        elif event_type == _RESUMED:
            self.paused = False
        if event_type == _SETTLED:
            self.paused = False
            settlement = payload.get("payload")
            self.settlement = settlement if isinstance(settlement, dict) else {}
            # A reject is an owner HITL cancel on this run before settlement.
            self.rejected = (
                self.settlement.get("status") == "canceled"
                and self.run_id in self._canceled_hitl_runs
            )


class SessionWaiter:
    def __init__(
        self,
        events: ConversationEventsPort,
        *,
        send_wait_seconds: float,
        start_grace_seconds: float,
        poll_interval: float = 0.5,
        page_size: int = 500,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._events = events
        self._send_wait_seconds = send_wait_seconds
        self._start_grace_seconds = start_grace_seconds
        self._poll_interval = poll_interval
        self._page_size = page_size
        self._clock = clock
        self._sleep = sleep

    async def latest_seq(self, room_id: str) -> int:
        return await self._events.latest_seq(room_id)

    async def scan(
        self, room_id: str, correlation_id: str, *, after: int
    ) -> CorrelatedOutcome:
        """Read every event after ``after`` once, without waiting."""
        outcome = CorrelatedOutcome(last_seq=after)
        await self._read_new(room_id, correlation_id, outcome)
        return outcome

    async def wait(
        self, room_id: str, correlation_id: str, *, after: int
    ) -> CorrelatedOutcome:
        """Poll until settlement, a pause for the owner, or the deadline.

        No correlated run event within the start grace means the run never
        started; the deadline without settlement means it is still working.
        """
        outcome = CorrelatedOutcome(last_seq=after)
        started_at = self._clock()
        while True:
            await self._read_new(room_id, correlation_id, outcome)
            if outcome.settlement is not None or outcome.waiting_input:
                return outcome
            elapsed = self._clock() - started_at
            if not outcome.seen and elapsed >= self._start_grace_seconds:
                outcome.not_started = True
                return outcome
            if elapsed >= self._send_wait_seconds:
                outcome.timed_out = True
                return outcome
            await self._sleep(self._poll_interval)

    async def _read_new(
        self, room_id: str, correlation_id: str, outcome: CorrelatedOutcome
    ) -> None:
        while True:
            cursor = outcome.last_seq
            records = await self._events.read_range(
                room_id, after=cursor, limit=self._page_size
            )
            for record in records:
                outcome.absorb(record, correlation_id)
            if len(records) < self._page_size or outcome.last_seq <= cursor:
                return


__all__ = ["CorrelatedOutcome", "SessionWaiter"]
