"""Pure event ordering and idempotency checks."""

from __future__ import annotations

from typing import Literal

from .models import ContractModel, OrchestratorEvent


class EventAppendEvaluation(ContractModel):
    outcome: Literal["accepted", "replayed", "conflict", "error"]
    reason: str


def evaluate_event_append(
    existing: list[OrchestratorEvent], event: OrchestratorEvent
) -> EventAppendEvaluation:
    """Validate global ID replay and contiguous per-Run event ordering."""

    same_id = next((item for item in existing if item.event_id == event.event_id), None)
    if same_id is not None:
        if same_id == event:
            return EventAppendEvaluation(
                outcome="replayed", reason="event already exists"
            )
        return EventAppendEvaluation(
            outcome="conflict", reason="event ID has different content"
        )
    if any(item.sequence == event.sequence for item in existing):
        return EventAppendEvaluation(
            outcome="conflict", reason="Run sequence is already occupied"
        )
    if any(item.run_id != event.run_id for item in existing):
        return EventAppendEvaluation(
            outcome="error", reason="existing event inventory spans multiple Runs"
        )
    expected = max((item.sequence for item in existing), default=0) + 1
    if event.sequence != expected:
        return EventAppendEvaluation(
            outcome="conflict", reason=f"expected event sequence {expected}"
        )
    latest = max(existing, key=lambda item: item.sequence) if existing else None
    if latest is not None and event.state_version < latest.state_version:
        return EventAppendEvaluation(
            outcome="conflict", reason="event state version moved backwards"
        )
    return EventAppendEvaluation(outcome="accepted", reason="event may be appended")
