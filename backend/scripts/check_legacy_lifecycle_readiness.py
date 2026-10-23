#!/usr/bin/env python3
"""Count the legacy data that gates each C2 and C8 cleanup step. Read-only.

Each count must be zero (or migrated) before the plan step it names deletes the
code that reads that data, or before deploying code whose strict readers reject
that data. The script only runs ``count_documents`` and ``distinct``; it never
writes.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

from common.a2a_constants import NON_TERMINAL_STATES
from common.dto.turn_lifecycle import CANONICAL_RUN_EVENT_KINDS
from models.hitl import HITLInteractionStatus

_CLOSED_INTERACTION_STATUSES = [
    HITLInteractionStatus.APPLIED.value,
    HITLInteractionStatus.CANCELED.value,
    HITLInteractionStatus.EXPIRED.value,
    HITLInteractionStatus.FAILED.value,
]
_NON_TERMINAL_TASK_STATES = sorted(state.value for state in NON_TERMINAL_STATES)
_TASK_STATE = "message_content.message_task.status.state"
# Shared with scripts/delete_legacy_orchestrator_runs.py, which removes these rows.
LEGACY_ORCHESTRATOR_RUN_QUERY: dict[str, Any] = {
    "$or": [
        {"lifecycle_family": {"$ne": "canonical"}},
        {"schema_version": 5},
    ]
}


@dataclass(frozen=True)
class _Check:
    name: str
    gates: str
    collections: tuple[str, ...]
    query: dict[str, Any]
    by_status: bool = False


@dataclass(frozen=True)
class ReadinessCount:
    name: str
    gates: str
    count: int
    by_status: dict[str, int] = field(default_factory=dict)


CHECKS = (
    _Check(
        name="orchestration_runs",
        gates="C2 step 2 (first-generation run store)",
        collections=("orchestration_runs",),
        query={},
        by_status=True,
    ),
    _Check(
        name="orchestrator_runs_legacy",
        gates="C2 step 3 (lifecycle family; delete_legacy_orchestrator_runs.py)",
        collections=("orchestrator_runs",),
        query=LEGACY_ORCHESTRATOR_RUN_QUERY,
        by_status=True,
    ),
    _Check(
        name="pending_cancellation_markers",
        gates="C2 step 2b (cancellation-marker path)",
        collections=("cancelled_messages",),
        query={"reconciliation_status": "pending"},
    ),
    _Check(
        name="open_a2a_resume_interactions",
        gates="C2 step 5 (legacy HITL reply path)",
        collections=("hitl_interactions",),
        query={
            "application_route": "a2a_resume",
            "status": {"$nin": _CLOSED_INTERACTION_STATUSES},
        },
    ),
    # Closed a2a_resume rows that a reconciler or cancellation cleanup still
    # reads; strict HITL models reject the retired route after C2 step 5.
    _Check(
        name="unsettled_a2a_resume_commands",
        gates="C2 step 5 (legacy HITL reply path)",
        collections=("hitl_resume_commands",),
        query={
            "kind": "a2a_resume",
            "$or": [
                {"status": {"$ne": "projected"}},
                {"aggregate_applied_at": None},
            ],
        },
    ),
    _Check(
        name="unreconciled_terminal_a2a_resume_interactions",
        gates="C2 step 5 (legacy HITL reply path)",
        collections=("hitl_interactions",),
        query={
            "application_route": "a2a_resume",
            "status": {
                "$in": [
                    HITLInteractionStatus.CANCELED.value,
                    HITLInteractionStatus.EXPIRED.value,
                    HITLInteractionStatus.FAILED.value,
                ]
            },
            "terminal_reconciled": {"$ne": True},
        },
    ),
    _Check(
        name="unreconciled_terminal_a2a_resume_requests",
        gates="C2 step 5 (legacy HITL reply path)",
        collections=("hitl_requests",),
        query={
            "application_route": "a2a_resume",
            "status": {"$in": ["canceled", "expired"]},
            "cancellation_reconciled": {"$ne": True},
        },
    ),
    _Check(
        name="open_a2a_resume_requests",
        gates="C2 step 5 (legacy HITL reply path)",
        collections=("hitl_requests",),
        query={
            "application_route": "a2a_resume",
            "status": {"$in": ["pending", "processing", "answer_recorded"]},
        },
    ),
    _Check(
        name="pending_continuation",
        gates="C2 step 6 (pending_continuation field)",
        collections=("room_agent_messages", "room_user_messages"),
        query={"pending_continuation": {"$ne": None}},
    ),
    _Check(
        name="tracked_non_terminal_tasks",
        gates="C2 step 6 (tracked-task machinery)",
        collections=("room_agent_messages",),
        query={
            "has_task_tracking": True,
            _TASK_STATE: {"$in": _NON_TERMINAL_TASK_STATES},
        },
    ),
    _Check(
        name="untracked_working_cards",
        gates="C2 step 6 (stale-checker step 5; plan C0 row 2)",
        collections=("room_agent_messages",),
        query={
            "has_task_tracking": {"$ne": True},
            _TASK_STATE: {"$in": ["submitted", "working"]},
            "extend_info.orchestrator_run_id": {"$in": [None, ""]},
        },
    ),
    _Check(
        name="webhook_token_hashes",
        gates="C2 step 7 (webhook ingress)",
        collections=("room_agent_messages",),
        query={"webhook_token_hash": {"$ne": None}},
    ),
    _Check(
        name="turn_completion_kind",
        gates="C2 step 8 (turn_completion_kind readers)",
        collections=("room_user_messages",),
        query={"extend_info.turn_completion_kind": {"$ne": None}},
    ),
    _Check(
        name="agents_missing_normalized_url",
        gates="C8 (normalized-url scan in AgentMongoRepository)",
        collections=("agents",),
        query={"normalized_url": {"$exists": False}},
    ),
    # Stored-data tolerances that strict readers no longer accept.
    _Check(
        name="processing_hitl_requests",
        gates="strict readers (HITLStatus.PROCESSING delivery lease)",
        collections=("hitl_requests",),
        query={"status": "processing"},
    ),
    _Check(
        name="user_messages_missing_idempotency_fingerprint",
        gates="strict readers (fingerprint-less idempotency replay)",
        collections=("room_user_messages",),
        query={
            "client_request_id": {"$type": "string"},
            "idempotency_fingerprint": None,
        },
    ),
    _Check(
        name="orchestrator_hitl_interactions_missing_published",
        gates="strict readers (orchestrator HITL published flag)",
        collections=("orchestrator_hitl_interactions",),
        query={"published": {"$nin": [True, False]}},
    ),
    _Check(
        name="rooms_applied_from_group_without_source_group_id",
        gates="strict readers (Room.applied_from_group provenance)",
        collections=("rooms",),
        query={
            "applied_from_group": {"$nin": [None, ""]},
            "source_group_id": {"$in": [None, ""]},
        },
    ),
    _Check(
        name="rooms_with_active_membership_origin_status",
        gates="strict readers (Room.membership_origin_status enum)",
        collections=("rooms",),
        query={"membership_origin_status": "active"},
    ),
    _Check(
        name="agents_missing_is_public",
        gates="strict readers (agent visibility is_public)",
        collections=("agents",),
        query={"is_public": {"$nin": [True, False]}},
    ),
    _Check(
        name="tool_bindings_missing_authorization_kind",
        gates="strict readers (AgentToolBindingRecord.authorization_kind)",
        collections=("orchestrator_agent_tool_bindings",),
        query={"authorization_kind": None},
    ),
    # Retired run_event kinds (llm_call_completed, tool_call_*, run_completed,
    # ...). The snapshot ignores them, so these turns render without a trace.
    _Check(
        name="non_canonical_run_events",
        gates="retired non-canonical run-event trace and runs snapshot folds",
        collections=("room_events",),
        query={
            "kind": "run_event",
            "payload_public.type": {"$nin": sorted(CANONICAL_RUN_EVENT_KINDS)},
        },
    ),
    _Check(
        name="orchestrator_runs_missing_authorization_basis",
        gates="strict readers (CandidateScopeSnapshot.authorization_basis)",
        collections=("orchestrator_runs",),
        query={"candidate_scope.authorization_basis": None},
        by_status=True,
    ),
)


async def _count(database: Any, check: _Check) -> ReadinessCount:
    total = 0
    by_status: dict[str, int] = {}
    for name in check.collections:
        collection = database[name]
        total += await collection.count_documents(check.query)
        if not check.by_status:
            continue
        for status in await collection.distinct("status", check.query):
            query = {**check.query, "status": status}
            key = str(status)
            by_status[key] = by_status.get(key, 0) + await collection.count_documents(
                query
            )
    return ReadinessCount(
        name=check.name, gates=check.gates, count=total, by_status=by_status
    )


async def collect_readiness(database: Any) -> list[ReadinessCount]:
    return [await _count(database, check) for check in CHECKS]


def format_report(report: list[ReadinessCount]) -> str:
    lines = []
    for item in report:
        state = "ready" if item.count == 0 else "NOT READY"
        lines.append(f"{item.name}: {item.count} [{state}] gates {item.gates}")
        for status, count in sorted(item.by_status.items()):
            lines.append(f"  status={status}: {count}")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--mongo-url", help="defaults to the configured backend mongodb_url"
    )
    parser.add_argument(
        "--database", help="defaults to the configured backend mongodb_db_name"
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    if args.mongo_url is None or args.database is None:
        from common.config import settings

        args.mongo_url = args.mongo_url or settings.mongodb_url
        args.database = args.database or settings.mongodb_db_name

    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(args.mongo_url)
    try:
        await client.admin.command("ping")
        print(format_report(await collect_readiness(client[args.database])))
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(_main())
