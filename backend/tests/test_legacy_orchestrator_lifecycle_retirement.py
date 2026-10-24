"""C2 step 3: legacy orchestrator runs are deleted and their branches retired."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from execution.orchestrator.lifecycle import LifecycleEmitter, SessionEvent
from execution.orchestrator.models import OrchestratorRunState
from scripts.delete_legacy_orchestrator_runs import delete_legacy_orchestrator_runs
from tests.test_legacy_lifecycle_readiness import FakeDatabase, _matches

BACKEND = Path(__file__).resolve().parents[1]


class DeletingCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def find(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        rows = [dict(row) for row in self.documents if _matches(row, query)]

        class _Cursor:
            async def to_list(self, length=None):
                return rows

        return _Cursor()

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for row in self.documents if _matches(row, query))

    async def delete_many(self, query: dict[str, Any]):
        kept = [row for row in self.documents if not _in_matches(row, query)]
        deleted = len(self.documents) - len(kept)
        self.documents[:] = kept
        return type("Result", (), {"deleted_count": deleted})()


def _in_matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$and":
            if not all(_in_matches(row, branch) for branch in condition):
                return False
        elif isinstance(condition, dict) and "$in" in condition and len(condition) == 1:
            if row.get(key) not in condition["$in"]:
                return False
        elif not _matches(row, {key: condition}):
            return False
    return True


class DeletingDatabase(FakeDatabase):
    def __init__(self, collections: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__(collections)
        self._handles = {
            name: DeletingCollection(rows) for name, rows in collections.items()
        }

    def __getitem__(self, name: str) -> DeletingCollection:
        if name not in self._handles:
            self.collections[name] = []
            self._handles[name] = DeletingCollection(self.collections[name])
        return self._handles[name]


def _database() -> DeletingDatabase:
    return DeletingDatabase(
        {
            "orchestrator_runs": [
                {"run_id": "v5", "schema_version": 5, "status": "completed"},
                {"run_id": "legacy", "lifecycle_family": "legacy", "status": "failed"},
                {"run_id": "unmarked", "status": "running"},
                {
                    "run_id": "canonical",
                    "lifecycle_family": "canonical",
                    "schema_version": 6,
                    "status": "running",
                },
            ],
            "orchestrator_run_events": [
                {"run_id": "v5", "sequence": 1},
                {"run_id": "legacy", "sequence": 1},
                {"run_id": "canonical", "sequence": 1},
            ],
            "orchestrator_recovery_leases": [
                {"run_id": "unmarked"},
                {"run_id": "canonical"},
            ],
            "orchestrator_agent_calls": [
                {"run_id": "legacy", "status": "working"},
                {"run_id": "canonical", "status": "working"},
            ],
            "orchestrator_agent_tool_bindings": [
                {"run_id": "v5"},
                {"run_id": "canonical"},
            ],
        }
    )


@pytest.mark.asyncio
async def test_dry_run_counts_legacy_runs_without_deleting():
    database = _database()

    result = await delete_legacy_orchestrator_runs(database, apply=False)

    assert result.matched_run_ids == ["v5", "legacy", "unmarked"]
    assert result.deleted == {}
    assert len(database.collections["orchestrator_runs"]) == 4


@pytest.mark.asyncio
async def test_apply_deletes_legacy_runs_and_their_run_scoped_rows():
    database = _database()

    result = await delete_legacy_orchestrator_runs(database, apply=True)

    assert result.deleted == {
        "orchestrator_run_events": 2,
        "orchestrator_recovery_leases": 1,
        "orchestrator_agent_calls": 1,
        "orchestrator_agent_tool_bindings": 1,
        "orchestrator_runs": 3,
    }
    for name in (
        "orchestrator_runs",
        "orchestrator_run_events",
        "orchestrator_recovery_leases",
        "orchestrator_agent_calls",
        "orchestrator_agent_tool_bindings",
    ):
        assert [row["run_id"] for row in database.collections[name]] == ["canonical"]


@pytest.mark.asyncio
async def test_apply_is_a_no_op_when_no_legacy_runs_remain():
    database = DeletingDatabase(
        {"orchestrator_runs": [{"run_id": "c", "lifecycle_family": "canonical"}]}
    )

    result = await delete_legacy_orchestrator_runs(database, apply=True)

    assert result.matched_run_ids == []
    assert result.deleted == {}


def _canonical_document() -> dict[str, Any]:
    from tests._orchestrator_helpers import make_run

    return make_run().model_dump(mode="json")


def test_run_state_defaults_to_canonical_schema_six():
    fields = OrchestratorRunState.model_fields

    assert fields["schema_version"].default == 6
    assert fields["lifecycle_family"].default == "canonical"


@pytest.mark.parametrize(
    "update",
    [
        {"schema_version": 5, "lifecycle_family": "legacy"},
        {"lifecycle_family": "legacy"},
        {"schema_version": 5},
    ],
)
def test_legacy_run_documents_no_longer_validate(update):
    document = _canonical_document()
    document.update(update)

    with pytest.raises(ValidationError):
        OrchestratorRunState.model_validate(document)


def _terminal_run_with_batch(*, entry_state: str, flushed: bool):
    from execution.orchestrator.models import ToolBatchEntry, ToolCallBatch, ToolResult
    from tests._orchestrator_helpers import make_run

    batch = ToolCallBatch(
        assistant_message_id="assistant-1",
        entries=[
            ToolBatchEntry(
                call_id="call-1",
                assistant_message_id="assistant-1",
                source_index=0,
                tool_name="agent_weather",
                state=entry_state,
                buffered_terminal_result=(
                    ToolResult(
                        call_id="call-1",
                        tool_name="agent_weather",
                        status="completed",
                        content=[],
                        artifact_refs=[],
                    )
                    if entry_state == "terminal"
                    else None
                ),
            )
        ],
        results_flushed=flushed,
    )
    document = make_run().model_dump(mode="json")
    document.update(status="completed", tool_batches=[batch.model_dump(mode="json")])
    return document


@pytest.mark.parametrize(
    ("entry_state", "flushed", "message"),
    [
        ("executing", True, "open declared Tools"),
        ("terminal", False, "every Tool batch flushed"),
    ],
)
def test_terminal_runs_reject_open_tools_and_unflushed_batches(
    entry_state, flushed, message
):
    document = _terminal_run_with_batch(entry_state=entry_state, flushed=flushed)

    with pytest.raises(ValidationError, match=message):
        OrchestratorRunState.model_validate(document)


def test_terminal_run_with_closed_flushed_batch_validates():
    document = _terminal_run_with_batch(entry_state="terminal", flushed=True)

    assert OrchestratorRunState.model_validate(document).status == "completed"


def test_session_events_carry_no_lifecycle_family():
    assert "lifecycle_family" not in SessionEvent.model_fields


@pytest.mark.asyncio
async def test_every_lifecycle_event_awaits_its_listeners():
    emitter = LifecycleEmitter()
    listener = AsyncMock()
    emitter.subscribe(listener)
    event = SessionEvent(
        event_type="turn_started",
        session_id="session-1",
        run_id="run-1",
        causation_id="message-1",
        sequence=1,
        timestamp=datetime.now(UTC),
        payload={},
    )

    await emitter.emit(event)

    listener.assert_awaited_once_with(event)


def test_projection_translator_has_no_lifecycle_family():
    from execution.orchestrator.public_projection import PublicProjectionTranslator

    parameters = inspect.signature(PublicProjectionTranslator).parameters

    assert "lifecycle_family" not in parameters


def test_hitl_service_has_no_lifecycle_family_reader():
    from approval.factory import create_hitl_service
    from approval.service import HITLService

    assert "lifecycle_family_reader" not in inspect.signature(HITLService).parameters
    with pytest.raises(TypeError, match="lifecycle_family_reader"):
        create_hitl_service(lifecycle_family_reader=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "canonical", "allows_resume"),
    [
        ("missing", False, True),
        ("running", True, True),
        ("canceling", True, False),
        ("completed", True, False),
    ],
)
async def test_hitl_service_reads_canonical_ownership_from_run_state(
    state, canonical, allows_resume
):
    from approval.service import HITLService

    service = HITLService(canonical_run_state_reader=AsyncMock(return_value=state))
    interaction = {"orchestration_run_id": "run-1"}

    assert await service._is_canonical_run("run-1") is canonical
    assert await service.canonical_run_allows_resume(interaction) is allows_resume


def test_container_has_no_legacy_orchestrator_projection():
    import container

    source = inspect.getsource(container)

    for name in (
        "legacy_projection_translator",
        "_emit_legacy_orchestrator_terminal",
        "read_orchestrator_lifecycle_family",
    ):
        assert name not in source


def test_producer_policy_marks_private_fields_without_legacy_wording():
    source = (BACKEND / "delivery" / "producer_policy.py").read_text()

    assert "private_legacy_only" not in source
    assert '"private_only"' in source


def test_lifecycle_family_is_only_the_run_model_field():
    model = BACKEND / "execution" / "orchestrator" / "models.py"
    excluded = {"tests", "build", "scripts", ".venv"}
    offenders = []
    for path in BACKEND.rglob("*.py"):
        relative = path.relative_to(BACKEND)
        if relative.parts[0] in excluded or path == model:
            continue
        if "lifecycle_family" in path.read_text():
            offenders.append(str(relative))

    assert offenders == []
    assert model.read_text().count("lifecycle_family") == 1
