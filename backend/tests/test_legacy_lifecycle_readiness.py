"""Readiness counts that gate the C2 legacy lifecycle deletions."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.check_legacy_lifecycle_readiness import collect_readiness, format_report

BACKEND = Path(__file__).resolve().parents[1]
_MISSING = object()


def _get(document: dict[str, Any], path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _present(document: dict[str, Any], path: str) -> bool:
    value: Any = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return True


def _operator_holds(value: Any, operator: str, operand: Any) -> bool:
    if operator == "$type":
        assert operand == "string", "fake supports only $type string"
        return isinstance(value, str)
    if operator == "$ne":
        return value != operand
    if operator == "$in":
        return value in operand
    if operator == "$nin":
        return value not in operand
    if operator == "$regex":
        return isinstance(value, str) and re.search(operand, value) is not None
    if operator == "$elemMatch":
        return isinstance(value, list) and any(
            isinstance(item, dict) and _matches(item, operand) for item in value
        )
    raise AssertionError(f"fake does not support {operator}")


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    """Evaluate the subset of Mongo query syntax the script uses.

    A missing field reads as None, as in Mongo equality and ``$in``/``$ne``.
    """
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
            continue
        value = _get(document, key)
        if isinstance(condition, dict):
            if (
                "$exists" in condition
                and _present(document, key) != condition["$exists"]
            ):
                return False
            if not all(
                _operator_holds(value, operator, operand)
                for operator, operand in condition.items()
                if operator != "$exists"
            ):
                return False
        elif value != condition:
            return False
    return True


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for document in self.documents if _matches(document, query))

    async def distinct(self, key: str, query: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        for document in self.documents:
            if _matches(document, query):
                value = _get(document, key)
                if value not in values:
                    values.append(value)
        return values


class FakeDatabase:
    def __init__(self, collections: dict[str, list[dict[str, Any]]]) -> None:
        self.collections = collections

    def __getitem__(self, name: str) -> FakeCollection:
        return FakeCollection(self.collections.get(name, []))


def _by_name(report):
    return {check.name: check for check in report}


@pytest.mark.asyncio
async def test_empty_database_is_ready_everywhere():
    report = await collect_readiness(FakeDatabase({}))

    assert report
    assert all(check.count == 0 for check in report)
    assert all(check.gates for check in report)


@pytest.mark.asyncio
async def test_first_generation_runs_are_counted_by_status():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "orchestration_runs": [
                        {"status": "completed"},
                        {"status": "completed"},
                        {"status": "running"},
                    ]
                }
            )
        )
    )

    check = report["orchestration_runs"]
    assert check.count == 3
    assert check.by_status == {"completed": 2, "running": 1}


@pytest.mark.asyncio
async def test_legacy_orchestrator_runs_include_v5_and_missing_family_of_any_status():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "orchestrator_runs": [
                        {"status": "completed", "schema_version": 5},
                        {"status": "running"},
                        {
                            "status": "failed",
                            "lifecycle_family": "legacy",
                            "schema_version": 6,
                        },
                        {
                            "status": "running",
                            "lifecycle_family": "canonical",
                            "schema_version": 6,
                        },
                    ]
                }
            )
        )
    )

    check = report["orchestrator_runs_legacy"]
    assert check.count == 3
    assert check.by_status == {"completed": 1, "running": 1, "failed": 1}


@pytest.mark.asyncio
async def test_only_open_a2a_resume_interactions_count():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "hitl_interactions": [
                        {"application_route": "a2a_resume", "status": "open"},
                        {"application_route": "a2a_resume", "status": "applying"},
                        {"application_route": "a2a_resume", "status": "applied"},
                        {"application_route": "a2a_resume", "status": "expired"},
                        {"application_route": "supervisor_run", "status": "open"},
                    ]
                }
            )
        )
    )

    assert report["open_a2a_resume_interactions"].count == 2


@pytest.mark.asyncio
async def test_only_pending_cancellation_markers_count():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "cancelled_messages": [
                        {"reconciliation_status": "pending"},
                        {"reconciliation_status": "reconciled"},
                        {},
                    ]
                }
            )
        )
    )

    assert report["pending_cancellation_markers"].count == 1


@pytest.mark.asyncio
async def test_pending_continuation_counts_both_message_collections():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "room_agent_messages": [
                        {"pending_continuation": {"a2a_task_id": "t"}},
                        {"pending_continuation": None},
                        {},
                    ],
                    "room_user_messages": [
                        {"pending_continuation": {"a2a_task_id": "u"}},
                    ],
                }
            )
        )
    )

    assert report["pending_continuation"].count == 2


@pytest.mark.asyncio
async def test_tracked_tasks_count_only_non_terminal_states():
    def message(state: str, tracked: bool = True) -> dict[str, Any]:
        return {
            "has_task_tracking": tracked,
            "message_content": {"message_task": {"status": {"state": state}}},
        }

    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "room_agent_messages": [
                        message("working"),
                        message("input-required"),
                        message("completed"),
                        message("working", tracked=False),
                    ]
                }
            )
        )
    )

    assert report["tracked_non_terminal_tasks"].count == 2


@pytest.mark.asyncio
async def test_webhook_token_hashes_count():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "room_agent_messages": [
                        {"webhook_token_hash": "abc"},
                        {"webhook_token_hash": None},
                        {},
                    ]
                }
            )
        )
    )

    assert report["webhook_token_hashes"].count == 1


@pytest.mark.asyncio
async def test_untracked_working_cards_exclude_orchestrator_managed_ones():
    def card(
        state: str, run_id: object = _MISSING, tracked: bool | None = None
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "message_content": {"message_task": {"status": {"state": state}}},
        }
        if tracked is not None:
            document["has_task_tracking"] = tracked
        if run_id is not _MISSING:
            document["extend_info"] = {"orchestrator_run_id": run_id}
        return document

    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "room_agent_messages": [
                        card("submitted"),
                        card("working", run_id=None),
                        card("working", run_id=""),
                        card("working", run_id="run-1"),
                        card("working", tracked=True),
                        card("working", tracked=False),
                        card("input-required"),
                        card("completed"),
                    ]
                }
            )
        )
    )

    assert report["untracked_working_cards"].count == 4


@pytest.mark.asyncio
async def test_turn_completion_kind_counts_user_messages_that_carry_it():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "room_user_messages": [
                        {"extend_info": {"turn_completion_kind": "final_answer"}},
                        {"extend_info": {"turn_completion_kind": None}},
                        {"extend_info": {}},
                    ]
                }
            )
        )
    )

    assert report["turn_completion_kind"].count == 1


@pytest.mark.asyncio
async def test_report_names_each_count_and_the_step_it_gates():
    report = await collect_readiness(
        FakeDatabase({"cancelled_messages": [{"reconciliation_status": "pending"}]})
    )

    text = format_report(report)

    assert "pending_cancellation_markers: 1" in text
    assert "C2 step 2b" in text
    assert "orchestration_runs: 0" in text


@pytest.mark.asyncio
async def test_strict_reader_shapes_are_counted():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "hitl_requests": [
                        {"application_route": "supervisor_run", "status": "processing"},
                        {"status": "pending"},
                    ],
                    "room_user_messages": [
                        {"client_request_id": "request-1"},
                        {
                            "client_request_id": "request-2",
                            "idempotency_fingerprint": None,
                        },
                        {
                            "client_request_id": "request-3",
                            "idempotency_fingerprint": "fingerprint",
                        },
                        {},
                    ],
                    "orchestrator_hitl_interactions": [
                        {"interaction_id": "missing"},
                        {"interaction_id": "false", "published": False},
                        {"interaction_id": "true", "published": True},
                    ],
                    "rooms": [
                        {"applied_from_group": "group-1"},
                        {"applied_from_group": "group-1", "source_group_id": ""},
                        {"applied_from_group": "group-1", "source_group_id": "g"},
                        {"applied_from_group": None},
                        {},
                    ],
                    "agents": [
                        {
                            "agent_id": "v03-url-only",
                            "is_public": True,
                            "agent_card": {"url": "u"},
                        },
                        {
                            "agent_id": "v03-interface",
                            "is_public": True,
                            "agent_card": {
                                "supportedInterfaces": [
                                    {"url": "u", "protocolVersion": "1.0"},
                                    {"url": "u", "protocolVersion": "0.3.0"},
                                ]
                            },
                        },
                        {
                            "agent_id": "v1",
                            "is_public": True,
                            "agent_card": {
                                "supportedInterfaces": [
                                    {"url": "u", "protocolVersion": "1.0"}
                                ]
                            },
                        },
                        {"agent_id": "missing", "normalized_url": "u"},
                        {"agent_id": "null", "is_public": None, "normalized_url": "u"},
                        {
                            "agent_id": "public",
                            "is_public": True,
                            "normalized_url": "u",
                        },
                        {
                            "agent_id": "private",
                            "is_public": False,
                            "normalized_url": "u",
                        },
                    ],
                    "orchestrator_agent_tool_bindings": [
                        {"binding_id": "missing"},
                        {"binding_id": "null", "authorization_kind": None},
                        {"binding_id": "set", "authorization_kind": "room_member"},
                    ],
                    "orchestrator_runs": [
                        {
                            "status": "running",
                            "lifecycle_family": "canonical",
                            "schema_version": 6,
                            "candidate_scope": {"authorization_basis": None},
                        },
                        {
                            "status": "completed",
                            "lifecycle_family": "canonical",
                            "schema_version": 6,
                            "candidate_scope": {},
                        },
                        {
                            "status": "running",
                            "lifecycle_family": "canonical",
                            "schema_version": 6,
                            "candidate_scope": {
                                "authorization_basis": {"kind": "room_member"}
                            },
                        },
                    ],
                }
            )
        )
    )

    assert report["processing_hitl_requests"].count == 1
    assert report["user_messages_missing_idempotency_fingerprint"].count == 2
    assert report["orchestrator_hitl_interactions_missing_published"].count == 1
    assert report["rooms_applied_from_group_without_source_group_id"].count == 2
    assert report["agents_missing_is_public"].count == 2
    # Every fixture agent without a 1.0 card counts, including the bare ones.
    assert report["agents_with_pre_1_0_cards"].count == 6
    assert report["tool_bindings_missing_authorization_kind"].count == 2
    runs = report["orchestrator_runs_missing_authorization_basis"]
    assert runs.count == 2
    assert runs.by_status == {"running": 1, "completed": 1}


def test_cli_help_runs_without_a_configured_install():
    result = subprocess.run(
        # As documented: a module run from backend/, so imports resolve from the
        # checkout rather than from an installed package.
        [sys.executable, "-m", "scripts.check_legacy_lifecycle_readiness", "--help"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )

    assert result.returncode == 0
    assert "--mongo-url" in result.stdout


@pytest.mark.asyncio
async def test_leftover_a2a_resume_rows_count_where_reconcilers_still_read_them():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "hitl_resume_commands": [
                        {"kind": "a2a_resume", "status": "delivery_uncertain"},
                        {"kind": "a2a_resume", "status": "projected"},
                        {
                            "kind": "a2a_resume",
                            "status": "projected",
                            "aggregate_applied_at": "2026-01-01",
                        },
                        {"kind": "supervisor_resume", "status": "retryable_error"},
                    ],
                    "hitl_interactions": [
                        {"application_route": "a2a_resume", "status": "canceled"},
                        {
                            "application_route": "a2a_resume",
                            "status": "expired",
                            "terminal_reconciled": True,
                        },
                        {"application_route": "a2a_resume", "status": "applied"},
                        {"application_route": "supervisor_run", "status": "failed"},
                    ],
                    "hitl_requests": [
                        {"application_route": "a2a_resume", "status": "canceled"},
                        {
                            "application_route": "a2a_resume",
                            "status": "expired",
                            "cancellation_reconciled": True,
                        },
                        {"application_route": "a2a_resume", "status": "pending"},
                        {"application_route": "a2a_resume", "status": "responded"},
                        {"application_route": "supervisor_run", "status": "pending"},
                    ],
                }
            )
        )
    )

    assert report["unsettled_a2a_resume_commands"].count == 2
    assert report["unreconciled_terminal_a2a_resume_interactions"].count == 1
    assert report["unreconciled_terminal_a2a_resume_requests"].count == 1
    assert report["open_a2a_resume_requests"].count == 1


@pytest.mark.asyncio
async def test_agents_without_a_normalized_url_count():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "agents": [
                        {"agent_id": "a1"},
                        {"agent_id": "a2", "normalized_url": None},
                        {"agent_id": "a3", "normalized_url": "https://a"},
                    ]
                }
            )
        )
    )

    assert report["agents_missing_normalized_url"].count == 1


@pytest.mark.asyncio
async def test_rooms_with_active_membership_origin_status_count():
    report = _by_name(
        await collect_readiness(
            FakeDatabase(
                {
                    "rooms": [
                        {"room_id": "r1", "membership_origin_status": "active"},
                        {"room_id": "r2", "membership_origin_status": "manual"},
                        {
                            "room_id": "r3",
                            "membership_origin_status": "seeded_never_edited",
                        },
                        {"room_id": "r4"},
                    ]
                }
            )
        )
    )

    assert report["rooms_with_active_membership_origin_status"].count == 1
