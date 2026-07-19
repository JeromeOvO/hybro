"""
Unit tests for SupervisorExecutor module.

Tests cover:
- _log_and_return: passes through result, includes trajectory metadata
- _save_interrupted_state: saves trajectory on unexpected failure
- CLARIFY cleanup compensation: orphan requests are canceled on failure
"""

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.utils.time import utcnow
from execution.orchestration.dispatch_payload import ResolvedDispatchPayload
from execution.orchestration.planner import RoomSupervisorPlannerAdapter
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from execution.orchestration.supervisor_executor import SupervisorExecutor
from models.orchestration import (
    AgentOutputRecord,
    DispatchContentRef,
    DispatchIntent,
    DispatchRefKind,
    OrchestrationRunState,
    OrchestrationStatus,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)
from models.processing import ProcessingResult, ProcessingStatus
from models.supervisor import (
    ActionType,
    AgentProfile,
    ClarifyQuestion,
    DelegateTarget,
    RoomConfig,
    RunStatus,
    StepResult,
    StepStatus,
    SupervisorAction,
    SupervisorRunResult,
    SupervisorTrajectory,
    TrajectoryEntry,
)

_ROOT = Path(__file__).resolve().parents[1]


def _make_supervisor_executor():
    se = object.__new__(SupervisorExecutor)
    se.message_reader = AsyncMock()
    se.message_writer = AsyncMock()
    se.task_state_store = AsyncMock()
    se.continuation_store = AsyncMock()
    se.delivery = MagicMock()
    se.room_runtime = MagicMock()
    se.supervisor_service = MagicMock()
    se.tsm = MagicMock()
    se.agent_dispatcher = MagicMock()
    se.agent_message_processor = MagicMock()
    se.room_memory = MagicMock()
    se.rate_limit_service = MagicMock()
    se.hitl_coordinator = MagicMock()
    se.orchestration_resource_provider = SimpleNamespace(
        list_resources=AsyncMock(return_value=[]),
        resolve_ref=AsyncMock(return_value=None),
    )

    async def raw_action_provider(_context):
        result = se.supervisor_service.decide_next()
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, SupervisorAction):
            payload = result.model_dump(mode="json")
            for question in payload.get("questions") or []:
                if question.get("prompt_type") is None:
                    question["prompt_type"] = "text"
            if payload.get("prompt_type") is None:
                payload.pop("prompt_type", None)
            return payload
        return result

    se.orchestration_planner = RoomSupervisorPlannerAdapter(
        raw_action_provider=raw_action_provider
    )
    return se


def _make_dispatch_target() -> PlannedDelegateTarget:
    return PlannedDelegateTarget(
        agent_id="agent-1",
        agent_name="Test Agent",
        task="Read the attachment.",
    )


def _make_agent_profile() -> AgentProfile:
    return AgentProfile(agent_id="agent-1", agent_name="Test Agent")


def _state_unification_user_message(message_id="msg-1", extend_info=None):
    return SimpleNamespace(
        message_id=message_id,
        user_id="user-1",
        extend_info=extend_info or {},
        client_request_id="cr-1",
    )


def _make_resolved_agent():
    return SimpleNamespace(
        agent_id="agent-1",
        agent_card=SimpleNamespace(default_input_modes=["text"]),
        rate_limit_per_user_per_hour=100,
        rate_limit_system_per_hour=1000,
    )


def _make_supervisor_agent_message(*, preflight: bool):
    extend_info = None
    if preflight:
        extend_info = {
            "attachment_preflight_failure": {
                "code": "file_too_large",
                "message": "Attached file report.pdf exceeds the inline A2A limit.",
            }
        }
    return SimpleNamespace(
        message_id="amsg-1",
        turn_id=None,
        client_request_id="client-req-1",
        extend_info=extend_info,
    )


@pytest.mark.asyncio
async def test_supervisor_preflight_failed_result_persists_and_notifies_task():
    se = _make_supervisor_executor()
    message = _make_supervisor_agent_message(preflight=True)
    se.agent_dispatcher.resolve_agent = AsyncMock(return_value=_make_resolved_agent())
    se.room_runtime.create_agent_message.return_value = message
    se.message_writer.add_room_agent_message = AsyncMock()
    se.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
        return_value="client-req-1"
    )
    se.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.FAILED,
            response_text="Attached file report.pdf exceeds the inline A2A limit.",
            status_message="file_too_large",
        )
    )
    se.tsm.fail_pre_dispatch_task = AsyncMock()
    se.delivery.send_task_update = AsyncMock()

    results = await se._dispatch_targets(
        [_make_dispatch_target()],
        [_make_agent_profile()],
        "room-1",
        "umsg-1",
        1,
        None,
        None,
        None,
    )

    assert results[0].status == StepStatus.FAILED
    assert results[0].error_message == (
        "Attached file report.pdf exceeds the inline A2A limit."
    )
    se.tsm.fail_pre_dispatch_task.assert_awaited_once_with(
        message,
        error="Attached file report.pdf exceeds the inline A2A limit.",
        error_code="file_too_large",
    )
    se.delivery.send_task_update.assert_awaited_once()
    assert se.delivery.send_task_update.await_args.kwargs == {
        "room_id": "room-1",
        "message_id": "amsg-1",
        "status": "failed",
        "error": "Attached file report.pdf exceeds the inline A2A limit.",
        "agent_name": "Test Agent",
        "agent_id": "agent-1",
        "step_number": 1,
        "total_steps": None,
        "task_content": "Read the attachment.",
        "client_request_id": "client-req-1",
    }


@pytest.mark.asyncio
async def test_supervisor_dispatch_marks_agent_message_explicit_refs_only_and_records_refs():
    se = _make_supervisor_executor()
    message = _make_supervisor_agent_message(preflight=False)
    target = _make_dispatch_target()
    target.attachment_refs = []
    target.artifact_refs = []
    target.context_refs = []
    target.expected_outputs = []
    target.attachment_policy = "explicit_refs_only"
    se.agent_dispatcher.resolve_agent = AsyncMock(return_value=_make_resolved_agent())
    se.rate_limit_service = None
    se.room_runtime.create_agent_message.return_value = message
    se.message_writer.add_room_agent_message = AsyncMock(return_value=True)
    se.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
        return_value="client-req-1"
    )
    se.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.SUCCESS,
            response_text="Agent completed.",
        )
    )

    result = await se._dispatch_targets(
        [target],
        [_make_agent_profile()],
        "room-1",
        "user-msg-1",
        1,
        None,
        "user-1",
        None,
    )

    assert result[0].success is True
    assert message.extend_info == {"public_task_label": "Requesting Test Agent"}
    processor_call = se.agent_message_processor.process_single_message.await_args
    assert processor_call.kwargs["attachment_forwarding_policy"] == (
        "explicit_refs_only"
    )
    assert processor_call.kwargs["resolved_resource_payloads"] == []
    assert processor_call.kwargs["explicit_attachment_refs"] == []


@pytest.mark.asyncio
async def test_supervisor_dispatch_resolves_payload_refs_in_live_path(monkeypatch):
    se = _make_supervisor_executor()
    message = _make_supervisor_agent_message(preflight=False)
    target = _make_dispatch_target()
    target.context_refs = [
        DispatchContentRef(kind=DispatchRefKind.CONTEXT, ref_id="room-background")
    ]
    target.artifact_refs = [
        DispatchContentRef(kind=DispatchRefKind.ARTIFACT, ref_id="artifact-1")
    ]
    target.required_resource_refs = ["room-background", "artifact-1"]
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Use selected refs",
        candidate_agent_ids=["agent-1"],
        artifacts=[
            {
                "artifact_key": "artifact-1",
                "name": "Broker submission",
                "mime_type": "application/json",
                "summary": "Structured facts for underwriting.",
                "source_agent_message_id": "agent-msg-source",
            }
        ],
    )
    calls = []

    async def fake_resolve_dispatch_payload_refs(**kwargs):
        calls.append(kwargs)
        return ResolvedDispatchPayload(
            selected_context_refs=["room-background"],
            selected_artifact_refs=["artifact-1"],
            selected_attachment_refs=[],
            attachment_failures=[],
        )

    monkeypatch.setattr(
        "execution.orchestration.supervisor_executor.resolve_dispatch_payload_refs",
        fake_resolve_dispatch_payload_refs,
        raising=False,
    )
    se.agent_dispatcher.resolve_agent = AsyncMock(return_value=_make_resolved_agent())
    se.rate_limit_service = None
    se.room_runtime.create_agent_message.return_value = message
    se.message_writer.add_room_agent_message = AsyncMock(return_value=True)
    se.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
        return_value="client-req-1"
    )
    se.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.SUCCESS,
            response_text="Agent completed.",
        )
    )

    result = await se._dispatch_targets(
        [target],
        [_make_agent_profile()],
        "room-1",
        "user-msg-1",
        1,
        None,
        "user-1",
        None,
        run_state=state,
        original_attachments=[],
    )

    assert result[0].success is True
    assert len(calls) == 1
    assert calls[0]["run_state"] is state
    assert calls[0]["required_resource_refs"] == [
        "room-background",
        "artifact-1",
    ]
    create_kwargs = se.room_runtime.create_agent_message.call_args.kwargs
    assert create_kwargs["content"] == "Requesting Test Agent"
    assert create_kwargs["task_content"] == "Requesting Test Agent"
    processor_call = se.agent_message_processor.process_single_message.await_args
    assert "artifact-1" in processor_call.kwargs["dispatch_task"]
    assert "Broker submission" in processor_call.kwargs["dispatch_task"]


@pytest.mark.asyncio
async def test_supervisor_dispatch_missing_required_context_ref_fails_before_processing():
    se = _make_supervisor_executor()
    target = _make_dispatch_target()
    target.context_refs = [
        DispatchContentRef(kind=DispatchRefKind.CONTEXT, ref_id="missing-fact")
    ]
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Use selected context",
        candidate_agent_ids=["agent-1"],
        facts=[
            {
                "fact_id": "fact-1",
                "text": "Known context.",
            }
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="run-1:step-1",
                step_target_id="run-1:step-1:target-1",
                dispatch_intent_id="run-1:step-1:target-1:intent",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Read the context.",
                task_hash="hash",
                context_refs=target.context_refs,
            )
        ],
    )
    se.run_store = InMemoryOrchestrationRunStore()
    await se.run_store.create_run(state)
    se.agent_dispatcher.resolve_agent = AsyncMock(return_value=_make_resolved_agent())
    se.message_reader.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    se.rate_limit_service = None
    se.room_runtime.create_agent_message = MagicMock()
    se.message_writer.add_room_agent_message = AsyncMock(return_value=True)
    se.agent_message_processor.process_single_message = AsyncMock()

    result = await se._dispatch_targets(
        [target],
        [_make_agent_profile()],
        "room-1",
        "user-msg-1",
        1,
        None,
        "user-1",
        None,
        planned_message_ids=["agent-msg-1"],
        run_state=state,
        original_attachments=[],
    )

    assert result[0].status == StepStatus.FAILED
    assert result[0].success is False
    assert result[0].agent_message_id == "agent-msg-1"
    assert result[0].status_message == "context_ref_not_found"
    assert result[0].error_message == "Context ref not found: missing-fact."
    se.room_runtime.create_agent_message.assert_not_called()
    se.agent_message_processor.process_single_message.assert_not_awaited()

    updated = await se._ingest_v2_results(
        state,
        result,
        status=OrchestrationStatus.RUNNING,
        advance_step=True,
    )

    assert len(updated.open_failures) == 1
    assert updated.open_failures[0].status == "open"
    assert updated.open_failures[0].error_code == "context_ref_not_found"


@pytest.mark.asyncio
async def test_supervisor_dispatch_projects_valid_context_ref_into_agent_task():
    se = _make_supervisor_executor()
    message = _make_supervisor_agent_message(preflight=False)
    target = _make_dispatch_target()
    target.task = "Use the selected context."
    target.context_refs = [
        DispatchContentRef(kind=DispatchRefKind.CONTEXT, ref_id="fact-1")
    ]
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Use selected context",
        candidate_agent_ids=["agent-1"],
        facts=[
            {
                "fact_id": "fact-1",
                "summary": "Replacement cost is 1.2M from the broker submission.",
            }
        ],
    )
    se.agent_dispatcher.resolve_agent = AsyncMock(return_value=_make_resolved_agent())
    se.rate_limit_service = None
    se.room_runtime.create_agent_message.return_value = message
    se.message_writer.add_room_agent_message = AsyncMock(return_value=True)
    se.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
        return_value="client-req-1"
    )
    se.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.SUCCESS,
            response_text="Agent completed.",
        )
    )

    result = await se._dispatch_targets(
        [target],
        [_make_agent_profile()],
        "room-1",
        "user-msg-1",
        1,
        None,
        "user-1",
        None,
        run_state=state,
        original_attachments=[],
    )

    assert result[0].success is True
    create_kwargs = se.room_runtime.create_agent_message.call_args.kwargs
    assert create_kwargs["task_content"] == "Requesting Test Agent"
    assert message.extend_info == {"public_task_label": "Requesting Test Agent"}
    processor_call = se.agent_message_processor.process_single_message.await_args
    assert "[Backend-selected references]" in processor_call.kwargs["dispatch_task"]
    assert "Selected context refs:" in processor_call.kwargs["dispatch_task"]
    assert "ref=fact-1" in processor_call.kwargs["dispatch_task"]
    assert "Replacement cost is 1.2M" in processor_call.kwargs["dispatch_task"]


@pytest.mark.asyncio
async def test_supervisor_dispatch_missing_required_attachment_ref_fails_before_processing():
    se = _make_supervisor_executor()
    target = _make_dispatch_target()
    target.attachment_refs = [
        DispatchContentRef(kind=DispatchRefKind.ATTACHMENT, ref_id="missing-file")
    ]
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Use selected refs",
        candidate_agent_ids=["agent-1"],
        dispatch_intents=[
            DispatchIntent(
                step_id="run-1:step-1",
                step_target_id="run-1:step-1:target-1",
                dispatch_intent_id="run-1:step-1:target-1:intent",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Read the attachment.",
                task_hash="hash",
                attachment_refs=target.attachment_refs,
            )
        ],
    )
    se.run_store = InMemoryOrchestrationRunStore()
    await se.run_store.create_run(state)
    se.agent_dispatcher.resolve_agent = AsyncMock(return_value=_make_resolved_agent())
    se.message_reader.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    se.rate_limit_service = None
    se.room_runtime.create_agent_message = MagicMock()
    se.message_writer.add_room_agent_message = AsyncMock(return_value=True)
    se.agent_message_processor.process_single_message = AsyncMock()

    result = await se._dispatch_targets(
        [target],
        [_make_agent_profile()],
        "room-1",
        "user-msg-1",
        1,
        None,
        "user-1",
        None,
        planned_message_ids=["agent-msg-1"],
        run_state=state,
        original_attachments=[],
    )

    assert result[0].status == StepStatus.FAILED
    assert result[0].success is False
    assert result[0].agent_message_id == "agent-msg-1"
    assert result[0].status_message == "attachment_ref_not_found"
    assert result[0].error_message == "Attachment ref not found: missing-file."
    se.room_runtime.create_agent_message.assert_not_called()
    se.agent_message_processor.process_single_message.assert_not_awaited()

    updated = await se._ingest_v2_results(
        state,
        result,
        status=OrchestrationStatus.RUNNING,
        advance_step=True,
    )

    assert len(updated.open_failures) == 1
    assert updated.open_failures[0].status == "open"
    assert updated.open_failures[0].error_code == "attachment_ref_not_found"


@pytest.mark.asyncio
async def test_supervisor_logs_planner_decision_with_refs(caplog):
    se = _make_supervisor_executor()
    se.run_store = InMemoryOrchestrationRunStore()
    state = await se.run_store.create_run(
        OrchestrationRunState(
            run_id="run-1",
            room_id="room-1",
            user_message_id="msg-1",
            goal="Coordinate the selected agents",
            candidate_agent_ids=["agent-1"],
            status=OrchestrationStatus.RUNNING,
        )
    )
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Use broker artifact for insurer.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Use broker artifact.",
                artifact_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ARTIFACT,
                        ref_id="broker-msg:artifact_id:submission",
                    )
                ],
            )
        ],
    )

    with caplog.at_level("INFO"):
        await se._record_v2_planner_action(state, action)

    assert any(
        record.message == "supervisor_planner_decision"
        and record.__dict__.get("action") == "delegate"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_run_creates_orchestration_state_without_legacy_or_trajectory_checkpoint(
    monkeypatch,
):
    store = InMemoryOrchestrationRunStore()
    executor = _make_supervisor_executor()
    executor.run_store = store
    user_message = _state_unification_user_message(
        extend_info={
            "orchestration": True,
            "orchestration_run_id": "msg-1",
            "candidate_scope_mode": "explicit_selection",
            "candidate_agent_ids": ["agent-1"],
        }
    )

    monkeypatch.setattr(
        executor,
        "_execute_orchestration_loop",
        AsyncMock(
            return_value=SupervisorRunResult(
                status=RunStatus.COMPLETED,
                run_id="msg-1",
                trajectory=None,
            )
        ),
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="msg-1",
        message_text="Need quote",
        agent_registry=[_make_agent_profile()],
        room_config=SimpleNamespace(room_agent_set={"agent-1": "Agent One"}),
        user_message=user_message,
    )

    state = await store.get_run("msg-1")
    assert result.run_id == "msg-1"
    assert result.trajectory is None
    assert state is not None
    assert state.candidate_scope is not None
    assert state.candidate_scope.agent_ids == ["agent-1"]
    assert "supervisor_trajectory" not in (user_message.extend_info or {})


@pytest.mark.asyncio
async def test_run_state_loader_uses_orchestration_run_id_for_existing_state():
    store = InMemoryOrchestrationRunStore()
    executor = _make_supervisor_executor()
    executor.run_store = store
    user_message = _state_unification_user_message(
        message_id="msg-1",
        extend_info={
            "orchestration": True,
            "orchestration_run_id": "run-1",
            "candidate_agent_ids": ["agent-1"],
        },
    )
    existing = await store.reconstruct_from_envelope(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        envelope=user_message.extend_info,
        goal="Need quote",
    )
    existing.status = OrchestrationStatus.COMPLETED
    existing.terminal_reason = "already completed"
    await store.create_run(existing)

    state = await executor._load_or_create_run_state_for_run(
        room_id="room-1",
        user_message_id="msg-1",
        message_text="Need quote",
        agent_registry=[_make_agent_profile()],
        room_config=RoomConfig(),
        user_message=user_message,
    )

    assert state.run_id == "run-1"
    assert state.user_message_id == "msg-1"
    assert state.status == OrchestrationStatus.COMPLETED
    assert state.terminal_reason == "already completed"
    assert await store.get_run("msg-1") is None


@pytest.mark.asyncio
async def test_run_state_loader_creates_state_with_orchestration_run_id():
    store = InMemoryOrchestrationRunStore()
    executor = _make_supervisor_executor()
    executor.run_store = store
    user_message = _state_unification_user_message(
        message_id="msg-1",
        extend_info={
            "orchestration": True,
            "orchestration_run_id": "run-1",
            "candidate_scope_snapshot_id": "scope-snapshot-1",
            "candidate_scope_mode": "explicit_selection",
            "candidate_agent_ids": ["agent-1"],
        },
    )

    state = await executor._load_or_create_run_state_for_run(
        room_id="room-1",
        user_message_id="msg-1",
        message_text="Need quote",
        agent_registry=[_make_agent_profile()],
        room_config=RoomConfig(),
        user_message=user_message,
    )

    assert state.run_id == "run-1"
    assert state.user_message_id == "msg-1"
    assert await store.get_run("run-1") is not None
    assert await store.get_run("msg-1") is None
    assert state.candidate_scope is not None
    assert state.candidate_scope.snapshot_id == "scope-snapshot-1"
    assert state.candidate_scope.agent_ids == ["agent-1"]


@pytest.mark.asyncio
async def test_run_terminalizes_state_when_planner_raises_unhandled_error():
    class ExplodingPlanner:
        async def plan(self, _context):
            raise RuntimeError("planner unavailable")

    store = InMemoryOrchestrationRunStore()
    executor = _make_supervisor_executor()
    executor.run_store = store
    executor.orchestration_planner = ExplodingPlanner()
    user_message = _state_unification_user_message(
        message_id="msg-1",
        extend_info={
            "orchestration": True,
            "orchestration_run_id": "msg-1",
            "candidate_scope_mode": "explicit_selection",
            "candidate_agent_ids": ["agent-1"],
        },
    )

    with pytest.raises(RuntimeError, match="planner unavailable"):
        await executor.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="Need quote",
            agent_registry=[_make_agent_profile()],
            room_config=SimpleNamespace(room_agent_set={"agent-1": "Agent One"}),
            user_message=user_message,
        )

    state = await store.get_run("msg-1")
    assert state is not None
    assert state.status == OrchestrationStatus.FAILED
    assert state.terminal_reason == "supervisor execution failed unexpectedly"
    assert await store.list_recoverable() == []


@pytest.mark.asyncio
async def test_run_copies_step_budget_from_extend_info(monkeypatch):
    store = InMemoryOrchestrationRunStore()
    executor = _make_supervisor_executor()
    executor.run_store = store
    user_message = _state_unification_user_message(
        message_id="msg-1",
        extend_info={
            "orchestration": True,
            "orchestration_run_id": "msg-1",
            "candidate_agent_ids": ["agent-1"],
            "orchestration_step_budget": 12,
        },
    )
    monkeypatch.setattr(
        executor,
        "_execute_orchestration_loop",
        AsyncMock(
            return_value=SupervisorRunResult(
                status=RunStatus.COMPLETED,
                run_id="msg-1",
                trajectory=None,
            )
        ),
    )

    await executor.run(
        room_id="room-1",
        user_message_id="msg-1",
        message_text="Need quote",
        agent_registry=[_make_agent_profile()],
        room_config=SimpleNamespace(room_agent_set={"agent-1": "Agent One"}),
        user_message=user_message,
    )

    state = await store.get_run("msg-1")
    assert state is not None
    assert state.step_budget == 12


@pytest.mark.asyncio
async def test_run_state_loader_falls_back_to_latest_by_user_message_id():
    store = InMemoryOrchestrationRunStore()
    executor = _make_supervisor_executor()
    executor.run_store = store
    user_message = _state_unification_user_message(
        message_id="msg-1",
        extend_info={
            "orchestration": True,
            "candidate_agent_ids": ["agent-1"],
        },
    )
    existing = await store.reconstruct_from_envelope(
        run_id="run-latest",
        room_id="room-1",
        user_message_id="msg-1",
        envelope=user_message.extend_info,
        goal="Need quote",
    )
    existing.status = OrchestrationStatus.AWAITING_USER
    existing.pending_hitl_request_ids = ["hitl-1"]
    await store.create_run(existing)

    state = await executor._load_or_create_run_state_for_run(
        room_id="room-1",
        user_message_id="msg-1",
        message_text="Need quote",
        agent_registry=[_make_agent_profile()],
        room_config=RoomConfig(),
        user_message=user_message,
    )

    assert state.run_id == "run-latest"
    assert state.status == OrchestrationStatus.AWAITING_USER
    assert state.pending_hitl_request_ids == ["hitl-1"]
    assert await store.get_run("msg-1") is None


@pytest.mark.asyncio
async def test_execute_orchestration_loop_returns_terminal_run_state_without_trajectory():
    executor = _make_supervisor_executor()
    state = OrchestrationRunState(
        run_id="msg-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Need quote",
        candidate_agent_ids=[],
        status=OrchestrationStatus.COMPLETED,
        terminal_reason="already completed",
    )

    result = await executor._execute_orchestration_loop(
        state=state,
        room_id="room-1",
        user_message_id="msg-1",
        message_text="Need quote",
        agent_registry=[],
        room_config=RoomConfig(),
        conversation_context=None,
        token=None,
        request_user_id="user-1",
        quoted_text=None,
        user_message=None,
    )

    assert result.status == RunStatus.COMPLETED
    assert result.trajectory is None
    assert result.run_id == "msg-1"
    assert result.run_state is not None
    assert result.run_state.status == OrchestrationStatus.COMPLETED
    assert result.terminal_reason == "already completed"


@pytest.mark.asyncio
async def test_run_synthesis_action_projects_state_agent_outputs_to_synthesis_trajectory():
    executor = _make_supervisor_executor()
    executor.run_store = InMemoryOrchestrationRunStore()
    executor.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
        return_value="cr-1"
    )
    captured = {}

    async def stream_synthesis(**kwargs):
        captured["trajectory"] = kwargs["trajectory"]
        return "Final synthesis"

    executor._stream_supervisor_synthesis = AsyncMock(side_effect=stream_synthesis)
    state = OrchestrationRunState(
        run_id="msg-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Need quote",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.RUNNING,
        client_request_id="cr-1",
        dispatch_intents=[
            DispatchIntent(
                step_id="msg-1:step-1",
                step_target_id="msg-1:step-1:target-1",
                dispatch_intent_id="msg-1:step-1:target-1:intent",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Find pricing",
                task_hash="hash",
            )
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status=StepStatus.SUCCESS.value,
                text="Agent found the enterprise quote.",
            )
        ],
    )
    state = await executor.run_store.create_run(state)

    result = await executor._run_synthesis_action(
        state=state,
        planner_action=PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="Summarize",
            synthesis_instruction="Write the final answer",
        ),
        room_id="room-1",
        user_message_id="msg-1",
        token=None,
    )

    projected = captured["trajectory"]
    projected_texts = [
        step.response_text
        for entry in projected.entries
        for step in entry.results
        if step.success
    ]
    assert "Agent found the enterprise quote." in projected_texts
    assert result.trajectory is None
    assert result.run_state is not None
    assert result.run_state.status == OrchestrationStatus.COMPLETED


def test_compat_trajectory_projects_completed_agent_output_as_success():
    state = OrchestrationRunState(
        run_id="msg-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Need quote",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.RUNNING,
        dispatch_intents=[
            DispatchIntent(
                step_id="msg-1:step-1",
                step_target_id="msg-1:step-1:target-1",
                dispatch_intent_id="msg-1:step-1:target-1:intent",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Find pricing",
                task_hash="hash",
            )
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                text="Completed agent output is visible.",
            )
        ],
    )

    trajectory = SupervisorExecutor._compat_trajectory_from_state(state)

    assert len(trajectory.entries) == 1
    projected = trajectory.entries[0].results[0]
    assert projected.status == StepStatus.SUCCESS
    assert projected.success is True
    assert projected.response_text == "Completed agent output is visible."


def test_compat_trajectory_preserves_a2a_metadata_from_agent_output():
    state = OrchestrationRunState(
        run_id="msg-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Need quote",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.RUNNING,
        dispatch_intents=[
            DispatchIntent(
                step_id="msg-1:step-1",
                step_target_id="msg-1:step-1:target-1",
                dispatch_intent_id="msg-1:step-1:target-1:intent",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Find pricing",
                task_hash="hash",
            )
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="awaiting_input",
                text="Need the effective date.",
                a2a_task_id="task-1",
                a2a_context_id="ctx-1",
                status_message="Provide the effective date.",
            )
        ],
    )

    trajectory = SupervisorExecutor._compat_trajectory_from_state(state)

    projected = trajectory.entries[0].results[0]
    assert projected.a2a_task_id == "task-1"
    assert projected.a2a_context_id == "ctx-1"
    assert projected.status_message == "Provide the effective date."


def test_v2_result_from_output_record_projects_completed_status_as_success():
    intent = DispatchIntent(
        step_id="msg-1:step-1",
        step_target_id="msg-1:step-1:target-1",
        dispatch_intent_id="msg-1:step-1:target-1:intent",
        planned_agent_message_id="agent-msg-1",
        agent_id="agent-1",
        task="Find pricing",
        task_hash="hash",
    )
    output = AgentOutputRecord(
        agent_message_id="agent-msg-1",
        agent_id="agent-1",
        status="completed",
        text="Recovered completed output.",
    )

    result = SupervisorExecutor._v2_result_from_output_record(
        intent,
        output,
        {"agent-1": "Agent One"},
        1,
    )

    assert result is not None
    assert result.status == StepStatus.SUCCESS
    assert result.success is True
    assert result.response_text == "Recovered completed output."


def test_resolve_pending_results_from_outputs_projects_completed_status_as_success():
    state = OrchestrationRunState(
        run_id="msg-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Need quote",
        candidate_agent_ids=["agent-1"],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                text="Pending output finished.",
            )
        ],
    )
    entry = TrajectoryEntry(
        step_number=1,
        action=SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="Wait for agent",
            targets=[
                DelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Find pricing",
                )
            ],
        ),
        results=[
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="Agent One",
                task="Find pricing",
                response_text="",
                success=False,
                status=StepStatus.PAUSED,
                agent_message_id="agent-msg-1",
            )
        ],
        started_at=utcnow(),
    )

    SupervisorExecutor._resolve_v2_pending_results_from_outputs(state, entry)

    resolved = entry.results[0]
    assert resolved.status == StepStatus.SUCCESS
    assert resolved.success is True
    assert resolved.response_text == "Pending output finished."
    assert entry.completed_at is not None


@pytest.mark.asyncio
async def test_supervisor_generic_failed_result_does_not_create_preflight_task():
    se = _make_supervisor_executor()
    message = _make_supervisor_agent_message(preflight=False)
    se.agent_dispatcher.resolve_agent = AsyncMock(return_value=_make_resolved_agent())
    se.room_runtime.create_agent_message.return_value = message
    se.message_writer.add_room_agent_message = AsyncMock()
    se.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
        return_value="client-req-1"
    )
    se.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.FAILED,
            response_text="Agent processing failed downstream.",
            status_message=None,
        )
    )
    se.tsm.fail_pre_dispatch_task = AsyncMock()
    se.delivery.send_task_update = AsyncMock()

    results = await se._dispatch_targets(
        [_make_dispatch_target()],
        [_make_agent_profile()],
        "room-1",
        "umsg-1",
        1,
        None,
        None,
        None,
    )

    assert results[0].status == StepStatus.FAILED
    assert results[0].error_message == "Agent processing failed downstream."
    se.tsm.fail_pre_dispatch_task.assert_not_awaited()
    se.delivery.send_task_update.assert_not_awaited()


def test_dispatch_targets_cancelled_error_handler_reraises():
    tree = ast.parse(
        (_ROOT / "execution" / "orchestration" / "supervisor_executor.py").read_text()
    )
    dispatch_targets = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_dispatch_targets"
    )
    handlers = [
        node
        for node in ast.walk(dispatch_targets)
        if isinstance(node, ast.ExceptHandler)
        and ast.unparse(node.type) == "asyncio.CancelledError"
    ]
    assert handlers, "CancelledError handler not found"
    for handler in handlers:
        assert any(isinstance(stmt, ast.Raise) for stmt in handler.body)
        assert not any(isinstance(stmt, ast.Return) for stmt in handler.body)


def test_supervisor_agent_hitl_request_passes_selected_message_ids():
    source = (
        _ROOT / "execution" / "orchestration" / "supervisor_executor.py"
    ).read_text()
    agent_hitl_anchor = "async def _run_agent_awaiting_input_action("
    start = source.index(
        "request = await self.hitl_coordinator.request_input(",
        source.index(agent_hitl_anchor),
    )
    end = source.index("if request is None:", start)
    request_call = source[start:end]

    assert "continuation_message_id=continuation_message_id" in request_call
    assert "display_message_id=display_message_id" in request_call
    assert request_call.index(
        "continuation_message_id=continuation_message_id"
    ) < request_call.index("display_message_id=display_message_id")


# =============================================================================
# _log_and_return Tests
# =============================================================================


class TestLogAndReturn:
    @pytest.mark.asyncio
    async def test_returns_result_unchanged(self):
        trajectory = SupervisorTrajectory()
        result = SupervisorRunResult(status="completed", trajectory=trajectory)
        se = _make_supervisor_executor()
        returned = await se._log_and_return(
            "room-1", trajectory, result
        )
        assert returned is result
        assert returned.status == "completed"

    @pytest.mark.asyncio
    async def test_returns_result_in_debate_mode(self):
        trajectory = SupervisorTrajectory()
        result = SupervisorRunResult(status="completed", trajectory=trajectory)
        se = _make_supervisor_executor()
        returned = await se._log_and_return(
            "room-1", trajectory, result, debate_mode=True
        )
        assert returned is result


# =============================================================================
# CLARIFY cleanup compensation Tests
# =============================================================================


class TestClarifyCleanupCompensation:
    """Tests that the CLARIFY handler cleans up HITL requests and messages
    when _save_interrupted_state fails or request_input returns None mid-group."""

    @pytest.fixture
    def se(self):
        return _make_supervisor_executor()

    def _make_room_config(self):
        cfg = MagicMock()
        cfg.is_debate_mode = False
        return cfg

    @pytest.mark.asyncio
    async def test_cancels_requests_when_save_interrupted_state_fails(self, se):
        """If all questions are created but continuation save fails,
        all HITL requests and messages must be cleaned up."""
        from models.supervisor import (
            ActionType,
            ClarifyQuestion,
            SupervisorAction,
        )

        hitl_mock = AsyncMock()
        req_a = MagicMock(request_id="req-a")
        req_b = MagicMock(request_id="req-b")
        hitl_mock.request_input = AsyncMock(side_effect=[req_a, req_b])
        hitl_mock.cancel_request = AsyncMock()

        agent_msg = MagicMock(message_id="msg-agent-1")
        se.room_runtime.create_agent_message.return_value = agent_msg
        se.message_writer.add_room_agent_message = AsyncMock()
        se.message_writer.delete_room_agent_message_by_message_id = AsyncMock()

        action = SupervisorAction(
            action=ActionType.CLARIFY,
            reasoning="need info",
            questions=[
                ClarifyQuestion(prompt="Q1?"),
                ClarifyQuestion(prompt="Q2?"),
            ],
        )

        se.supervisor_service.decide_next = AsyncMock(return_value=action)
        se._save_interrupted_state = AsyncMock(return_value=False)

        se.hitl_coordinator = hitl_mock
        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Hello",
            agent_registry=[],
            room_config=self._make_room_config(),
            request_user_id="user-1",
        )

        assert result.status == "failed"
        assert hitl_mock.cancel_request.await_count == 2
        hitl_mock.cancel_request.assert_any_await("req-a", "room-1")
        hitl_mock.cancel_request.assert_any_await("req-b", "room-1")

    @pytest.mark.asyncio
    async def test_cancels_prior_requests_when_request_input_returns_none(self, se):
        """If request_input returns None mid-group (e.g. max rounds),
        previously created requests must be canceled."""
        from models.supervisor import (
            ActionType,
            ClarifyQuestion,
            SupervisorAction,
        )

        req_a = MagicMock()
        req_a.request_id = "req-a"

        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(side_effect=[req_a, None])
        hitl_mock.cancel_request = AsyncMock()

        agent_msg = MagicMock(message_id="msg-agent-1")
        se.room_runtime.create_agent_message.return_value = agent_msg
        se.message_writer.add_room_agent_message = AsyncMock()
        se.message_writer.delete_room_agent_message_by_message_id = AsyncMock()

        action = SupervisorAction(
            action=ActionType.CLARIFY,
            reasoning="need info",
            questions=[
                ClarifyQuestion(prompt="Q1?"),
                ClarifyQuestion(prompt="Q2?"),
            ],
        )

        se.supervisor_service.decide_next = AsyncMock(return_value=action)

        se.hitl_coordinator = hitl_mock
        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Hello",
            agent_registry=[],
            room_config=self._make_room_config(),
            request_user_id="user-1",
        )

        assert result.status == "failed"
        assert hitl_mock.cancel_request.await_count == 1
        hitl_mock.cancel_request.assert_awaited_once_with("req-a", "room-1")
        assert se.message_writer.delete_room_agent_message_by_message_id.await_count == 2


class TestProcessingStatusLifecycleOrder:
    @pytest.mark.asyncio
    async def test_stage_notification_records_before_send(self):
        se = _make_supervisor_executor()
        order: list[str] = []
        emit = AsyncMock(side_effect=lambda *a, **k: order.append("emit"))
        se.bind_execution_event_deps(emit)
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.DONE,
                reasoning="nothing else to do",
            )
        )
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[],
            room_config=RoomConfig(),
            user_message=_state_unification_user_message(
                "msg-1",
                {"orchestration_step_budget": 1},
            ),
        )

        assert result.status == RunStatus.FAILED
        emit.assert_awaited_once()
        se.delivery.send_processing_status.assert_not_called()
        assert order == ["emit"]

    @pytest.mark.asyncio
    async def test_stage_notification_helper_failure_is_swallowed(self):
        se = _make_supervisor_executor()
        se.delivery.send_processing_status = AsyncMock()
        emit = AsyncMock(side_effect=RuntimeError("lifecycle unavailable"))
        se.bind_execution_event_deps(emit)
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.DONE,
                reasoning="nothing else to do",
            )
        )
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[],
            room_config=RoomConfig(),
            user_message=_state_unification_user_message(
                "msg-1",
                {"orchestration_step_budget": 1},
            ),
        )

        assert result.status == RunStatus.FAILED
        emit.assert_awaited_once()
        se.delivery.send_processing_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_awaiting_input_records_before_send(self):
        se = _make_supervisor_executor()
        order: list[str] = []
        emit = AsyncMock(
            side_effect=lambda *a, **k: order.append("emit")
            if k.get("status") == "awaiting_input"
            else None
        )
        se.bind_execution_event_deps(emit)
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="ask agent",
                targets=[
                    DelegateTarget(
                        agent_id="agent-1",
                        agent_name="Agent",
                        task="ask",
                    )
                ],
            )
        )
        se._dispatch_targets = AsyncMock(
            return_value=[
                StepResult(
                    step_number=1,
                    agent_id="agent-1",
                    agent_name="Agent",
                    task="ask",
                    response_text="",
                    success=False,
                    status=StepStatus.AWAITING_INPUT,
                    paused_message_id="agent-msg-1",
                    agent_message_id="agent-msg-1",
                    status_message="Need input",
                )
            ]
        )
        se._save_interrupted_state = AsyncMock(return_value=True)
        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(
            return_value=SimpleNamespace(request_id="hitl-1")
        )

        se.hitl_coordinator = hitl_mock
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[
                AgentProfile(agent_id="agent-1", agent_name="Agent")
            ],
            room_config=RoomConfig(),
        )

        assert result.status == RunStatus.AWAITING_INPUT
        assert order == ["emit"]
        se.delivery.send_processing_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_hitl_receives_orchestration_run_link(self):
        se = _make_supervisor_executor()
        se.bind_execution_event_deps(AsyncMock())
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="ask agent",
                targets=[
                    DelegateTarget(
                        agent_id="agent-1",
                        agent_name="Agent",
                        task="ask",
                    )
                ],
            )
        )
        se._dispatch_targets = AsyncMock(
            return_value=[
                StepResult(
                    step_number=1,
                    agent_id="agent-1",
                    agent_name="Agent",
                    task="ask",
                    response_text="",
                    success=False,
                    status=StepStatus.AWAITING_INPUT,
                    paused_message_id="agent-msg-1",
                    agent_message_id="agent-msg-1",
                    status_message="Need input",
                )
            ]
        )
        se._save_interrupted_state = AsyncMock(return_value=True)
        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(
            return_value=SimpleNamespace(request_id="hitl-1")
        )
        se.hitl_coordinator = hitl_mock
        user_message = SimpleNamespace(
            client_request_id="cr-1",
            extend_info={
                "orchestration_run_id": "run-msg-1",
                "orchestration_schema_version": 2,
            },
        )

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent")],
            room_config=RoomConfig(),
            user_message=user_message,
        )

        call_kwargs = hitl_mock.request_input.await_args.kwargs
        assert call_kwargs["orchestration_run_id"] == "run-msg-1"
        assert call_kwargs["orchestration_schema_version"] == 2

    @pytest.mark.asyncio
    async def test_supervisor_hitl_records_before_awaiting_input_send(self):
        se = _make_supervisor_executor()
        order: list[str] = []
        emit = AsyncMock(
            side_effect=lambda *a, **k: order.append("emit")
            if k.get("status") == "awaiting_input"
            else None
        )
        se.bind_execution_event_deps(emit)
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.CLARIFY,
                reasoning="need details",
                questions=[ClarifyQuestion(prompt="Which account?")],
            )
        )
        se.room_runtime.create_agent_message.return_value = SimpleNamespace(
            message_id="hitl-agent-msg"
        )
        se.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-1"
        )
        se.message_writer.add_room_agent_message = AsyncMock()
        se._save_interrupted_state = AsyncMock(return_value=True)
        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(
            return_value=SimpleNamespace(request_id="hitl-1")
        )

        se.hitl_coordinator = hitl_mock
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[],
            room_config=RoomConfig(),
        )

        assert result.status == RunStatus.AWAITING_INPUT
        assert order == ["emit"]
        se.delivery.send_processing_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_supervisor_hitl_receives_orchestration_run_link(self):
        se = _make_supervisor_executor()
        se.bind_execution_event_deps(AsyncMock())
        se.supervisor_service.decide_next = AsyncMock(
            return_value=SupervisorAction(
                action=ActionType.CLARIFY,
                reasoning="need details",
                questions=[ClarifyQuestion(prompt="Which account?")],
            )
        )
        se.room_runtime.create_agent_message.return_value = SimpleNamespace(
            message_id="hitl-agent-msg"
        )
        se.task_state_store.resolve_client_request_id_for_message_id = AsyncMock(
            return_value="cr-1"
        )
        se.message_writer.add_room_agent_message = AsyncMock()
        se._save_interrupted_state = AsyncMock(return_value=True)
        hitl_mock = AsyncMock()
        hitl_mock.request_input = AsyncMock(
            return_value=SimpleNamespace(request_id="hitl-1")
        )
        se.hitl_coordinator = hitl_mock
        user_message = SimpleNamespace(
            client_request_id="cr-1",
            extend_info={
                "orchestration_run_id": "run-msg-1",
                "orchestration_schema_version": 2,
            },
        )

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[],
            room_config=RoomConfig(),
            user_message=user_message,
        )

        call_kwargs = hitl_mock.request_input.await_args.kwargs
        assert call_kwargs["orchestration_run_id"] == "run-msg-1"
        assert call_kwargs["orchestration_schema_version"] == 2
