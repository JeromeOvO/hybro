"""Readiness counts that gate the C2 legacy lifecycle deletions."""

from __future__ import annotations

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


def _operator_holds(value: Any, operator: str, operand: Any) -> bool:
    if operator == "$ne":
        return value != operand
    if operator == "$in":
        return value in operand
    if operator == "$nin":
        return value not in operand
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
            if not all(
                _operator_holds(value, operator, operand)
                for operator, operand in condition.items()
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


def test_cli_help_runs_without_a_configured_install():
    result = subprocess.run(
        [sys.executable, "scripts/check_legacy_lifecycle_readiness.py", "--help"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )

    assert result.returncode == 0
    assert "--mongo-url" in result.stdout
