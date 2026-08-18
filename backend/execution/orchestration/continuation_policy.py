from __future__ import annotations

import hashlib
from dataclasses import dataclass

from common.utils.time import utcnow
from models.orchestration import PendingAgentContinuation, PlannedDelegateTarget


@dataclass(frozen=True)
class ContinuationMatch:
    allowed: bool
    code: str
    continuation_id: str
    new_resource_fingerprints: tuple[str, ...] = ()


def continuation_id_for(
    *,
    run_id: str,
    source_intent_id: str,
    a2a_task_id: str,
    a2a_context_id: str,
) -> str:
    return ":".join(
        ("continuation", run_id, source_intent_id, a2a_task_id, a2a_context_id)
    )


def continuation_match(
    continuation: PendingAgentContinuation,
    *,
    target: PlannedDelegateTarget,
    goal_family_fingerprint: str,
    selected_resource_fingerprints: set[str],
) -> ContinuationMatch:
    if continuation.status != "open":
        return _rejected(continuation, "continuation_not_open")
    if not continuation.a2a_task_id or not continuation.a2a_context_id:
        return _rejected(continuation, "continuation_task_context_missing")
    if not target.repair_of_intent_id:
        return _rejected(continuation, "continuation_lineage_missing")
    if target.repair_of_intent_id != continuation.source_intent_id:
        return _rejected(continuation, "continuation_lineage_mismatch")
    if target.agent_id != continuation.agent_id:
        return _rejected(continuation, "continuation_agent_mismatch")
    if goal_family_fingerprint != continuation.goal_family_fingerprint:
        return _rejected(continuation, "continuation_goal_family_mismatch")

    new_resources = tuple(
        sorted(
            set(selected_resource_fingerprints)
            - set(continuation.attempted_resource_fingerprints)
        )
    )
    if not new_resources:
        return _rejected(continuation, "continuation_resource_already_attempted")
    return ContinuationMatch(
        allowed=True,
        code="continuation_allowed",
        continuation_id=continuation.continuation_id,
        new_resource_fingerprints=new_resources,
    )


def claim_continuation(
    continuation: PendingAgentContinuation,
    new_resource_fingerprints: tuple[str, ...],
) -> PendingAgentContinuation | None:
    if continuation.status != "open":
        return None
    delivery_revision = continuation.delivery_revision + 1
    outbound_message_id = (
        "orchestration-continuation-"
        + hashlib.sha256(
            f"{continuation.continuation_id}:{delivery_revision}".encode()
        ).hexdigest()
    )
    return continuation.model_copy(
        update={
            "status": "resuming",
            "delivery_revision": delivery_revision,
            "outbound_message_id": outbound_message_id,
            "delivery_started_at": utcnow(),
            "delivery_error": None,
            "attempted_resource_fingerprints": list(
                dict.fromkeys(
                    [
                        *continuation.attempted_resource_fingerprints,
                        *new_resource_fingerprints,
                    ]
                )
            ),
        }
    )


def reconcile_continuation(
    continuation: PendingAgentContinuation,
    *,
    status: str,
    response_snapshot: dict | None = None,
    delivery_error: str | None = None,
) -> PendingAgentContinuation:
    allowed = {
        "open",
        "delivery_uncertain",
        "acknowledged",
        "projected",
        "permanent_failure",
        "resolved",
        "abandoned",
    }
    if status not in allowed:
        raise ValueError(f"unsupported continuation status: {status}")
    if continuation.status in {"resolved", "abandoned"}:
        return continuation
    updates = {
        "status": status,
        "updated_at": utcnow(),
        "delivery_error": delivery_error,
    }
    if status == "open":
        updates.update(
            outbound_message_id=None,
            response_snapshot=None,
            delivery_started_at=None,
            delivery_acknowledged_at=None,
        )
    if response_snapshot is not None:
        updates["response_snapshot"] = response_snapshot
    if status == "acknowledged":
        updates["delivery_acknowledged_at"] = utcnow()
    return continuation.model_copy(update=updates)


def _rejected(
    continuation: PendingAgentContinuation,
    code: str,
) -> ContinuationMatch:
    return ContinuationMatch(
        allowed=False,
        code=code,
        continuation_id=continuation.continuation_id,
    )
