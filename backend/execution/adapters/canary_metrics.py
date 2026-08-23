"""Canary observability for the orchestrator dark launch (plan §8.2).

Metrics are derived exclusively from the existing orchestrator durable stores
(``orchestrator_runs``, ``orchestrator_agent_calls``,
``orchestrator_a2a_observation_conflicts``). No new fact source, telemetry
backend, or side-effect is introduced here: the collector only reads, and the
threshold evaluator is a pure function that returns breach messages for the
leader-elected canary job to log.

The one metric that has no durable backing is the recovery-cycle last-run time.
``A2ARecoveryCycle.run_once`` does not persist a completion marker today, so the
caller supplies ``recovery_cycle_last_run_at`` (tracked in-process by the
recovery job). A ``None`` value means "no completed cycle yet" and the
corresponding threshold is simply not evaluated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

TERMINAL_RUN_STATUSES = frozenset(
    {"completed", "failed", "canceled", "budget_exhausted"}
)
TERMINAL_AGENT_CALL_STATES = frozenset(
    {"completed", "failed", "canceled", "rejected", "expired"}
)
_RUN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "waiting_external",
        "awaiting_user",
        "finalizing",
        "completed",
        "failed",
        "canceled",
        "budget_exhausted",
    }
)


async def collect_metrics(
    runs: Any,
    agent_calls: Any,
    conflicts: Any,
    *,
    recovery_cycle_last_run_at: datetime | None = None,
    window_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read the durable orchestrator stores and return canary metrics.

    ``runs``, ``agent_calls``, and ``conflicts`` are collection-like objects
    exposing ``find(query) -> cursor`` (the bounded Mongo collections behind the
    orchestrator stores). The collector performs a full scan; traffic during the
    dark launch is expected to be zero-to-small, so this is acceptable for the
    canary job and can be replaced with server-side aggregation later.
    """
    now = _as_utc(now) if now is not None else datetime.now(UTC)
    run_metrics = _scan_runs(
        await _documents(runs.find({})),
        cutoff=now - timedelta(seconds=window_seconds),
    )
    agent_calls_outstanding = _count_outstanding_calls(
        await _documents(agent_calls.find({}))
    )
    observation_conflicts_open = _count_open_conflicts(
        await _documents(conflicts.find({}))
    )

    return {
        "runs_by_status": {
            status: run_metrics["runs_by_status"].get(status, 0)
            for status in sorted(_RUN_STATUSES)
        },
        "runs_by_status_window": {
            status: run_metrics["runs_by_status_window"].get(status, 0)
            for status in sorted(_RUN_STATUSES)
        },
        "projection_outbox_pending": run_metrics["projection_pending"],
        "projection_outbox_blocked": run_metrics["projection_blocked"],
        "projection_outbox_blocked_oldest_at": run_metrics[
            "projection_blocked_oldest_at"
        ],
        "agent_calls_outstanding": agent_calls_outstanding,
        "observation_conflicts_open": observation_conflicts_open,
        "recovery_cycle_last_run_at": _as_utc(recovery_cycle_last_run_at)
        if recovery_cycle_last_run_at is not None
        else None,
        "collected_at": now,
    }


def _scan_runs(documents: list[dict[str, Any]], *, cutoff: datetime) -> dict[str, Any]:
    runs_by_status: dict[str, int] = {}
    runs_by_status_window: dict[str, int] = {}
    projection_pending = 0
    projection_blocked = 0
    projection_blocked_oldest_at: datetime | None = None

    for document in documents:
        status = document.get("status")
        if not isinstance(status, str):
            continue
        runs_by_status[status] = runs_by_status.get(status, 0) + 1
        updated_at = _as_utc(document.get("updated_at"))
        if updated_at is not None and updated_at >= cutoff:
            runs_by_status_window[status] = runs_by_status_window.get(status, 0) + 1
        pending, blocked = _count_intents(document)
        projection_pending += pending
        projection_blocked += blocked
        if (
            blocked
            and updated_at is not None
            and (
                projection_blocked_oldest_at is None
                or updated_at < projection_blocked_oldest_at
            )
        ):
            projection_blocked_oldest_at = updated_at

    return {
        "runs_by_status": runs_by_status,
        "runs_by_status_window": runs_by_status_window,
        "projection_pending": projection_pending,
        "projection_blocked": projection_blocked,
        "projection_blocked_oldest_at": projection_blocked_oldest_at,
    }


def _count_intents(document: dict[str, Any]) -> tuple[int, int]:
    pending = 0
    blocked = 0
    for intent in document.get("projection_outbox") or []:
        if not isinstance(intent, dict):
            continue
        status = intent.get("status")
        if status == "pending":
            pending += 1
        elif status == "blocked":
            blocked += 1
    return pending, blocked


def _count_outstanding_calls(documents: list[dict[str, Any]]) -> int:
    outstanding = 0
    for document in documents:
        state = document.get("state")
        if isinstance(state, str) and state not in TERMINAL_AGENT_CALL_STATES:
            outstanding += 1
    return outstanding


def _count_open_conflicts(documents: list[dict[str, Any]]) -> int:
    return sum(1 for document in documents if document.get("status") == "open")


def evaluate_canary_thresholds(metrics: dict[str, Any], settings_obj: Any) -> list[str]:
    """Return human-readable breach messages for the §8.2 thresholds."""
    collected_at = metrics.get("collected_at") or datetime.now(UTC)
    breaches: list[str] = []

    window = metrics.get("runs_by_status_window", {})
    terminal_in_window = sum(window.get(status, 0) for status in TERMINAL_RUN_STATUSES)
    failures_in_window = window.get("failed", 0) + window.get("budget_exhausted", 0)
    if terminal_in_window > 0:
        failure_rate = failures_in_window / terminal_in_window
        if failure_rate > settings_obj.orchestrator_canary_run_failure_rate_max:
            breaches.append(
                "orchestrator canary: run failure rate "
                f"{failure_rate:.4f} exceeds "
                f"{settings_obj.orchestrator_canary_run_failure_rate_max:.4f} "
                f"({failures_in_window}/{terminal_in_window} terminal runs in window)"
            )

    blocked = int(metrics.get("projection_outbox_blocked", 0) or 0)
    if blocked > 0:
        oldest_at = metrics.get("projection_outbox_blocked_oldest_at")
        if oldest_at is not None:
            age_seconds = (collected_at - oldest_at).total_seconds()
            if (
                age_seconds
                > settings_obj.orchestrator_canary_blocked_intent_max_age_seconds
            ):
                breaches.append(
                    "orchestrator canary: "
                    f"{blocked} blocked projection intent(s) require requeue "
                    f"(oldest age {age_seconds:.0f}s)"
                )
        else:
            breaches.append(
                f"orchestrator canary: {blocked} blocked projection intent(s) "
                "require requeue"
            )

    last_run = metrics.get("recovery_cycle_last_run_at")
    if last_run is not None:
        age_seconds = (collected_at - last_run).total_seconds()
        if (
            age_seconds
            > settings_obj.orchestrator_canary_recovery_cycle_max_age_seconds
        ):
            breaches.append(
                "orchestrator canary: recovery cycle is stale "
                f"({age_seconds:.0f}s since last completed cycle)"
            )

    conflicts = int(metrics.get("observation_conflicts_open", 0) or 0)
    if conflicts > settings_obj.orchestrator_canary_observation_conflicts_max:
        breaches.append(
            f"orchestrator canary: {conflicts} open observation conflict(s)"
        )

    return breaches


async def _documents(cursor: Any) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        values = await cursor.to_list(length=None)
        return [value for value in values if isinstance(value, dict)]
    return [value async for value in cursor if isinstance(value, dict)]


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "TERMINAL_AGENT_CALL_STATES",
    "TERMINAL_RUN_STATUSES",
    "collect_metrics",
    "evaluate_canary_thresholds",
]
