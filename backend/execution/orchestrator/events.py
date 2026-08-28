"""Pure event ordering and idempotency checks."""

from __future__ import annotations

from datetime import UTC
from typing import Literal

from .models import ContractModel, OrchestratorEvent


class EventAppendEvaluation(ContractModel):
    outcome: Literal["accepted", "replayed", "conflict", "error"]
    reason: str


def canonicalize_orchestrator_event(
    event: OrchestratorEvent,
) -> OrchestratorEvent:
    """Return the exact event identity that survives BSON persistence.

    BSON dates have millisecond precision. Event replay therefore compares and
    stores UTC timestamps truncated to milliseconds while leaving every other
    identity field exact. This also lets legacy outbox payloads carrying Python
    microseconds replay the event already inserted before a worker crash.
    """

    created_at = event.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)
    created_at = created_at.replace(microsecond=(created_at.microsecond // 1000) * 1000)
    return event.model_copy(update={"created_at": created_at})


def evaluate_event_append(
    existing: list[OrchestratorEvent], event: OrchestratorEvent
) -> EventAppendEvaluation:
    """Validate global ID replay and contiguous per-Run event ordering.

    Only ``created_at`` is normalized, to UTC BSON millisecond precision. All
    other event identity, ordering, and state-version fields remain exact.
    """

    event = canonicalize_orchestrator_event(event)
    existing = [canonicalize_orchestrator_event(item) for item in existing]
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
