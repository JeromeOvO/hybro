from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.config.settings import Settings
from common.utils.time import utcnow
from execution.orchestration.action_validator import PlannerActionValidationError
from execution.orchestration.planner import RoomSupervisorPlannerAdapter
from execution.orchestration.resources import (
    OrchestrationResourceProvider,
    ResourcePayload,
    ResourceProjectionRef,
)
from execution.orchestration.result_ingestor import AgentResultRead
from execution.orchestration.room_message_center import RoomMessageCenter
from execution.orchestration.run_store import (
    InMemoryOrchestrationRunStore,
    OrchestrationStoreConflict,
)
from execution.orchestration.supervisor_executor import SupervisorExecutor
from models.agent import AgentStatus
from models.hitl import InterruptKind
from models.orchestration import (
    AgentOutputRecord,
    CompletionEvidence,
    DispatchContentRef,
    DispatchExpectedOutput,
    DispatchIntent,
    DispatchRefKind,
    OpenFailureRecord,
    OrchestrationEventType,
    OrchestrationRunState,
    OrchestrationStatus,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)
from models.processing import ProcessingResult, ProcessingStatus
from models.response import OrchestrationResponse
from models.room import MessageContent, Room, RoomUserMessage, UserAttachment
from models.supervisor import (
    ActionType,
    AgentProfile,
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


class RecordingPlanner:
    def __init__(self, *actions: PlannerAction) -> None:
        self._actions = list(actions)
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        if not self._actions:
            raise AssertionError("planner called more times than expected")
        return self._actions.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_room_id", "stored_user_message_id"),
    [
        ("room-2", "message-1"),
        ("room-1", "message-2"),
    ],
)
async def test_run_rejects_state_bound_to_another_request(
    stored_room_id,
    stored_user_message_id,
):
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="shared-run",
        room_id=stored_room_id,
        user_message_id=stored_user_message_id,
        envelope={},
        goal="Other request",
    )
    await store.create_run(state)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration_run_id": "shared-run",
            "candidate_agent_ids": ["agent-1"],
        },
    )
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    with pytest.raises(ValueError, match="orchestration run binding mismatch"):
        await executor.run(
            room_id="room-1",
            user_message_id="message-1",
            message_text="Coordinate this",
            agent_registry=[
                AgentProfile(agent_id="agent-1", agent_name="Agent One")
            ],
            room_config=RoomConfig(),
            user_message=user_message,
        )


@pytest.mark.asyncio
async def test_blocking_resume_logs_state_result_before_returning():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
    )
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope={},
        goal="Coordinate this",
    )
    state.status = OrchestrationStatus.AWAITING_USER
    state.pending_hitl_request_ids = ["hitl-1"]
    state.open_questions = [
        {
            "request_id": "hitl-1",
            "status": "open",
            "prompt": "Continue?",
            "source": "supervisor",
        }
    ]
    await store.create_run(state)
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor._resolve_v2_hitl_if_answered = AsyncMock(return_value=state)
    executor._sync_v2_resumed_trajectory = AsyncMock(
        return_value=(state, RunStatus.AWAITING_INPUT)
    )
    executor._log_state_and_return = AsyncMock(
        side_effect=lambda _room_id, _state, result: result
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[],
        room_config=RoomConfig(),
        resumed_trajectory=SupervisorTrajectory(),
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor._log_state_and_return.assert_awaited_once()


@pytest.mark.asyncio
async def test_loaded_debate_run_reconciles_missing_participant_snapshot():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Debate this"),
    )
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope={},
        goal="Debate this",
    )
    state.candidate_agent_ids = ["agent-1", "agent-2"]
    await store.create_run(state)
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.debate_rounds = 1

    loaded = await executor._load_or_create_run_state_for_run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Debate this",
        agent_registry=[
            AgentProfile(
                agent_id="agent-1",
                agent_name="Agent One",
                is_healthy=True,
            ),
            AgentProfile(
                agent_id="agent-2",
                agent_name="Agent Two",
                is_healthy=True,
            ),
        ],
        room_config=RoomConfig(is_debate_mode=True),
        user_message=user_message,
    )

    assert loaded.participant_snapshot is not None
    assert loaded.participant_snapshot.ordered_agent_ids == [
        "agent-1",
        "agent-2",
    ]
    assert loaded.step_budget >= 3
    assert loaded.state_version == state.state_version + 1


@pytest.mark.asyncio
async def test_run_builds_resource_catalog_before_planning():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(
            message_text="Use attachment",
            attachments=[
                UserAttachment(
                    file_id="file-1",
                    s3_key="uploads/room-1/file-1/submission.pdf",
                    mime_type="application/pdf",
                    file_name="submission.pdf",
                    size_bytes=128,
                )
            ],
        ),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "run-message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    provider = SimpleNamespace(list_resources=AsyncMock(return_value=[]))
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.FAIL,
            reasoning="cannot proceed",
            failure_reason="test stop",
        )
    )
    executor = _executor(
        store=InMemoryOrchestrationRunStore(),
        planner=planner,
        user_message=user_message,
    )
    executor.orchestration_resource_provider = provider

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Use attachment",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    provider.list_resources.assert_awaited_once()
    assert planner.contexts[0].available_resources == []


@pytest.mark.asyncio
async def test_run_materializes_only_selected_resource_refs_for_dispatch():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(
            message_text="Use the selected projection",
            attachments=[
                UserAttachment(
                    file_id="file-1",
                    s3_key="uploads/room-1/file-1/submission.pdf",
                    mime_type="application/pdf",
                    file_name="submission.pdf",
                    size_bytes=128,
                )
            ],
        ),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "run-message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Use the selected projection",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    task="Review the projection",
                    context_refs=[
                        {
                            "kind": "context",
                            "ref_id": "ctx:file-file-1:text",
                        }
                    ],
                )
            ],
        ),
        PlannerAction(
            action=PlannerActionType.COMPLETE,
            reasoning="Projection reviewed",
            completion_evidence=CompletionEvidence(
                satisfied_criteria=["The selected projection was reviewed."],
                final_answer_intent="Summarize the projection review.",
                confidence=1.0,
            ),
        ),
    )
    executor = _executor(
        store=InMemoryOrchestrationRunStore(),
        planner=planner,
        user_message=user_message,
    )
    projection_service = SimpleNamespace(
        ensure_projection=AsyncMock(
            return_value=(
                ResourceProjectionRef(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    source_ref_id="file:file-1",
                    mime_type="text/plain",
                    status="ready",
                    recommended_for_input_modes=["text"],
                ),
                ResourcePayload(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    mime_type="text/plain",
                    text="Projected submission text",
                ),
            )
        )
    )
    executor.orchestration_resource_provider = OrchestrationResourceProvider(
        projection_service=projection_service
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Use the selected projection",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    dispatched_message = (
        executor.agent_message_processor.process_single_message.await_args.args[0]
    )
    resolved = dispatched_message.extend_info["resolved_dispatch_payload_refs"]
    assert resolved["context_refs"] == ["ctx:file-file-1:text"]
    assert resolved["resource_payloads"][0]["text"] == (
        "Projected submission text"
    )
    projection_service.ensure_projection.assert_awaited_once()


def _agent_message(message_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        turn_id=None,
        client_request_id="client-1",
        extend_info={},
        message_content=SimpleNamespace(message_text="", message_task=None),
    )


def _state_unification_user_message(message_id: str, extend_info: dict | None = None):
    return SimpleNamespace(
        message_id=message_id,
        user_id="user-1",
        client_request_id="cr-1",
        extend_info=extend_info or {},
    )


def _run_state(**overrides):
    values = {
        "run_id": "run-1",
        "room_id": "room-1",
        "user_message_id": "msg-1",
        "goal": "Collect agent results",
        "candidate_agent_ids": ["agent-1", "agent-2"],
        "client_request_id": "cr-1",
    }
    values.update(overrides)
    return OrchestrationRunState(**values)


def _executor(
    *,
    store: InMemoryOrchestrationRunStore,
    planner: RecordingPlanner,
    user_message: RoomUserMessage,
) -> SupervisorExecutor:
    created_message_ids: list[str] = []

    def create_agent_message(**_kwargs):
        message_id = f"generated-{len(created_message_ids) + 1}"
        created_message_ids.append(message_id)
        return _agent_message(message_id)

    executor = SupervisorExecutor(
        supervisor_service=SimpleNamespace(),
        room_runtime=SimpleNamespace(
            create_agent_message=MagicMock(side_effect=create_agent_message)
        ),
        tsm=SimpleNamespace(),
        delivery=SimpleNamespace(
            send_task_submitted=AsyncMock(),
            send_task_update=AsyncMock(),
            send_agent_response=AsyncMock(),
        ),
        message_reader=SimpleNamespace(
            get_room_user_message_by_message_id=AsyncMock(return_value=user_message),
            get_room_agent_message_by_message_id=AsyncMock(return_value=None),
        ),
        message_writer=SimpleNamespace(
            add_room_agent_message=AsyncMock(),
            upsert_room_agent_message=AsyncMock(),
            delete_room_agent_message_by_message_id=AsyncMock(return_value=True),
            update_room_user_message_by_message_id=AsyncMock(return_value=True),
            update_room_agent_message_with_new_message_content_by_message_id=AsyncMock(),
        ),
        task_state_store=SimpleNamespace(
            resolve_client_request_id_for_message_id=AsyncMock(return_value="client-1")
        ),
        continuation_store=SimpleNamespace(),
        event_publisher=SimpleNamespace(emit_internal=AsyncMock()),
        rate_limit_service=None,
        agent_dispatcher=SimpleNamespace(
            resolve_agent=AsyncMock(
                return_value=SimpleNamespace(
                    agent_id="agent-1",
                    rate_limit_per_user_per_hour=100,
                    rate_limit_system_per_hour=1000,
                )
            )
        ),
        agent_message_processor=SimpleNamespace(
            process_single_message=AsyncMock(
                return_value=ProcessingResult(
                    ProcessingStatus.SUCCESS,
                    response_text="Agent One response",
                )
            )
        ),
        orchestration_run_store=store,
        orchestration_planner=planner,
    )
    executor.bind_execution_event_deps(AsyncMock())
    executor._stream_supervisor_synthesis = AsyncMock(return_value="Final summary")
    return executor


def _dispatch_refs_payload():
    return {
        "context_refs": [
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="ctx-1",
                source_agent_message_id="source-msg-1",
                mime_type="text/plain",
                required=False,
            )
        ],
        "artifact_refs": [
            DispatchContentRef(
                kind=DispatchRefKind.ARTIFACT,
                ref_id="artifact-1",
                source_agent_message_id="source-msg-2",
                mime_type="application/json",
                required=False,
            )
        ],
        "attachment_refs": [
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file-1",
                source_agent_message_id="user-msg-1",
                mime_type="application/pdf",
                required=False,
            )
        ],
        "expected_outputs": [
            DispatchExpectedOutput(
                kind="summary",
                required=True,
                description="Summarize the attached file",
            )
        ],
    }


def _explicit_dispatch_intent(
    run_id: str,
    step_number: int,
    target_index: int,
    target,
) -> DispatchIntent:
    return DispatchIntent(
        step_id=f"{run_id}:step-{step_number}",
        step_target_id=f"{run_id}:step-{step_number}:target-{target_index}",
        dispatch_intent_id=f"{run_id}:step-{step_number}:target-{target_index}:intent",
        planned_agent_message_id=f"agent-msg-{step_number}",
        agent_id=target.agent_id,
        task=target.task,
        task_hash=f"hash-{step_number}",
        context_refs=list(target.context_refs),
        artifact_refs=list(target.artifact_refs),
        attachment_refs=list(target.attachment_refs),
        expected_outputs=list(target.expected_outputs),
        attachment_policy=target.attachment_policy,
    )


@pytest.mark.asyncio
async def test_run_uses_sidecar_scope_planner_store_and_planned_message_ids():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Use the sidecar-selected agent",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Handle the request",
                )
            ],
        ),
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="Summarize the completed agent output",
            synthesis_instruction="Summarize the answer",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="@agent-2 should not widen candidate scope",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        conversation_context="Room background",
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert result.synthesis_text == "Final summary"
    assert [ctx.candidate_agent_ids for ctx in planner.contexts] == [
        ["agent-1"],
        ["agent-1"],
    ]
    assert planner.contexts[0].room_background == "Room background"

    state = await store.get_latest_by_user_message_id("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.COMPLETED
    assert state.candidate_agent_ids == ["agent-1"]
    assert state.summary_intent_id == "message-1:summary"
    assert state.summary_message_id == "sys-message-1"
    assert len(state.dispatch_intents) == 1

    intent = state.dispatch_intents[0]
    assert intent.agent_id == "agent-1"
    assert intent.planned_agent_message_id == "message-1:step-1:target-1:message"
    assert state.agent_outputs[0].agent_message_id == intent.planned_agent_message_id

    added_message_ids = [
        call.args[0].message_id
        for call in executor.message_writer.add_room_agent_message.await_args_list
    ]
    assert added_message_ids == ["sys-message-1", intent.planned_agent_message_id]
    assert "supervisor_trajectory" not in user_message.extend_info


@pytest.mark.asyncio
async def test_run_delegate_path_preserves_planner_dispatch_metadata():
    refs = _dispatch_refs_payload()
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Delegate with explicit refs",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Use the referenced materials",
                    context_refs=refs["context_refs"],
                    attachment_refs=refs["attachment_refs"],
                    expected_outputs=refs["expected_outputs"],
                    attachment_policy="compatible_only",
                )
            ],
        ),
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="Summarize the completed agent output",
            synthesis_instruction="Summarize the answer",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    state = await store.get_run("message-1")
    assert state is not None
    assert len(state.dispatch_intents) == 1
    intent = state.dispatch_intents[0]
    assert intent.context_refs == refs["context_refs"]
    assert intent.artifact_refs == []
    assert intent.attachment_refs == refs["attachment_refs"]
    assert intent.expected_outputs == refs["expected_outputs"]
    assert intent.attachment_policy == "compatible_only"

    delegated_message = executor.message_writer.add_room_agent_message.await_args_list[
        1
    ].args[0]
    assert delegated_message.message_id == intent.planned_agent_message_id
    assert (
        delegated_message.extend_info["attachment_forwarding_policy"]
        == "compatible_only"
    )
    assert delegated_message.extend_info["dispatch_payload_refs"] == {
        "context_refs": [
            ref.model_dump(mode="json") for ref in refs["context_refs"]
        ],
        "artifact_refs": [],
        "attachment_refs": [
            ref.model_dump(mode="json") for ref in refs["attachment_refs"]
        ],
        "expected_outputs": [
            output.model_dump(mode="json") for output in refs["expected_outputs"]
        ],
    }


def test_v2_dispatch_intent_preserves_dispatch_metadata():
    refs = _dispatch_refs_payload()

    intent = SupervisorExecutor._v2_dispatch_intent(
        run_id="run-1",
        step_number=2,
        target_index=3,
        target=DelegateTarget(
            agent_id="agent-1",
            agent_name="Agent One",
            task="Use the referenced materials",
            depends_on=["prior-intent"],
            parallel_group="fanout-1",
            required_resource_refs=["ctx:file-file-1:text"],
            context_refs=refs["context_refs"],
            artifact_refs=refs["artifact_refs"],
            attachment_refs=refs["attachment_refs"],
            expected_outputs=refs["expected_outputs"],
            attachment_policy="compatible_only",
        ),
    )

    assert intent.depends_on == ["prior-intent"]
    assert intent.parallel_group == "fanout-1"
    assert intent.required_resource_refs == ["ctx:file-file-1:text"]
    assert intent.context_refs == refs["context_refs"]
    assert intent.artifact_refs == refs["artifact_refs"]
    assert intent.attachment_refs == refs["attachment_refs"]
    assert intent.expected_outputs == refs["expected_outputs"]
    assert intent.attachment_policy == "compatible_only"


def test_v2_supervisor_action_preserves_dependency_metadata():
    planner_action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Run independent checks.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Check section A.",
                parallel_group="fanout-1",
                required_resource_refs=["artifact-1"],
            )
        ],
    )

    action = SupervisorExecutor._v2_supervisor_action(
        planner_action,
        [AgentProfile(agent_id="agent-1", agent_name="Agent One")],
    )
    target = action.targets[0]

    assert target.depends_on == []
    assert target.parallel_group == "fanout-1"
    assert target.required_resource_refs == ["artifact-1"]


@pytest.mark.asyncio
async def test_run_allows_final_synthesis_after_step_budget_is_consumed():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Use one budgeted agent step",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Handle the request",
                )
            ],
        ),
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="Summarize after budget is consumed",
            synthesis_instruction="Summarize the answer",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.MAX_STEPS = 1

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert result.synthesis_text == "Final summary"
    assert len(planner.contexts) == 2
    assert planner.contexts[1].state_context.current_step.steps_used == 1


@pytest.mark.asyncio
async def test_run_replans_after_invalid_complete_action():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Collect evidence",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Handle the request",
                )
            ],
        ),
        PlannerAction(
            action=PlannerActionType.COMPLETE,
            reasoning="Done without structured evidence",
        ),
        PlannerAction(
            action=PlannerActionType.FAIL,
            reasoning="Stop after the invalid completion was rejected.",
            failure_reason="test stop",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    state = await store.get_latest_by_user_message_id("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.FAILED
    assert state.terminal_reason == "test stop"
    assert state.open_failures[0].error_code == "completion_evidence_invalid"
    assert state.open_failures[0].status == "resolved"


@pytest.mark.asyncio
async def test_run_replans_after_recoverable_agent_failure_before_complete():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="user-msg-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Underwrite this submission"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "user-msg-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    failed_action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Try insurer with raw attachment.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Underwrite the submission.",
                artifact_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ARTIFACT,
                        ref_id="broker-msg:artifact_id:submission",
                    )
                ],
                attachment_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ATTACHMENT,
                        ref_id="file-1",
                    )
                ],
            )
        ],
    )
    retry_action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Retry insurer using broker artifact only.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Underwrite using the broker artifact.",
                artifact_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ARTIFACT,
                        ref_id="broker-msg:artifact_id:submission",
                    )
                ],
            )
        ],
    )
    complete_action = PlannerAction(
        action=PlannerActionType.COMPLETE,
        reasoning="Recovered answer satisfies the goal.",
        completion_evidence=CompletionEvidence(
            satisfied_criteria=["underwriting_answer_collected"],
            referenced_fact_ids=["agent-msg-2:text"],
            referenced_artifact_keys=[],
            unresolved_questions=[],
            final_answer_intent="answer_user",
            confidence=0.8,
        ),
    )
    planner = SimpleNamespace(
        plan=AsyncMock(side_effect=[failed_action, retry_action, complete_action])
    )
    store = InMemoryOrchestrationRunStore()
    await store.create_run(
        _run_state(
            run_id="user-msg-1",
            user_message_id="user-msg-1",
            goal="Underwrite this submission",
            candidate_agent_ids=["agent-1"],
            artifacts=[
                {
                    "artifact_key": "broker-msg:artifact_id:submission",
                    "artifact_id": "submission",
                    "source_agent_message_id": "broker-msg",
                    "source_agent_id": "agent-broker",
                    "summary": "Broker artifact",
                }
            ],
        )
    )
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor._v2_dispatch_intent = MagicMock(side_effect=_explicit_dispatch_intent)
    executor._dispatch_targets = AsyncMock(
        side_effect=[
            [
                StepResult(
                    step_number=1,
                    agent_id="agent-1",
                    agent_name="agent-1",
                    task="Underwrite the submission.",
                    response_text="",
                    success=False,
                    status=StepStatus.FAILED,
                    error_message=(
                        "Agent does not accept the uploaded file type for: "
                        "report.pdf (application/pdf)."
                    ),
                    agent_message_id="agent-msg-1",
                    status_message="agent_does_not_accept_file_type",
                )
            ],
            [
                StepResult(
                    step_number=2,
                    agent_id="agent-1",
                    agent_name="agent-1",
                    task="Underwrite using the broker artifact.",
                    response_text="Recovered answer.",
                    success=True,
                    status=StepStatus.SUCCESS,
                    agent_message_id="agent-msg-2",
                )
            ],
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="user-msg-1",
        message_text="Underwrite this submission",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Insurer")],
        room_config=RoomConfig(),
        conversation_context=None,
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert planner.plan.await_count == 3


@pytest.mark.asyncio
async def test_run_stops_when_repeated_recoverable_failure_exhausts_step_budget():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="user-msg-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Underwrite this submission"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "user-msg-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
            "orchestration_step_budget": 1,
        },
    )
    delegate_action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Try the same failing dispatch.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Underwrite the submission.",
                attachment_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ATTACHMENT,
                        ref_id="file-1",
                    )
                ],
            )
        ],
    )
    planner = SimpleNamespace(plan=AsyncMock(return_value=delegate_action))
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor._v2_dispatch_intent = MagicMock(side_effect=_explicit_dispatch_intent)
    executor._dispatch_targets = AsyncMock(
        return_value=[
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="Insurer",
                task="Underwrite the submission.",
                response_text="",
                success=False,
                status=StepStatus.FAILED,
                error_message=(
                    "Agent does not accept the uploaded file type for: "
                    "report.pdf (application/pdf)."
                ),
                agent_message_id="agent-msg-1",
                status_message="agent_does_not_accept_file_type",
            )
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="user-msg-1",
        message_text="Underwrite this submission",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Insurer")],
        room_config=RoomConfig(),
        conversation_context=None,
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    assert executor._dispatch_targets.await_count == 1
    assert planner.plan.await_count <= 2


@pytest.mark.asyncio
async def test_run_stops_when_recoverable_failure_retry_budget_is_exhausted():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="user-msg-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Underwrite this submission"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "user-msg-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
            "orchestration_step_budget": 6,
        },
    )
    delegate_action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Retry the same recoverable dispatch.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Underwrite the submission.",
                attachment_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ATTACHMENT,
                        ref_id="file-1",
                    )
                ],
            )
        ],
    )
    planner = SimpleNamespace(plan=AsyncMock(return_value=delegate_action))
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor._v2_dispatch_intent = MagicMock(side_effect=_explicit_dispatch_intent)
    executor._dispatch_targets = AsyncMock(
        return_value=[
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="Insurer",
                task="Underwrite the submission.",
                response_text="",
                success=False,
                status=StepStatus.FAILED,
                error_message=(
                    "Agent does not accept the uploaded file type for: "
                    "report.pdf (application/pdf)."
                ),
                agent_message_id=None,
                status_message="agent_does_not_accept_file_type",
            )
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="user-msg-1",
        message_text="Underwrite this submission",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Insurer")],
        room_config=RoomConfig(),
        conversation_context=None,
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    assert executor._dispatch_targets.await_count == 3
    state = await store.get_latest_by_user_message_id("user-msg-1")
    assert state is not None
    assert len(state.open_failures) == 1
    assert state.open_failures[0].retry_count == state.open_failures[0].max_retries
    assert state.open_failures[0].status == "abandoned"


def test_exhausted_recoverable_failure_for_intents_does_not_block_different_error_class():
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Underwrite submission",
                task_hash="hash-shared",
                artifact_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ARTIFACT,
                        ref_id="artifact-1",
                    )
                ],
                attachment_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ATTACHMENT,
                        ref_id="file-1",
                    )
                ],
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Retry underwrite with broker artifact only",
                task_hash="hash-retry",
                artifact_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.ARTIFACT,
                        ref_id="artifact-1",
                    )
                ],
            ),
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-a",
                fingerprint="failure-a",
                source="a2a_adapter",
                agent_id="agent-1",
                agent_message_id="agent-msg-1",
                dispatch_intent_id="intent-1",
                error_code="agent_does_not_accept_file_type",
                error_message="file type rejected",
                recoverable=True,
                retry_count=2,
                max_retries=2,
                status="abandoned",
            ),
            OpenFailureRecord(
                failure_id="failure-b",
                fingerprint="failure-b",
                source="a2a_adapter",
                agent_id="agent-1",
                agent_message_id="agent-msg-2",
                dispatch_intent_id="intent-2",
                error_code="timeout",
                error_message="agent timed out",
                recoverable=True,
                retry_count=0,
                max_retries=2,
                status="open",
            ),
        ],
    )
    retry_intent = DispatchIntent(
        step_id="step-3",
        step_target_id="step-3:target-1",
        dispatch_intent_id="intent-3",
        planned_agent_message_id="agent-msg-3",
        agent_id="agent-1",
        task="Retry underwrite with broker artifact only",
        task_hash="hash-shared",
        artifact_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ARTIFACT,
                ref_id="artifact-1",
            )
        ],
    )

    blocking_failure = SupervisorExecutor._exhausted_recoverable_failure_for_intents(
        state,
        [retry_intent],
    )

    assert blocking_failure is None


@pytest.mark.asyncio
async def test_push_resume_ingests_paused_result_and_persists_outcome_before_planning():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    first_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="dispatch async agent",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Handle the request",
                )
            ],
        )
    )
    first_executor = _executor(
        store=store,
        planner=first_planner,
        user_message=user_message,
    )
    first_executor.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.PAUSED,
            message_id="message-1:step-1:target-1:message",
        )
    )
    first_executor._save_interrupted_state = AsyncMock(return_value=True)

    paused = await first_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        user_message=user_message,
    )
    assert paused.status == RunStatus.PAUSED

    resumed_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resumed delegate",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Handle the request",
                        )
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Handle the request",
                        response_text="Webhook result",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-1:message",
                    )
                ],
                started_at=utcnow(),
            )
        ]
    )
    second_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="summarize after webhook",
            synthesis_instruction="Summarize",
        )
    )
    second_executor = _executor(
        store=store,
        planner=second_planner,
        user_message=user_message,
    )

    result = await second_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    context = second_planner.contexts[0]
    assert context.state_context.current_step.steps_used == 1
    assert context.state_context.agent_outputs[0]["text"] == "Webhook result"
    state = await store.get_run("message-1")
    assert state is not None
    assert state.agent_outputs[0].text == "Webhook result"
    assert state.dispatch_intents[0].status == StepStatus.SUCCESS.value
    assert len(state.delegation_outcomes) == 2
    assert [event.type for event in store._events_by_run["message-1"]].count(
        OrchestrationEventType.OUTCOME_EVALUATED
    ) == 2


@pytest.mark.asyncio
async def test_agent_outputs_are_ingested_with_single_state_writer(monkeypatch):
    writes: list[int] = []
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = await store.create_run(
        _run_state(
            run_id="run-1",
            user_message_id="msg-1",
            candidate_agent_ids=["agent-1", "agent-2"],
        )
    )

    original_save = store.save_state

    async def save_state_spy(
        next_state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        writes.append(expected_version)
        return await original_save(next_state, expected_version=expected_version)

    monkeypatch.setattr(store, "save_state", save_state_spy)

    updated = await executor._ingest_agent_results_serially(
        state,
        [
            AgentResultRead(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                text="one",
            ),
            AgentResultRead(
                agent_message_id="agent-msg-2",
                agent_id="agent-2",
                status="completed",
                text="two",
            ),
        ],
    )

    assert writes == [0, 1]
    assert updated.state_version == 2
    assert [output.agent_message_id for output in updated.agent_outputs] == [
        "agent-msg-1",
        "agent-msg-2",
    ]


@pytest.mark.asyncio
async def test_ingest_agent_results_serially_ignores_event_append_failures(monkeypatch):
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = await store.create_run(
        _run_state(
            run_id="run-1",
            user_message_id="msg-1",
            candidate_agent_ids=["agent-1"],
        )
    )

    monkeypatch.setattr(store, "append_event", AsyncMock(side_effect=RuntimeError("oops")))

    updated = await executor._ingest_agent_results_serially(
        state,
        [
            AgentResultRead(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                text="result one",
            )
        ],
    )

    assert updated.state_version == 1
    persisted = await store.get_run("run-1")
    assert persisted is not None
    assert persisted.state_version == 1
    assert persisted.agent_outputs[0].status == "completed"


@pytest.mark.asyncio
async def test_v2_results_are_ingested_with_state_writes_per_result(monkeypatch):
    writes: list[int] = []
    event_versions: list[int] = []
    event_payloads: list[str] = []

    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        run_id="run-1",
        user_message_id="msg-1",
        candidate_agent_ids=["agent-1", "agent-2"],
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="one",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="two",
            ),
        ),
    ]
    await store.create_run(state)

    original_save = store.save_state

    async def save_state_spy(
        next_state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        writes.append(expected_version)
        return await original_save(next_state, expected_version=expected_version)

    original_append_event = store.append_event

    async def append_event_spy(event):
        if event.type == OrchestrationEventType.AGENT_RESULT_INGESTED:
            event_versions.append(event.state_version)
            event_payloads.append(event.payload["agent_message_id"])
        return await original_append_event(event)

    monkeypatch.setattr(store, "save_state", save_state_spy)
    monkeypatch.setattr(store, "append_event", append_event_spy)

    updated = await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="Agent One",
                task="one",
                response_text="one",
                success=True,
                status=StepStatus.SUCCESS,
                agent_message_id="run-1:step-1:target-1:message",
            ),
            StepResult(
                step_number=1,
                agent_id="agent-2",
                agent_name="Agent Two",
                task="two",
                response_text="two",
                success=False,
                status=StepStatus.FAILED,
                error_message="agent two failed",
                agent_message_id="run-1:step-1:target-2:message",
            ),
        ],
        status=OrchestrationStatus.RUNNING,
        advance_step=True,
    )

    assert writes == [0, 1]
    assert event_versions == [1, 2]
    assert event_payloads == [
        "run-1:step-1:target-1:message",
        "run-1:step-1:target-2:message",
    ]
    assert updated.state_version == 2
    assert updated.status == OrchestrationStatus.RUNNING
    assert updated.steps_used == 1
    assert updated.dispatch_intents[0].status == StepStatus.SUCCESS.value
    assert updated.dispatch_intents[1].status == StepStatus.FAILED.value
    outputs_by_id = {output.agent_message_id: output for output in updated.agent_outputs}
    assert outputs_by_id["run-1:step-1:target-1:message"].status == (
        "completed"
    )
    assert outputs_by_id["run-1:step-1:target-2:message"].status == "failed"


@pytest.mark.asyncio
async def test_v2_result_without_message_id_updates_only_one_fallback_intent():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        run_id="run-1",
        user_message_id="msg-1",
        candidate_agent_ids=["agent-1"],
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="same",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="same",
            ),
        ),
    ]
    await store.create_run(state)

    updated = await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="Agent One",
                task="same",
                response_text="",
                success=False,
                status=StepStatus.FAILED,
            )
        ],
        status=OrchestrationStatus.RUNNING,
        advance_step=True,
    )

    assert updated.state_version == 1
    assert updated.dispatch_intents[0].status == StepStatus.FAILED.value
    assert updated.dispatch_intents[1].status == "planned"


@pytest.mark.asyncio
async def test_v2_result_without_message_id_updates_fallback_intents_in_order():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        run_id="run-1",
        user_message_id="msg-1",
        candidate_agent_ids=["agent-1"],
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="same",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="same",
            ),
        ),
    ]
    await store.create_run(state)

    updated = await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="Agent One",
                task="same",
                response_text="first result",
                success=True,
                status=StepStatus.SUCCESS,
            ),
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="Agent One",
                task="same",
                response_text="second result",
                success=False,
                status=StepStatus.FAILED,
                error_message="second failure",
            ),
        ],
        status=OrchestrationStatus.RUNNING,
        advance_step=True,
    )

    assert updated.dispatch_intents[0].status == StepStatus.SUCCESS.value
    assert updated.dispatch_intents[1].status == StepStatus.FAILED.value
    assert updated.state_version == 2
    output_messages = [
        output.agent_message_id for output in updated.agent_outputs
    ]
    assert output_messages == [
        "run-1:step-1:target-1:message",
        "run-1:step-1:target-2:message",
    ]


@pytest.mark.asyncio
async def test_run_awaiting_input_status_is_not_persisted_without_hitl_request_ids(monkeypatch):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="both need user input",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Auth required",
                    parallel_group="fanout-1",
                ),
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="More context",
                    parallel_group="fanout-1",
                ),
            ],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_message_processor.process_single_message = AsyncMock(
        side_effect=[
            ProcessingResult(
                ProcessingStatus.AWAITING_INPUT,
                message_id="message-1:step-1:target-1:message",
                status_message="auth_required",
            ),
            ProcessingResult(
                ProcessingStatus.AWAITING_INPUT,
                message_id="message-1:step-1:target-2:message",
                status_message="Need approval.",
            ),
        ]
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    saved_states = []
    original_save = store.save_state

    async def save_state_spy(
        next_state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        saved_states.append(
            (
                next_state.status,
                list(next_state.pending_hitl_request_ids),
                next_state.state_version,
            )
        )
        return await original_save(next_state, expected_version=expected_version)

    monkeypatch.setattr(store, "save_state", save_state_spy)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    assert not any(
        status == OrchestrationStatus.AWAITING_USER and not pending
        for status, pending, _version in saved_states
    )
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.AWAITING_USER
    assert state.pending_hitl_request_ids == ["hitl-agent-1"]


@pytest.mark.asyncio
async def test_run_ask_user_creates_hitl_prompt_and_continuation():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    )

    async def save_interrupted_state(**_kwargs):
        creating_state = await store.get_run("message-1")
        assert creating_state is not None
        assert creating_state.status == OrchestrationStatus.INGESTING
        assert creating_state.pending_hitl_request_ids == ["hitl-1"]
        assert creating_state.open_questions[0]["status"] == "creating"
        return True

    executor._save_interrupted_state = AsyncMock(
        side_effect=save_interrupted_state
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor.hitl_coordinator.request_input.assert_awaited_once()
    hitl_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert hitl_kwargs["source"] == "supervisor"
    assert hitl_kwargs["prompt"] == "Which account?"
    assert hitl_kwargs["orchestration_run_id"] == "message-1"
    executor._save_interrupted_state.assert_awaited_once()
    assert executor._save_interrupted_state.await_args.kwargs["kind"].value == (
        "hitl_supervisor"
    )
    state = await store.get_run("message-1")
    assert state is not None
    assert state.steps_used == 1


@pytest.mark.asyncio
async def test_run_ask_user_marks_sidecar_recoverable_before_hitl_request():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor._save_interrupted_state = AsyncMock(return_value=True)

    async def request_input(**_kwargs):
        state = await store.get_run("message-1")
        assert state is not None
        return SimpleNamespace(request_id="hitl-1")

    executor.hitl_coordinator = SimpleNamespace(request_input=AsyncMock(side_effect=request_input))

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    state = await store.get_run("message-1")
    assert state is not None
    assert state.steps_used == 1
    assert state.pending_hitl_request_ids == ["hitl-1"]


@pytest.mark.asyncio
async def test_run_resumes_ingesting_hitl_without_replanning_or_duplicates():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "run-message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="need user choice",
        questions=[{"prompt": "Which account?", "prompt_type": "text"}],
    )
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="run-message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope=user_message.extend_info,
        goal="Coordinate this",
    )
    state.status = OrchestrationStatus.INGESTING
    state.steps_used = 1
    state.pending_hitl_request_ids = [
        "run-message-1:step-1:supervisor-hitl-1"
    ]
    state.decision_log = [
        {
            "action": PlannerActionType.ASK_USER.value,
            "reasoning": action.reasoning,
            "targets": [],
            "planner_action": action.model_dump(mode="json"),
        }
    ]
    await store.create_run(state)
    planner = RecordingPlanner()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(
            return_value=SimpleNamespace(
                request_id="run-message-1:step-1:supervisor-hitl-1"
            )
        )
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    assert planner.contexts == []
    executor.hitl_coordinator.request_input.assert_awaited_once()
    request_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert request_kwargs["request_id"] == (
        "run-message-1:step-1:supervisor-hitl-1"
    )
    upserted = executor.message_writer.upsert_room_agent_message.await_args.args[0]
    assert upserted.message_id == (
        "run-message-1:step-1:supervisor-hitl-1:message"
    )
    state = await store.get_run("run-message-1")
    assert state is not None
    assert state.steps_used == 1
    assert state.status == OrchestrationStatus.AWAITING_USER


@pytest.mark.asyncio
async def test_run_fails_corrupt_ingesting_hitl_checkpoint():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "run-message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="run-message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope=user_message.extend_info,
        goal="Coordinate this",
    )
    state.status = OrchestrationStatus.INGESTING
    state.steps_used = 1
    state.pending_hitl_request_ids = [
        "run-message-1:step-1:supervisor-hitl-1"
    ]
    state.decision_log = [
        {
            "action": PlannerActionType.ASK_USER.value,
            "reasoning": "missing serialized planner action",
            "targets": [],
        }
    ]
    await store.create_run(state)
    planner = RecordingPlanner()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    assert result.trajectory is None
    assert result.run_state is not None
    assert result.run_state.status == OrchestrationStatus.FAILED
    assert planner.contexts == []
    state = await store.get_run("run-message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.FAILED
    assert "no valid ASK_USER planner action" in (state.terminal_reason or "")


@pytest.mark.asyncio
async def test_resolve_v2_hitl_if_answered_clears_pending_without_leaving_awaiting_user():
    store = InMemoryOrchestrationRunStore()
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.AWAITING_USER,
        candidate_agent_ids=["agent-1"],
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Auth required",
            ),
        )
    ]
    state.pending_hitl_request_ids = ["open-hitl-1"]
    state.open_questions = [
        {
            "request_id": "open-hitl-1",
            "status": "open",
            "prompt": "Authenticate?",
            "source": "agent",
        }
    ]
    await store.create_run(state)

    resolved = await executor._resolve_v2_hitl_if_answered(
        state,
        resumed_trajectory=SupervisorTrajectory(hitl_user_reply="approved"),
    )

    assert resolved.status != OrchestrationStatus.AWAITING_USER
    assert resolved.pending_hitl_request_ids == []
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status != OrchestrationStatus.AWAITING_USER
    assert persisted.pending_hitl_request_ids == []
    assert persisted.open_questions
    assert persisted.open_questions[0]["status"] == "resolved"


@pytest.mark.asyncio
async def test_resolve_v2_hitl_if_answered_does_not_revive_terminal_cleanup_state():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message("message-1"),
    )
    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        status=OrchestrationStatus.FAILED,
    )
    state.pending_hitl_request_ids = ["hitl-1"]
    state.open_questions = [
        {
            "request_id": "hitl-1",
            "source": "supervisor",
            "status": "cleanup_failed",
        }
    ]

    resolved = await executor._resolve_v2_hitl_if_answered(
        state,
        resumed_trajectory=SupervisorTrajectory(hitl_user_reply="late answer"),
    )

    assert resolved is state
    assert resolved.status == OrchestrationStatus.FAILED
    assert resolved.pending_hitl_request_ids == ["hitl-1"]
    assert resolved.open_questions[0]["status"] == "cleanup_failed"


@pytest.mark.asyncio
async def test_resolve_v2_hitl_if_answered_does_not_overresolve_ambiguous_questions():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message("message-1"),
    )
    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        status=OrchestrationStatus.AWAITING_USER,
    )
    state.open_questions = [
        {
            "source": "supervisor",
            "step": 1,
            "prompt": "First?",
            "status": "open",
        },
        {
            "source": "supervisor",
            "step": 1,
            "prompt": "Second?",
            "status": "open",
        },
    ]

    resolved = await executor._resolve_v2_hitl_if_answered(
        state,
        resumed_trajectory=SupervisorTrajectory(hitl_user_reply="ambiguous"),
    )

    assert resolved is state
    assert [question["status"] for question in resolved.open_questions] == [
        "open",
        "open",
    ]


def test_clear_stale_pending_hitl_retains_recoverable_cleanup_refs():
    executor = _executor(
        store=InMemoryOrchestrationRunStore(),
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message("message-1"),
    )
    state = _run_state(run_id="message-1", user_message_id="message-1")
    state.pending_hitl_request_ids = ["creating", "cleanup", "resolved"]
    state.open_questions = [
        {"request_id": "creating", "status": "creating"},
        {"request_id": "cleanup", "status": "cleanup_failed"},
        {"request_id": "resolved", "status": "resolved"},
    ]

    executor._clear_stale_pending_hitl_request_ids(state)

    assert state.pending_hitl_request_ids == ["creating", "cleanup"]


@pytest.mark.asyncio
async def test_resolve_v2_hitl_if_answered_clears_only_addressed_agent_request_id():
    store = InMemoryOrchestrationRunStore()
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="approved"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "hitl_user_reply": "approved",
            "hitl_request_id": "hitl-agent-1",
        },
    )
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.AWAITING_USER,
        candidate_agent_ids=["agent-1", "agent-2"],
    )
    state.pending_hitl_request_ids = ["hitl-agent-1", "hitl-agent-2"]
    state.open_questions = [
        {
            "request_id": "hitl-agent-1",
            "status": "open",
            "prompt": "First approval?",
            "source": "agent",
        },
        {
            "request_id": "hitl-agent-2",
            "status": "open",
            "prompt": "Second approval?",
            "source": "agent",
        },
    ]
    await store.create_run(state)

    resolved = await executor._resolve_v2_hitl_if_answered(
        state,
        user_message=user_message,
    )

    assert resolved.status == OrchestrationStatus.AWAITING_USER
    assert resolved.pending_hitl_request_ids == ["hitl-agent-2"]
    statuses = {
        question["request_id"]: question["status"]
        for question in resolved.open_questions
    }
    assert statuses == {
        "hitl-agent-1": "resolved",
        "hitl-agent-2": "open",
    }


@pytest.mark.asyncio
async def test_run_records_supervisor_hitl_reply_from_resumed_trajectory_without_pending_state():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="answer was provided",
            synthesis_instruction="Use the clarified account",
        ),
        PlannerAction(
            action=PlannerActionType.FAIL,
            reasoning="Stop after the invalid synthesis was rejected.",
            failure_reason="test stop",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.RUNNING,
        candidate_agent_ids=["agent-1"],
    )
    await store.create_run(state)
    resumed_trajectory = SupervisorTrajectory(
        hitl_user_reply="Use the enterprise account",
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.CLARIFY,
                    reasoning="need user choice",
                    clarification_question="Which account?",
                ),
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ],
    )

    await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    persisted = await store.get_run("message-1")
    assert persisted is not None
    hitl_facts = [
        fact for fact in persisted.facts if fact.get("source") == "hitl_user_reply"
    ]
    assert hitl_facts
    assert hitl_facts[0]["text"] == "Use the enterprise account"
    assert persisted.open_questions
    assert persisted.open_questions[0]["status"] == "resolved"
    assert persisted.open_questions[0]["prompt"] == "Which account?"
    assert planner.contexts
    assert planner.contexts[0].state_context.facts[0]["text"] == (
        "Use the enterprise account"
    )


@pytest.mark.asyncio
async def test_run_ask_user_cleanup_on_final_state_save_failure(monkeypatch):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    request_input = AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    cancel_request = AsyncMock()
    executor.hitl_coordinator = SimpleNamespace(
        request_input=request_input,
        cancel_request=cancel_request,
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)
    executor.message_writer.delete_room_agent_message_by_message_id = AsyncMock()
    executor.continuation_store.get_and_clear_continuation_on_user_message = AsyncMock()

    original_save = store.save_state

    async def save_state_spy(
        next_state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        if (
            next_state.status == OrchestrationStatus.AWAITING_USER
            and next_state.pending_hitl_request_ids == ["hitl-1"]
        ):
            raise RuntimeError("failed to persist final ask user state")
        return await original_save(next_state, expected_version=expected_version)

    monkeypatch.setattr(store, "save_state", save_state_spy)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    request_input.assert_awaited_once()
    cancel_request.assert_awaited_once_with("hitl-1", "room-1")
    executor.message_writer.delete_room_agent_message_by_message_id.assert_awaited_once()
    executor.continuation_store.get_and_clear_continuation_on_user_message.assert_awaited_once_with(
        "message-1"
    )
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert persisted.pending_hitl_request_ids == []
    assert not persisted.open_questions


@pytest.mark.asyncio
async def test_run_ask_user_records_creating_question_before_request_input():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor._save_interrupted_state = AsyncMock(return_value=True)

    async def request_input(**_kwargs):
        state = await store.get_run("message-1")
        assert state is not None
        assert state.status == OrchestrationStatus.AWAITING_USER
        assert any(
            question.get("source") == "supervisor"
            and question.get("status") == "creating"
            and question.get("prompt") == "Which account?"
            and question.get("display_message_id")
            and "request_id" not in question
            for question in state.open_questions
        )
        return SimpleNamespace(request_id="hitl-1")

    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(side_effect=request_input)
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.pending_hitl_request_ids == ["hitl-1"]
    assert any(
        question.get("request_id") == "hitl-1"
        and question.get("status") == "open"
        for question in persisted.open_questions
    )


@pytest.mark.asyncio
async def test_run_ask_user_preserves_request_reference_when_final_state_cleanup_cancel_fails(
    monkeypatch,
):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    request_input = AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    cancel_request = AsyncMock(side_effect=RuntimeError("cancel failed"))
    executor.hitl_coordinator = SimpleNamespace(
        request_input=request_input,
        cancel_request=cancel_request,
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)
    executor.message_writer.delete_room_agent_message_by_message_id = AsyncMock()
    executor.continuation_store.get_and_clear_continuation_on_user_message = AsyncMock()

    original_save = store.save_state

    async def save_state_spy(
        next_state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        if (
            next_state.status == OrchestrationStatus.AWAITING_USER
            and next_state.pending_hitl_request_ids == ["hitl-1"]
        ):
            raise RuntimeError("failed to persist final ask user state")
        return await original_save(next_state, expected_version=expected_version)

    monkeypatch.setattr(store, "save_state", save_state_spy)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    cancel_request.assert_awaited_once_with("hitl-1", "room-1")
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert "hitl-1" in persisted.pending_hitl_request_ids
    cleanup_questions = [
        question
        for question in persisted.open_questions
        if question.get("request_id") == "hitl-1"
    ]
    assert cleanup_questions
    assert cleanup_questions[0]["status"] == "cleanup_failed"


@pytest.mark.asyncio
async def test_run_ask_user_request_input_exception_triggers_cleanup_and_failure(monkeypatch):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    input_error = RuntimeError("request input failed")
    input_error.request_id = "orphaned-hitl"
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(side_effect=input_error),
        cancel_request=AsyncMock(),
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)
    executor.message_writer.delete_room_agent_message_by_message_id = AsyncMock()

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    executor.hitl_coordinator.request_input.assert_awaited_once()
    executor.hitl_coordinator.cancel_request.assert_awaited_once_with(
        "orphaned-hitl",
        "room-1",
    )
    executor.message_writer.delete_room_agent_message_by_message_id.assert_awaited_once()
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert persisted.pending_hitl_request_ids == []


@pytest.mark.asyncio
async def test_run_ask_user_message_creation_failure_clears_synthetic_pending_state():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)
    executor.message_writer.upsert_room_agent_message = AsyncMock(
        side_effect=RuntimeError("failed to persist clarifier message")
    )
    executor.message_writer.delete_room_agent_message_by_message_id = AsyncMock()

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    executor.hitl_coordinator.request_input.assert_not_awaited()
    failed_message_id = (
        executor.message_writer.upsert_room_agent_message.await_args_list[-1]
        .args[0]
        .message_id
    )
    executor.message_writer.delete_room_agent_message_by_message_id.assert_awaited_once_with(
        failed_message_id
    )
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert persisted.pending_hitl_request_ids == []
    assert persisted.open_questions == []


@pytest.mark.asyncio
async def test_run_ask_user_records_failed_message_cleanup_without_pending_request():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor._save_interrupted_state = AsyncMock(return_value=True)
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(side_effect=RuntimeError("request failed")),
    )
    executor.message_writer.delete_room_agent_message_by_message_id = AsyncMock(
        return_value=False
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.pending_hitl_request_ids == []
    cleanup_question = next(
        question
        for question in persisted.open_questions
        if question.get("request_id")
        == "message-1:step-1:supervisor-hitl-1"
    )
    assert cleanup_question["status"] == "cleanup_failed"
    assert cleanup_question["cleanup_failed_message_ids"] == [
        "message-1:step-1:supervisor-hitl-1:message"
    ]
    assert any(
        fact.get("source") == "hitl_cleanup_failed"
        and "message-1:step-1:supervisor-hitl-1:message"
        in fact.get("message_ids", [])
        for fact in persisted.facts
    )


@pytest.mark.asyncio
async def test_run_ask_user_message_creation_failure_preserves_failed_delete_message_id():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)
    executor.message_writer.upsert_room_agent_message = AsyncMock(
        side_effect=RuntimeError("failed to persist clarifier message")
    )
    executor.message_writer.delete_room_agent_message_by_message_id = AsyncMock(
        return_value=False
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    failed_message_id = (
        executor.message_writer.upsert_room_agent_message.await_args_list[-1]
        .args[0]
        .message_id
    )
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert any(
        question.get("status") == "cleanup_failed"
        and question.get("display_message_id") == failed_message_id
        for question in persisted.open_questions
    )
    assert any(
        fact.get("source") == "hitl_cleanup_failed"
        and failed_message_id in fact.get("message_ids", [])
        for fact in persisted.facts
    )


@pytest.mark.asyncio
async def test_run_pending_hitl_without_reply_returns_awaiting_without_planning():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope=user_message.extend_info,
        goal="Coordinate this",
    )
    state.status = OrchestrationStatus.AWAITING_USER
    state.pending_hitl_request_ids = ["hitl-1"]
    await store.create_run(state)
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="should not plan before user reply",
            synthesis_instruction="Summarize",
        )
    )
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    assert planner.contexts == []
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.FAILED
    assert state.terminal_reason == "awaiting_user_without_open_hitl"
    assert state.pending_hitl_request_ids == ["hitl-1"]


@pytest.mark.asyncio
async def test_run_stale_awaiting_user_without_open_hitl_fails():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner()
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.AWAITING_USER,
        candidate_agent_ids=["agent-1"],
    )
    state.pending_hitl_request_ids = ["stale-hitl-1"]
    state.open_questions = [
        {"request_id": "stale-hitl-1", "status": "resolved", "prompt": "old question"}
    ]
    await store.create_run(state)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert persisted.terminal_reason == "awaiting_user_without_open_hitl"


@pytest.mark.asyncio
async def test_run_stale_awaiting_user_pending_hitl_does_not_block_recovering_awaiting_input():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="should not plan",
            synthesis_instruction="ignored",
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.AWAITING_USER,
        candidate_agent_ids=["agent-1"],
    )
    state.pending_hitl_request_ids = ["resolved-hitl-1"]
    state.open_questions = [
        {
            "request_id": "resolved-hitl-1",
            "status": "resolved",
            "prompt": "already answered",
            "answer": "ok",
        }
    ]
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Need approval",
            ),
        )
    ]
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="message-1:step-1:target-1:message",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            text="Needs review",
        )
    ]
    await store.create_run(state)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor.hitl_coordinator.request_input.assert_awaited_once()
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.AWAITING_USER
    assert persisted.pending_hitl_request_ids == ["hitl-agent-1"]


@pytest.mark.asyncio
async def test_run_agent_awaiting_input_creates_hitl_prompt_and_continuation():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="agent needs auth",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Use external account",
                )
            ],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.AWAITING_INPUT,
            message_id="message-1:step-1:target-1:message",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
            status_message="auth_required",
        )
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor.hitl_coordinator.request_input.assert_awaited_once()
    hitl_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert hitl_kwargs["source"] == "agent"
    assert hitl_kwargs["prompt"] == "auth_required"
    assert hitl_kwargs["continuation_message_id"] == (
        "message-1:step-1:target-1:message"
    )
    assert hitl_kwargs["display_message_id"] == "message-1:step-1:target-1:message"
    assert hitl_kwargs["orchestration_run_id"] == "message-1"
    executor._save_interrupted_state.assert_awaited_once()
    save_kwargs = executor._save_interrupted_state.await_args.kwargs
    assert save_kwargs["kind"].value == "hitl_agent"
    assert save_kwargs["message_id"] == "message-1:step-1:target-1:message"

    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.AWAITING_USER
    assert state.pending_hitl_request_ids == ["hitl-agent-1"]
    assert state.agent_outputs[0].status == StepStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_run_agent_awaiting_input_request_input_exception_cancels_and_fails():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="agent needs auth",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Use external account",
                )
            ],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.AWAITING_INPUT,
            message_id="message-1:step-1:target-1:message",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
            status_message="auth_required",
        )
    )
    request_error = RuntimeError("agent request input failed")
    request_error.request_id = "agent-hitl-1"
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(side_effect=request_error),
        cancel_request=AsyncMock(),
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    executor.hitl_coordinator.request_input.assert_awaited_once()
    executor.hitl_coordinator.cancel_request.assert_awaited_once_with(
        "agent-hitl-1",
        "room-1",
    )
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED


@pytest.mark.asyncio
async def test_run_agent_awaiting_input_save_interrupted_state_exception_cancels_and_fails():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="agent needs auth",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Use external account",
                )
            ],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.AWAITING_INPUT,
            message_id="message-1:step-1:target-1:message",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
            status_message="auth_required",
        )
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1")),
        cancel_request=AsyncMock(),
    )
    executor._save_interrupted_state = AsyncMock(
        side_effect=RuntimeError("failed to save interrupted state")
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    executor.hitl_coordinator.request_input.assert_awaited_once()
    executor.hitl_coordinator.cancel_request.assert_awaited_once_with(
        "hitl-agent-1",
        "room-1",
    )
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED


@pytest.mark.asyncio
async def test_run_ask_user_save_interrupted_state_exception_clears_transient_state():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    request_input = AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    cancel_request = AsyncMock()
    executor.hitl_coordinator = SimpleNamespace(
        request_input=request_input,
        cancel_request=cancel_request,
    )
    executor._save_interrupted_state = AsyncMock(
        side_effect=RuntimeError("failed to save interrupted state")
    )
    executor.message_writer.upsert_room_agent_message = AsyncMock(
        return_value=SimpleNamespace(message_id="clarifier-message-1")
    )
    executor.message_writer.delete_room_agent_message_by_message_id = AsyncMock()

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    request_input.assert_awaited_once()
    cancel_request.assert_awaited_once_with("hitl-1", "room-1")
    created_message_id = (
        executor.message_writer.upsert_room_agent_message.await_args.args[0].message_id
    )
    executor.message_writer.delete_room_agent_message_by_message_id.assert_awaited_once_with(
        created_message_id,
    )
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert persisted.pending_hitl_request_ids == []
    assert persisted.open_questions == []


@pytest.mark.asyncio
async def test_run_mixed_paused_and_awaiting_input_creates_hitl_prompt():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="mixed async inputs",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Async task",
                    parallel_group="fanout-1",
                ),
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="Needs user input",
                    parallel_group="fanout-1",
                ),
            ],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_message_processor.process_single_message = AsyncMock(
        side_effect=[
            ProcessingResult(
                ProcessingStatus.PAUSED,
                message_id="message-1:step-1:target-1:message",
            ),
            ProcessingResult(
                ProcessingStatus.AWAITING_INPUT,
                message_id="message-1:step-1:target-2:message",
                status_message="auth_required",
            ),
        ]
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor.hitl_coordinator.request_input.assert_awaited_once()
    hitl_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert hitl_kwargs["prompt"] == "auth_required"
    assert hitl_kwargs["continuation_message_id"] == (
        "message-1:step-1:target-2:message"
    )
    save_kinds = [
        call.kwargs["kind"].value
        for call in executor._save_interrupted_state.await_args_list
    ]
    assert save_kinds == ["hitl_agent"]
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.AWAITING_USER
    assert state.pending_hitl_request_ids == ["hitl-agent-1"]


@pytest.mark.asyncio
async def test_run_multiple_awaiting_input_results_keep_secondary_awaiting_input_recoverable():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="both need user input",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Auth required",
                    parallel_group="fanout-1",
                ),
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="More context",
                    parallel_group="fanout-1",
                ),
            ],
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_message_processor.process_single_message = AsyncMock(
        side_effect=[
            ProcessingResult(
                ProcessingStatus.AWAITING_INPUT,
                message_id="message-1:step-1:target-1:message",
                status_message="auth_required",
            ),
            ProcessingResult(
                ProcessingStatus.AWAITING_INPUT,
                message_id="message-1:step-1:target-2:message",
                status_message="Need approval.",
            ),
        ]
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    state = await store.get_run("message-1")
    assert state is not None
    outputs_by_id = {
        output.agent_message_id: output
        for output in state.agent_outputs
    }
    assert (
        outputs_by_id["message-1:step-1:target-1:message"].status
        == StepStatus.AWAITING_INPUT.value
    )
    assert outputs_by_id["message-1:step-1:target-2:message"].status == (
        StepStatus.AWAITING_INPUT.value
    )
    assert state.pending_hitl_request_ids == ["hitl-agent-1"]

    recover_executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    recover_executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-2"))
    )
    recover_executor._save_interrupted_state = AsyncMock(return_value=True)

    recovered_state, recover_status = await recover_executor._recover_v2_inflight_dispatch(
        state=state,
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        conversation_context=None,
        token=None,
        request_user_id="user-1",
        quoted_text=None,
        user_message=user_message,
    )

    assert recover_status == RunStatus.AWAITING_INPUT
    assert recovered_state.status == OrchestrationStatus.AWAITING_USER
    assert recover_executor.hitl_coordinator.request_input.await_count == 1
    recovered_outputs_by_id = {
        output.agent_message_id: output for output in recovered_state.agent_outputs
    }
    assert recovered_outputs_by_id["message-1:step-1:target-2:message"].status == (
        StepStatus.AWAITING_INPUT.value
    )


@pytest.mark.asyncio
async def test_run_partial_paused_resume_waits_for_remaining_agents():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    first_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="dispatch both async agents",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="First task",
                    parallel_group="fanout-1",
                ),
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="Second task",
                    parallel_group="fanout-1",
                ),
            ],
        )
    )
    first_executor = _executor(
        store=store,
        planner=first_planner,
        user_message=user_message,
    )
    first_executor.agent_message_processor.process_single_message = AsyncMock(
        side_effect=[
            ProcessingResult(
                ProcessingStatus.PAUSED,
                message_id="message-1:step-1:target-1:message",
            ),
            ProcessingResult(
                ProcessingStatus.PAUSED,
                message_id="message-1:step-1:target-2:message",
            ),
        ]
    )
    first_executor._save_interrupted_state = AsyncMock(return_value=True)

    paused = await first_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        user_message=user_message,
    )
    assert paused.status == RunStatus.PAUSED

    resumed_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resumed delegate",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="First task",
                        ),
                        DelegateTarget(
                            agent_id="agent-2",
                            agent_name="Agent Two",
                            task="Second task",
                        ),
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="First task",
                        response_text="First webhook result",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-1:message",
                    ),
                    StepResult(
                        step_number=1,
                        agent_id="agent-2",
                        agent_name="Agent Two",
                        task="Second task",
                        response_text="",
                        success=True,
                        status=StepStatus.PAUSED,
                        paused_message_id="message-1:step-1:target-2:message",
                        agent_message_id="message-1:step-1:target-2:message",
                    ),
                ],
                started_at=utcnow(),
            )
        ]
    )
    second_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="must not run while sibling is paused",
            synthesis_instruction="Summarize",
        )
    )
    second_executor = _executor(
        store=store,
        planner=second_planner,
        user_message=user_message,
    )

    result = await second_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.PAUSED
    assert second_planner.contexts == []
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.WAITING_AGENT
    assert state.steps_used == 0
    outputs_by_id = {output.agent_message_id: output for output in state.agent_outputs}
    assert (
        outputs_by_id["message-1:step-1:target-1:message"].text
        == "First webhook result"
    )
    assert (
        outputs_by_id["message-1:step-1:target-2:message"].status
        == StepStatus.PAUSED.value
    )


@pytest.mark.asyncio
async def test_run_final_paused_sibling_resume_reconciles_sidecar_outputs():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    first_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="dispatch both async agents",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="First task",
                    parallel_group="fanout-1",
                ),
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="Second task",
                    parallel_group="fanout-1",
                ),
            ],
        )
    )
    first_executor = _executor(
        store=store,
        planner=first_planner,
        user_message=user_message,
    )
    first_executor.agent_message_processor.process_single_message = AsyncMock(
        side_effect=[
            ProcessingResult(
                ProcessingStatus.PAUSED,
                message_id="message-1:step-1:target-1:message",
            ),
            ProcessingResult(
                ProcessingStatus.PAUSED,
                message_id="message-1:step-1:target-2:message",
            ),
        ]
    )
    first_executor._save_interrupted_state = AsyncMock(return_value=True)

    paused = await first_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        user_message=user_message,
    )
    assert paused.status == RunStatus.PAUSED

    delegate_action = SupervisorAction(
        action=ActionType.DELEGATE,
        reasoning="dispatch both async agents",
        targets=[
            DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="First task",
            ),
            DelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="Second task",
            ),
        ],
    )
    first_resume = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=delegate_action,
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="First task",
                        response_text="First result",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-1:message",
                    ),
                    StepResult(
                        step_number=1,
                        agent_id="agent-2",
                        agent_name="Agent Two",
                        task="Second task",
                        response_text="",
                        success=True,
                        status=StepStatus.PAUSED,
                        paused_message_id="message-1:step-1:target-2:message",
                        agent_message_id="message-1:step-1:target-2:message",
                    ),
                ],
                started_at=utcnow(),
            )
        ]
    )
    first_resume_executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    still_paused = await first_resume_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        resumed_trajectory=first_resume,
        user_message=user_message,
    )
    assert still_paused.status == RunStatus.PAUSED

    stale_second_resume = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=delegate_action,
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="First task",
                        response_text="",
                        success=True,
                        status=StepStatus.PAUSED,
                        paused_message_id="message-1:step-1:target-1:message",
                        agent_message_id="message-1:step-1:target-1:message",
                    ),
                    StepResult(
                        step_number=1,
                        agent_id="agent-2",
                        agent_name="Agent Two",
                        task="Second task",
                        response_text="Second result",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-2:message",
                    ),
                ],
                started_at=utcnow(),
            )
        ]
    )
    final_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="summarize both completed siblings",
            synthesis_instruction="Summarize",
        )
    )
    final_executor = _executor(
        store=store,
        planner=final_planner,
        user_message=user_message,
    )

    result = await final_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        resumed_trajectory=stale_second_resume,
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert final_planner.contexts[0].state_context.current_step.steps_used == 1
    output_texts = {
        output["agent_message_id"]: output["text"]
        for output in final_planner.contexts[0].state_context.agent_outputs
    }
    assert output_texts["message-1:step-1:target-1:message"] == "First result"
    assert output_texts["message-1:step-1:target-2:message"] == "Second result"


@pytest.mark.asyncio
async def test_run_resumed_trajectory_only_pending_awaiting_input_rehydrates_hitl_request():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.RUNNING,
        candidate_agent_ids=["agent-1"],
    )
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.system_agent_message_id = "sys-message-1"
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    await store.create_run(state)

    resumed_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resume with pending awaiting input only",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Handle the request",
                        )
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Handle the request",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-1:message",
                    )
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor.hitl_coordinator.request_input.assert_awaited_once()
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.AWAITING_USER
    assert persisted.pending_hitl_request_ids == ["hitl-agent-1"]
    assert persisted.open_questions
    assert persisted.open_questions[0]["request_id"] == "hitl-agent-1"


@pytest.mark.asyncio
async def test_run_resumed_trajectory_mixed_terminal_and_pending_awaiting_input_rehydrates_hitl_request():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.RUNNING,
        candidate_agent_ids=["agent-1", "agent-2"],
    )
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.system_agent_message_id = "sys-message-1"
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Auth required",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="Need approval",
            ),
        ),
    ]
    await store.create_run(state)

    resumed_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resume mixed outcomes",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Auth required",
                        ),
                        DelegateTarget(
                            agent_id="agent-2",
                            agent_name="Agent Two",
                            task="Need approval",
                        ),
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Auth required",
                        response_text="done",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-1:message",
                    ),
                    StepResult(
                        step_number=1,
                        agent_id="agent-2",
                        agent_name="Agent Two",
                        task="Need approval",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-2:message",
                    ),
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        request_user_id="user-1",
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor.hitl_coordinator.request_input.assert_awaited_once()
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.AWAITING_USER
    assert persisted.pending_hitl_request_ids == ["hitl-agent-1"]
    assert persisted.open_questions
    assert persisted.open_questions[0]["request_id"] == "hitl-agent-1"
    output_status_by_id = {
        output.agent_message_id: output.status
        for output in persisted.agent_outputs
    }
    assert output_status_by_id["message-1:step-1:target-1:message"] == "completed"
    assert (
        output_status_by_id["message-1:step-1:target-2:message"]
        == StepStatus.AWAITING_INPUT.value
    )


@pytest.mark.asyncio
async def test_run_resumed_trajectory_mixed_terminal_and_awaiting_input_clears_resolved_hitl_request():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-2"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.RUNNING,
        candidate_agent_ids=["agent-1", "agent-2"],
    )
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.system_agent_message_id = "sys-message-1"
    state.pending_hitl_request_ids = ["old-hitl-id"]
    state.open_questions = [
        {
            "request_id": "old-hitl-id",
            "status": "open",
            "source": "agent",
            "prompt": "Old question",
        }
    ]
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Auth required",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="Need approval",
            ),
        ),
    ]
    await store.create_run(state)

    resumed_trajectory = SupervisorTrajectory(
        hitl_user_reply="approved",
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resume mixed outcomes",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Auth required",
                        ),
                        DelegateTarget(
                            agent_id="agent-2",
                            agent_name="Agent Two",
                            task="Need approval",
                        ),
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Auth required",
                        response_text="done",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-1:message",
                    ),
                    StepResult(
                        step_number=1,
                        agent_id="agent-2",
                        agent_name="Agent Two",
                        task="Need approval",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-2:message",
                    ),
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ],
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        request_user_id="user-1",
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor.hitl_coordinator.request_input.assert_awaited_once()
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.AWAITING_USER
    assert persisted.pending_hitl_request_ids == ["hitl-agent-2"]
    assert all(
        question.get("request_id") != "old-hitl-id" or question.get("status") != "open"
        for question in persisted.open_questions
    )
    output_status_by_id = {
        output.agent_message_id: output.status
        for output in persisted.agent_outputs
    }
    assert output_status_by_id["message-1:step-1:target-1:message"] == "completed"
    assert output_status_by_id["message-1:step-1:target-2:message"] == StepStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_run_resumed_trajectory_clears_resolved_agent_hitl_without_reply_string():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-2"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.RUNNING,
        candidate_agent_ids=["agent-1", "agent-2"],
    )
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.system_agent_message_id = "sys-message-1"
    state.pending_hitl_request_ids = ["hitl-agent-1"]
    state.open_questions = [
        {
            "request_id": "hitl-agent-1",
            "status": "open",
            "source": "agent",
            "agent_id": "agent-1",
            "prompt": "First approval?",
            "display_message_id": "message-1:step-1:target-1:message",
        }
    ]
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="First approval",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="Second approval",
            ),
        ),
    ]
    await store.create_run(state)

    resumed_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="first HITL answered, second still pending",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="First approval",
                        ),
                        DelegateTarget(
                            agent_id="agent-2",
                            agent_name="Agent Two",
                            task="Second approval",
                        ),
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="First approval",
                        response_text="approved",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-1:message",
                    ),
                    StepResult(
                        step_number=1,
                        agent_id="agent-2",
                        agent_name="Agent Two",
                        task="Second approval",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-2:message",
                        status_message="Second approval?",
                    ),
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        request_user_id="user-1",
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    executor.hitl_coordinator.request_input.assert_awaited_once()
    request_input_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert request_input_kwargs["agent_id"] == "agent-2"
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.AWAITING_USER
    assert persisted.pending_hitl_request_ids == ["hitl-agent-2"]
    assert all(
        question.get("request_id") != "hitl-agent-1"
        or question.get("status") != "open"
        for question in persisted.open_questions
    )
    assert any(
        question.get("request_id") == "hitl-agent-2"
        and question.get("status") == "open"
        for question in persisted.open_questions
    )


def test_agent_id_fallback_requires_one_open_pending_question_for_agent():
    state = _run_state(run_id="message-1", user_message_id="message-1")
    state.pending_hitl_request_ids = ["hitl-1", "hitl-2"]
    state.open_questions = [
        {
            "request_id": "hitl-1",
            "source": "agent",
            "status": "open",
            "agent_id": "agent-1",
        },
        {
            "request_id": "hitl-2",
            "source": "agent",
            "status": "open",
            "agent_id": "agent-1",
        },
    ]
    terminal_result = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="Complete one request",
        response_text="done",
        success=True,
        status=StepStatus.SUCCESS,
    )

    resolved = SupervisorExecutor._resolved_agent_hitl_request_ids_for_results(
        state,
        [terminal_result],
    )

    assert resolved == set()


@pytest.mark.asyncio
async def test_sync_v2_resumed_trajectory_clears_pending_hitl_request_ids_after_progress():
    user_message = _state_unification_user_message(message_id="message-1")
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        candidate_agent_ids=["agent-1"],
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    state.pending_hitl_request_ids = ["hitl-1"]
    await store.create_run(state)

    synced_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="replay terminal",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Handle the request",
                        )
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Handle the request",
                        response_text="Done",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-1:message",
                    )
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    restored_state, blocking_status = await executor._sync_v2_resumed_trajectory(
        state,
        synced_trajectory,
    )

    assert blocking_status is None
    assert restored_state.pending_hitl_request_ids == []
    persisted_state = await store.get_run("message-1")
    assert persisted_state is not None
    assert persisted_state.pending_hitl_request_ids == []
    events = store._events_by_run["message-1"]
    result_events = [
        event
        for event in events
        if event.type == OrchestrationEventType.AGENT_RESULT_INGESTED
    ]
    assert [event.payload["agent_message_id"] for event in result_events] == [
        "message-1:step-1:target-1:message",
    ]
    assert result_events[0].state_version == 1


@pytest.mark.asyncio
async def test_agent_hitl_resume_persists_outcomes_for_terminal_and_awaiting_results():
    user_message = _state_unification_user_message(message_id="message-1")
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        candidate_agent_ids=["agent-1", "agent-2"],
        status=OrchestrationStatus.RUNNING,
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Auth required",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="Need approval",
            ),
        ),
    ]
    await store.create_run(state)

    synced_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resume mixed outcomes",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Auth required",
                        ),
                        DelegateTarget(
                            agent_id="agent-2",
                            agent_name="Agent Two",
                            task="Need approval",
                        ),
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Auth required",
                        response_text="done",
                        success=True,
                        status=StepStatus.SUCCESS,
                        agent_message_id="message-1:step-1:target-1:message",
                    ),
                    StepResult(
                        step_number=1,
                        agent_id="agent-2",
                        agent_name="Agent Two",
                        task="Need approval",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-2:message",
                    ),
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    restored_state, blocking_status = await executor._sync_v2_resumed_trajectory(
        state,
        synced_trajectory,
    )

    assert blocking_status == RunStatus.AWAITING_INPUT
    assert restored_state.status == OrchestrationStatus.WAITING_AGENT
    assert restored_state.pending_hitl_request_ids == []
    persisted_state = await store.get_run("message-1")
    assert persisted_state is not None
    assert persisted_state.status == OrchestrationStatus.WAITING_AGENT
    assert len(persisted_state.agent_outputs) == 2
    assert [
        output.status
        for output in persisted_state.agent_outputs
    ] == ["completed", StepStatus.AWAITING_INPUT.value]
    assert {
        output.agent_message_id: output.status
        for output in persisted_state.agent_outputs
    } == {
        "message-1:step-1:target-1:message": "completed",
        "message-1:step-1:target-2:message": StepStatus.AWAITING_INPUT.value,
    }
    assert len(persisted_state.delegation_outcomes) == 2
    assert [event.type for event in store._events_by_run["message-1"]].count(
        OrchestrationEventType.OUTCOME_EVALUATED
    ) == 2


@pytest.mark.asyncio
async def test_sync_v2_resumed_trajectory_waiting_agent_with_awaiting_input_has_no_awaiting_user():
    user_message = _state_unification_user_message(message_id="message-1")
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.RUNNING,
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    await store.create_run(state)

    synced_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resume with awaiting input",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Handle the request",
                        )
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Handle the request",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-1:message",
                    )
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    restored_state, blocking_status = await executor._sync_v2_resumed_trajectory(
        state,
        synced_trajectory,
    )

    assert blocking_status == RunStatus.AWAITING_INPUT
    assert restored_state.status == OrchestrationStatus.WAITING_AGENT
    assert restored_state.pending_hitl_request_ids == []
    persisted_state = await store.get_run("message-1")
    assert persisted_state is not None
    assert persisted_state.status == OrchestrationStatus.WAITING_AGENT
    assert len(persisted_state.agent_outputs) == 1
    assert persisted_state.agent_outputs[0].agent_message_id == (
        "message-1:step-1:target-1:message"
    )
    assert persisted_state.agent_outputs[0].status == StepStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_sync_v2_resumed_trajectory_only_pending_awaiting_input_is_persisted_recoverably():
    user_message = _state_unification_user_message(message_id="message-1")
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.RUNNING,
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    await store.create_run(state)

    synced_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resume with pending awaiting input only",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Handle the request",
                        )
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Handle the request",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-1:message",
                    )
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    restored_state, blocking_status = await executor._sync_v2_resumed_trajectory(
        state,
        synced_trajectory,
    )

    assert blocking_status == RunStatus.AWAITING_INPUT
    assert restored_state.status == OrchestrationStatus.WAITING_AGENT
    assert restored_state.pending_hitl_request_ids == []
    persisted_state = await store.get_run("message-1")
    assert persisted_state is not None
    assert persisted_state.status == OrchestrationStatus.WAITING_AGENT
    assert persisted_state.pending_hitl_request_ids == []
    assert [
        output.agent_message_id for output in persisted_state.agent_outputs
    ] == ["message-1:step-1:target-1:message"]
    assert persisted_state.agent_outputs[0].status == StepStatus.AWAITING_INPUT.value
    assert restored_state.agent_outputs[0].status == StepStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_run_resumed_pending_awaiting_input_without_dispatch_intents_does_not_plan():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="must not be invoked",
            synthesis_instruction="This would silently plan past HITL",
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.RUNNING,
        candidate_agent_ids=["agent-1"],
    )
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.system_agent_message_id = "sys-message-1"
    await store.create_run(state)

    resumed_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="reconstructed without sidecar intents",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Handle the request",
                        )
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Handle the request",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="agent-msg-1",
                        a2a_task_id="task-1",
                        a2a_context_id="ctx-1",
                        status_message="Provide missing details",
                    )
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.AWAITING_INPUT
    assert planner.contexts == []
    executor.hitl_coordinator.request_input.assert_awaited_once()
    request_input_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert request_input_kwargs["prompt"] == "Provide missing details"
    assert request_input_kwargs["a2a_task_id"] == "task-1"
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.AWAITING_USER
    assert persisted.pending_hitl_request_ids == ["hitl-agent-1"]
    assert persisted.open_questions[0]["request_id"] == "hitl-agent-1"


@pytest.mark.asyncio
async def test_recover_v2_inflight_dispatch_rehydrates_awaiting_input_output_to_hitl():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.WAITING_AGENT,
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="message-1:step-1:target-1:message",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            text="Needs human input",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
            status_message="Provide missing details",
        )
    ]
    await store.create_run(state)

    recovered_state, run_status = await executor._recover_v2_inflight_dispatch(
        state=state,
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        conversation_context=None,
        token=None,
        request_user_id="user-1",
        quoted_text=None,
        user_message=user_message,
    )

    assert run_status == RunStatus.AWAITING_INPUT
    assert recovered_state.status == OrchestrationStatus.AWAITING_USER
    assert recovered_state.pending_hitl_request_ids == ["hitl-agent-1"]
    executor.hitl_coordinator.request_input.assert_awaited_once()
    request_input_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert request_input_kwargs["a2a_task_id"] == "task-1"
    assert request_input_kwargs["a2a_context_id"] == "ctx-1"
    assert request_input_kwargs["prompt"] == "Provide missing details"


@pytest.mark.asyncio
async def test_inflight_recovery_persists_outcome_for_interactive_message_without_output_record():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)
    message_task_status = SimpleNamespace(
        state="input-required",
        message=SimpleNamespace(message_text="Please provide missing details."),
    )
    executor.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            message_id="message-1:step-1:target-1:message",
            message_content=SimpleNamespace(
                message_text="",
                message_task=SimpleNamespace(
                    status=message_task_status,
                    metadata={
                        "hitl_a2a_task_id": "task-1",
                        "hitl_a2a_context_id": "ctx-1",
                    },
                ),
            ),
        )
    )

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        candidate_agent_ids=["agent-1"],
        status=OrchestrationStatus.WAITING_AGENT,
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    await store.create_run(state)

    recovered_state, run_status = await executor._recover_v2_inflight_dispatch(
        state=state,
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        conversation_context=None,
        token=None,
        request_user_id="user-1",
        quoted_text=None,
        user_message=user_message,
    )

    assert run_status == RunStatus.AWAITING_INPUT
    assert recovered_state.status == OrchestrationStatus.AWAITING_USER
    assert recovered_state.pending_hitl_request_ids == ["hitl-agent-1"]
    executor.hitl_coordinator.request_input.assert_awaited_once()
    request_input_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert request_input_kwargs["a2a_task_id"] == "task-1"
    assert request_input_kwargs["a2a_context_id"] == "ctx-1"
    assert (
        request_input_kwargs["prompt"]
        == "Please provide missing details."
    )
    assert recovered_state.agent_outputs
    recovered_output = recovered_state.agent_outputs[0]
    assert recovered_output.a2a_task_id == "task-1"
    assert recovered_output.a2a_context_id == "ctx-1"
    assert recovered_output.status == StepStatus.AWAITING_INPUT.value
    assert len(recovered_state.delegation_outcomes) == 1
    assert [event.type for event in store._events_by_run["message-1"]].count(
        OrchestrationEventType.OUTCOME_EVALUATED
    ) == 1


@pytest.mark.asyncio
async def test_recover_v2_inflight_dispatch_paused_and_awaiting_saves_paused_before_hitl():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    call_order: list[str] = []

    async def save_interrupted_state_spy(
        kind: InterruptKind,
        **_: object,
    ) -> bool:
        call_order.append(f"save:{kind.value}")
        return True

    async def request_input_spy(*_, **__) -> SimpleNamespace:
        call_order.append("request_input")
        return SimpleNamespace(request_id="hitl-agent-1")

    executor.hitl_coordinator = SimpleNamespace(request_input=AsyncMock(side_effect=request_input_spy))
    executor._save_interrupted_state = AsyncMock(
        side_effect=save_interrupted_state_spy
    )

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        candidate_agent_ids=["agent-1", "agent-2"],
        status=OrchestrationStatus.WAITING_AGENT,
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="First task",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="Second task",
            ),
        ),
    ]
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="message-1:step-1:target-1:message",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            text="Needs human input",
        ),
        AgentOutputRecord(
            agent_message_id="message-1:step-1:target-2:message",
            agent_id="agent-2",
            status=StepStatus.PAUSED.value,
            text="Paused",
            paused_message_id="message-1:step-1:target-2:message",
        ),
    ]
    await store.create_run(state)

    recovered_state, run_status = await executor._recover_v2_inflight_dispatch(
        state=state,
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        conversation_context=None,
        token=None,
        request_user_id="user-1",
        quoted_text=None,
        user_message=user_message,
    )

    assert run_status == RunStatus.AWAITING_INPUT
    assert recovered_state.status == OrchestrationStatus.AWAITING_USER
    assert recovered_state.pending_hitl_request_ids == ["hitl-agent-1"]
    assert call_order[0] == "save:push_notification"
    assert call_order[1] == "request_input"
    assert "save:hitl_agent" in call_order


@pytest.mark.asyncio
async def test_recover_v2_inflight_dispatch_paused_and_awaiting_fails_without_hitl_if_save_fails():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1", "agent-2"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    call_order: list[str] = []

    async def save_interrupted_state_spy(
        kind: InterruptKind,
        **_: object,
    ) -> bool:
        call_order.append(f"save:{kind.value}")
        return False

    async def request_input_spy(*_, **__) -> SimpleNamespace:
        call_order.append("request_input")
        return SimpleNamespace(request_id="hitl-agent-1")

    executor.hitl_coordinator = SimpleNamespace(request_input=AsyncMock(side_effect=request_input_spy))
    executor._save_interrupted_state = AsyncMock(
        side_effect=save_interrupted_state_spy
    )

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        candidate_agent_ids=["agent-1", "agent-2"],
        status=OrchestrationStatus.WAITING_AGENT,
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="First task",
            ),
        ),
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=2,
            target=DelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="Second task",
            ),
        ),
    ]
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="message-1:step-1:target-1:message",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            text="Needs human input",
        ),
        AgentOutputRecord(
            agent_message_id="message-1:step-1:target-2:message",
            agent_id="agent-2",
            status=StepStatus.PAUSED.value,
            text="Paused",
            paused_message_id="message-1:step-1:target-2:message",
        ),
    ]
    await store.create_run(state)

    recovered_state, run_status = await executor._recover_v2_inflight_dispatch(
        state=state,
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Agent One"),
            AgentProfile(agent_id="agent-2", agent_name="Agent Two"),
        ],
        room_config=RoomConfig(),
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        conversation_context=None,
        token=None,
        request_user_id="user-1",
        quoted_text=None,
        user_message=user_message,
    )

    assert run_status == RunStatus.FAILED
    assert recovered_state.status == OrchestrationStatus.FAILED
    assert not any(event == "request_input" for event in call_order)
    assert call_order == ["save:push_notification"]


@pytest.mark.asyncio
async def test_run_agent_awaiting_input_cancels_hitl_request_if_v2_state_save_fails(
    monkeypatch,
):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.continuation_store.get_and_clear_continuation_on_message = AsyncMock()
    request_input = AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    cancel_request = AsyncMock()
    executor.hitl_coordinator = SimpleNamespace(
        request_input=request_input,
        cancel_request=cancel_request,
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.RUNNING,
        candidate_agent_ids=["agent-1"],
    )
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.system_agent_message_id = "sys-message-1"
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    await store.create_run(state)

    original_save_state = store.save_state

    async def save_state_spy(
        next_state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        if next_state.status == OrchestrationStatus.AWAITING_USER:
            raise RuntimeError("failed to persist awaitl user state")
        return await original_save_state(next_state, expected_version=expected_version)

    monkeypatch.setattr(store, "save_state", save_state_spy)

    resumed_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resume pending awaiting input only",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Handle the request",
                        )
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Handle the request",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-1:message",
                    )
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    request_input.assert_awaited_once()
    cancel_request.assert_awaited_once_with("hitl-agent-1", "room-1")
    executor.continuation_store.get_and_clear_continuation_on_message.assert_awaited_once_with(
        "message-1:step-1:target-1:message"
    )
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert persisted.pending_hitl_request_ids == []
    assert persisted.open_questions == []


@pytest.mark.asyncio
async def test_run_agent_awaiting_input_preserves_request_reference_when_cleanup_cancel_fails(
    monkeypatch,
):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.continuation_store.get_and_clear_continuation_on_message = AsyncMock()
    request_input = AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    cancel_request = AsyncMock(side_effect=RuntimeError("cancel failed"))
    executor.hitl_coordinator = SimpleNamespace(
        request_input=request_input,
        cancel_request=cancel_request,
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        status=OrchestrationStatus.RUNNING,
        candidate_agent_ids=["agent-1"],
    )
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.system_agent_message_id = "sys-message-1"
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    await store.create_run(state)

    original_save_state = store.save_state

    async def save_state_spy(
        next_state: OrchestrationRunState,
        *,
        expected_version: int,
    ) -> OrchestrationRunState:
        if next_state.status == OrchestrationStatus.AWAITING_USER:
            raise RuntimeError("failed to persist await user state")
        return await original_save_state(next_state, expected_version=expected_version)

    monkeypatch.setattr(store, "save_state", save_state_spy)

    resumed_trajectory = SupervisorTrajectory(
        entries=[
            TrajectoryEntry(
                step_number=1,
                action=SupervisorAction(
                    action=ActionType.DELEGATE,
                    reasoning="resume pending awaiting input only",
                    targets=[
                        DelegateTarget(
                            agent_id="agent-1",
                            agent_name="Agent One",
                            task="Handle the request",
                        )
                    ],
                ),
                results=[
                    StepResult(
                        step_number=1,
                        agent_id="agent-1",
                        agent_name="Agent One",
                        task="Handle the request",
                        response_text="",
                        success=False,
                        status=StepStatus.AWAITING_INPUT,
                        agent_message_id="message-1:step-1:target-1:message",
                    )
                ],
                started_at=utcnow(),
                completed_at=utcnow(),
            )
        ]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    request_input.assert_awaited_once()
    cancel_request.assert_awaited_once_with("hitl-agent-1", "room-1")
    persisted = await store.get_run("message-1")
    assert persisted is not None
    assert persisted.status == OrchestrationStatus.FAILED
    assert "hitl-agent-1" in persisted.pending_hitl_request_ids
    cleanup_questions = [
        question
        for question in persisted.open_questions
        if question.get("request_id") == "hitl-agent-1"
    ]
    assert cleanup_questions
    assert cleanup_questions[0]["status"] == "cleanup_failed"


@pytest.mark.asyncio
async def test_ingest_v2_results_surfaces_outcome_event_append_failures(monkeypatch):
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        run_id="run-1",
        user_message_id="msg-1",
        candidate_agent_ids=["agent-1"],
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="one",
            ),
        )
    ]
    await store.create_run(state)

    original_append_event = store.append_event

    async def fail_outcome_event(event):
        if event.type == OrchestrationEventType.OUTCOME_EVALUATED:
            raise RuntimeError("oops")
        return await original_append_event(event)

    monkeypatch.setattr(store, "append_event", fail_outcome_event)

    with pytest.raises(RuntimeError, match="oops"):
        await executor._ingest_v2_results(
            await store.get_run("run-1"),
            [
                StepResult(
                    step_number=1,
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="one",
                    response_text="done",
                    success=True,
                    status=StepStatus.SUCCESS,
                    agent_message_id="run-1:step-1:target-1:message",
                )
            ],
            status=OrchestrationStatus.RUNNING,
            advance_step=True,
        )

    persisted = await store.get_run("run-1")
    assert persisted is not None
    assert persisted.state_version == 1
    assert persisted.agent_outputs[0].status == "completed"


@pytest.mark.asyncio
async def test_ingest_v2_results_retries_missing_persisted_outcome_event(monkeypatch):
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(candidate_agent_ids=["agent-1"])
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="produce quote",
            ),
        )
    ]
    await store.create_run(state)
    result = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="produce quote",
        response_text="quote ready",
        success=True,
        status=StepStatus.SUCCESS,
        agent_message_id="run-1:step-1:target-1:message",
    )
    original_append_event = store.append_event
    append_attempts = 0

    async def fail_first_outcome_event(event):
        nonlocal append_attempts
        if event.type == OrchestrationEventType.OUTCOME_EVALUATED:
            append_attempts += 1
            if append_attempts == 1:
                raise RuntimeError("outcome event unavailable")
        return await original_append_event(event)

    monkeypatch.setattr(store, "append_event", fail_first_outcome_event)

    with pytest.raises(RuntimeError, match="outcome event unavailable"):
        await executor._ingest_v2_results(
            await store.get_run("run-1"),
            [result],
            status=OrchestrationStatus.RUNNING,
            advance_step=True,
        )

    await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [result],
        status=OrchestrationStatus.RUNNING,
        advance_step=False,
    )

    persisted = await store.get_run("run-1")
    assert persisted is not None
    assert len(persisted.delegation_outcomes) == 1
    events = store._events_by_run["run-1"]
    assert [event.type for event in events].count(
        OrchestrationEventType.OUTCOME_EVALUATED
    ) == 1


@pytest.mark.asyncio
async def test_ingest_v2_results_persists_one_idempotent_outcome():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(candidate_agent_ids=["agent-1"])
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="produce quote",
            ),
        )
    ]
    await store.create_run(state)
    result = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="produce quote",
        response_text="quote ready",
        success=True,
        status=StepStatus.SUCCESS,
        agent_message_id="run-1:step-1:target-1:message",
    )

    await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [result],
        status=OrchestrationStatus.RUNNING,
        advance_step=True,
    )
    await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [result],
        status=OrchestrationStatus.RUNNING,
        advance_step=False,
    )

    saved = await store.get_run("run-1")
    assert saved is not None
    assert len(saved.delegation_outcomes) == 1
    assert saved.delegation_outcomes[0].dispatch_intent_id.endswith(":intent")
    events = store._events_by_run["run-1"]
    assert [event.type for event in events].count(
        OrchestrationEventType.AGENT_RESULT_INGESTED
    ) == 1
    assert [event.type for event in events].count(
        OrchestrationEventType.OUTCOME_EVALUATED
    ) == 1


@pytest.mark.asyncio
async def test_ingest_v2_results_segments_outcomes_by_selected_resource_content():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(candidate_agent_ids=["agent-1"])
    state.facts = [
        {"fact_id": "context-1", "kind": "context", "text": "first evidence"},
        {"fact_id": "context-2", "kind": "context", "text": "second evidence"},
    ]
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=index,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="produce quote",
                context_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.CONTEXT,
                        ref_id=f"context-{index}",
                    )
                ],
            ),
        )
        for index in (1, 2)
    ]
    await store.create_run(state)

    for index in (1, 2):
        await executor._ingest_v2_results(
            await store.get_run("run-1"),
            [
                StepResult(
                    step_number=index,
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="produce quote",
                    response_text=f"quote {index}",
                    success=True,
                    status=StepStatus.SUCCESS,
                    agent_message_id=f"run-1:step-{index}:target-1:message",
                )
            ],
            status=OrchestrationStatus.RUNNING,
            advance_step=True,
        )

    saved = await store.get_run("run-1")
    assert saved is not None
    assert len(saved.delegation_outcomes) == 2
    first, second = saved.delegation_outcomes
    assert first.goal_family_fingerprint == second.goal_family_fingerprint
    assert first.goal_revision_fingerprint != second.goal_revision_fingerprint
    assert first.attempt_fingerprint != second.attempt_fingerprint


@pytest.mark.asyncio
async def test_append_v2_event_swallows_non_required_store_conflicts(monkeypatch):
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    monkeypatch.setattr(
        store,
        "append_event",
        AsyncMock(side_effect=OrchestrationStoreConflict("version conflict")),
    )

    await executor._append_v2_event(
        _run_state(),
        OrchestrationEventType.STATE_REDUCED,
        payload={},
    )

    with pytest.raises(OrchestrationStoreConflict, match="version conflict"):
        await executor._append_v2_event(
            _run_state(),
            OrchestrationEventType.OUTCOME_EVALUATED,
            required=True,
            payload={},
        )


@pytest.mark.asyncio
async def test_ingest_v2_results_distinguishes_changed_error_message():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(candidate_agent_ids=["agent-1"])
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="one"),
        )
    ]
    await store.create_run(state)
    base = dict(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="one",
        response_text="failed",
        success=False,
        status=StepStatus.FAILED,
        agent_message_id="run-1:step-1:target-1:message",
    )

    await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [StepResult(**base, error_message="Upstream timeout")],
        status=OrchestrationStatus.RUNNING,
        advance_step=True,
    )
    await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [StepResult(**base, error_message="Invalid request")],
        status=OrchestrationStatus.RUNNING,
        advance_step=False,
    )

    saved = await store.get_run("run-1")
    assert saved is not None
    assert len(saved.delegation_outcomes) == 2
    assert len({outcome.outcome_id for outcome in saved.delegation_outcomes}) == 2
    events = store._events_by_run["run-1"]
    assert [event.type for event in events].count(
        OrchestrationEventType.AGENT_RESULT_INGESTED
    ) == 2


@pytest.mark.asyncio
async def test_ingest_v2_results_distinguishes_changed_artifact_content():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(candidate_agent_ids=["agent-1"])
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="run-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="one"),
        )
    ]
    await store.create_run(state)
    artifacts = iter(
        [
            [{"artifact_id": "quote", "content": {"premium": 100}}],
            [{"artifact_id": "quote", "content": {"premium": 125}}],
        ]
    )
    executor._v2_artifacts_for_output_message = AsyncMock(side_effect=artifacts)
    result = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="one",
        response_text="quote ready",
        success=True,
        status=StepStatus.SUCCESS,
        agent_message_id="run-1:step-1:target-1:message",
    )

    await executor._ingest_v2_results(
        await store.get_run("run-1"), [result], status=OrchestrationStatus.RUNNING, advance_step=True
    )
    await executor._ingest_v2_results(
        await store.get_run("run-1"), [result], status=OrchestrationStatus.RUNNING, advance_step=False
    )

    saved = await store.get_run("run-1")
    assert saved is not None
    assert len(saved.delegation_outcomes) == 2
    assert len({outcome.outcome_id for outcome in saved.delegation_outcomes}) == 2
    events = store._events_by_run["run-1"]
    assert [event.type for event in events].count(
        OrchestrationEventType.AGENT_RESULT_INGESTED
    ) == 2


@pytest.mark.asyncio
async def test_recover_v2_inflight_dispatch_preserves_artifacts_for_replayed_output():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )

    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        room_id="room-1",
        candidate_agent_ids=["agent-1"],
    )
    state.dispatch_intents = [
        executor._v2_dispatch_intent(
            run_id="message-1",
            step_number=1,
            target_index=1,
            target=DelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Handle the request",
            ),
        )
    ]
    artifact_key = "message-1:step-1:target-1:artifact"
    state.agent_outputs = [
        AgentOutputRecord(
            agent_message_id="message-1:step-1:target-1:message",
            agent_id="agent-1",
            status="completed",
            text="Recovered output",
            artifact_keys=[artifact_key],
        )
    ]
    state.artifacts = [
        {
            "artifact_key": artifact_key,
            "artifact_id": "artifact-1",
            "source_agent_message_id": "message-1:step-1:target-1:message",
            "source_agent_id": "agent-1",
            "kind": "agent_file",
            "summary": "existing artifact",
        }
    ]
    await store.create_run(state)

    recovered_state, run_status = await executor._recover_v2_inflight_dispatch(
        state=state,
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        conversation_context=None,
        token=None,
        request_user_id="user-1",
        quoted_text=None,
        user_message=user_message,
    )

    assert run_status is None
    assert recovered_state.state_version == 1
    persisted_state = await store.get_run("message-1")
    assert persisted_state is not None
    persisted_artifact = persisted_state.artifacts[0]
    assert persisted_state.agent_outputs[0].artifact_keys == [
        persisted_artifact["artifact_key"]
    ]
    assert persisted_artifact["artifact_id"] == "artifact-1"
    assert persisted_artifact["summary"] == "existing artifact"


@pytest.mark.asyncio
async def test_run_paused_result_reconciles_terminal_agent_before_waiting():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="dispatch async agent",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Async task",
                )
            ],
        ),
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="terminal result already visible",
            synthesis_instruction="Summarize",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.PAUSED,
            message_id="message-1:step-1:target-1:message",
        )
    )
    executor.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            message_id="message-1:step-1:target-1:message",
            last_notified_state="completed",
            message_content=SimpleNamespace(message_text="Fast webhook result"),
        )
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    executor._save_interrupted_state.assert_not_awaited()
    assert planner.contexts[1].state_context.agent_outputs[0]["text"] == (
        "Fast webhook result"
    )


@pytest.mark.asyncio
async def test_run_supervisor_hitl_resume_clears_pending_request_ids():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    first_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    first_executor = _executor(
        store=store,
        planner=first_planner,
        user_message=user_message,
    )
    first_executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    )
    first_executor._save_interrupted_state = AsyncMock(return_value=True)

    first_result = await first_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )
    assert first_result.status == RunStatus.AWAITING_INPUT
    state = await store.get_run("message-1")
    assert state is not None
    assert state.pending_hitl_request_ids == ["hitl-1"]

    assert first_result.trajectory is None
    resumed_trajectory = SupervisorTrajectory(hitl_user_reply="Account A")
    second_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.FAIL,
            reasoning="done after user reply",
            failure_reason="stop after inspecting context",
        )
    )
    second_executor = _executor(
        store=store,
        planner=second_planner,
        user_message=user_message,
    )

    second_result = await second_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert second_result.status == RunStatus.FAILED
    assert second_planner.contexts[0].state_context.pending_hitl_request_ids == []
    state = await store.get_run("message-1")
    assert state is not None
    assert state.pending_hitl_request_ids == []
    assert any(
        fact.get("source") == "hitl_user_reply"
        and fact.get("text") == "Account A"
        and fact.get("request_ids") == ["hitl-1"]
        for fact in state.facts
    )
    assert any(
        question.get("request_id") == "hitl-1"
        and question.get("status") == "resolved"
        and question.get("answer") == "Account A"
        for question in state.open_questions
    )


@pytest.mark.asyncio
async def test_run_supervisor_hitl_reply_allows_complete_after_question_resolves():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    first_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need user choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    first_executor = _executor(store=store, planner=first_planner, user_message=user_message)
    first_executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    )
    first_executor._save_interrupted_state = AsyncMock(return_value=True)

    first_result = await first_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )
    assert first_result.status == RunStatus.AWAITING_INPUT

    second_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.COMPLETE,
            reasoning="user supplied enough information",
            completion_evidence=CompletionEvidence(
                satisfied_criteria=["User selected an account"],
                final_answer_intent="Use Account A",
                confidence=0.9,
            ),
        )
    )
    second_executor = _executor(store=store, planner=second_planner, user_message=user_message)

    result = await second_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        resumed_trajectory=SupervisorTrajectory(hitl_user_reply="Account A"),
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.COMPLETED
    assert state.open_questions[0]["resolved"] is True


@pytest.mark.asyncio
async def test_run_supervisor_hitl_reply_is_consumed_before_next_hitl_round():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    first_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need first choice",
            questions=[{"prompt": "Which account?", "prompt_type": "text"}],
        )
    )
    first_executor = _executor(store=store, planner=first_planner, user_message=user_message)
    first_executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-1"))
    )
    first_executor._save_interrupted_state = AsyncMock(return_value=True)

    first_result = await first_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert first_result.trajectory is None
    resumed_trajectory = SupervisorTrajectory(hitl_user_reply="Account A")
    second_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.ASK_USER,
            reasoning="need second choice",
            questions=[{"prompt": "Which region?", "prompt_type": "text"}],
        )
    )
    second_executor = _executor(store=store, planner=second_planner, user_message=user_message)
    second_executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-2"))
    )
    second_executor._save_interrupted_state = AsyncMock(return_value=True)

    second_result = await second_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        resumed_trajectory=resumed_trajectory,
        user_message=user_message,
    )

    assert second_result.status == RunStatus.AWAITING_INPUT
    assert second_result.trajectory is None
    state = await store.get_run("message-1")
    assert state is not None
    assert state.pending_hitl_request_ids == ["hitl-2"]

    third_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="should not be planned without the second reply",
        )
    )
    third_executor = _executor(store=store, planner=third_planner, user_message=user_message)

    third_result = await third_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        resumed_trajectory=SupervisorTrajectory(),
        user_message=user_message,
    )

    assert third_result.status == RunStatus.AWAITING_INPUT
    assert third_planner.contexts == []
    state = await store.get_run("message-1")
    assert state is not None
    assert state.pending_hitl_request_ids == ["hitl-2"]


@pytest.mark.asyncio
async def test_run_invalid_planner_action_fails_after_retry_exhaustion():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    invalid_action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="invalid target",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-2",
                agent_name="Agent Two",
                task="Not in scope",
            )
        ],
    )
    planner = RecordingPlanner(
        invalid_action,
        invalid_action.model_copy(deep=True),
        invalid_action.model_copy(deep=True),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.FAILED
    assert "not in candidate_agent_ids" in (state.terminal_reason or "")
    assert state.open_failures[0].retry_count == 2
    assert state.open_failures[0].status == "abandoned"


@pytest.mark.asyncio
async def test_run_adapter_validation_error_marks_sidecar_failed():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )

    async def invalid_raw_action(_context):
        return {
            "action": "delegate",
            "reasoning": "invalid target",
            "targets": [
                {
                    "agent_id": "agent-2",
                    "agent_name": "Agent Two",
                    "task": "Not in scope",
                }
            ],
        }

    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RoomSupervisorPlannerAdapter(raw_action_provider=invalid_raw_action),
        user_message=user_message,
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.FAILED
    assert "not in candidate_agent_ids" in (state.terminal_reason or "")


@pytest.mark.asyncio
async def test_run_accepts_legacy_done_from_room_supervisor_adapter():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    legacy_actions = [
        {
            "action": "delegate",
            "reasoning": "ask the selected agent",
            "targets": [
                {
                    "agent_id": "agent-1",
                    "agent_name": "Agent One",
                    "task": "Handle the request",
                }
            ],
        },
        {
            "action": "done",
            "reasoning": "legacy done after agent response",
        },
    ]

    async def raw_action_provider(_context):
        return legacy_actions.pop(0)

    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RoomSupervisorPlannerAdapter(raw_action_provider=raw_action_provider),
        user_message=user_message,
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert result.trajectory is None
    assert result.run_state is not None
    assert result.run_state.status == OrchestrationStatus.COMPLETED
    assert result.run_state.terminal_reason == "legacy done after agent response"
    assert result.run_state.decision_log[-1]["action"] == "complete"


@pytest.mark.asyncio
async def test_run_accepts_legacy_done_from_adapter_with_facts_only_state():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )

    async def raw_action_provider(_context):
        return {
            "action": "done",
            "reasoning": "facts satisfy the request",
        }

    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope=user_message.extend_info,
        goal="Coordinate this",
    )
    state.facts.append(
        {
            "fact_id": "fact-1",
            "text": "The customer already selected Account A.",
            "source": "hitl_user_reply",
        }
    )
    await store.create_run(state)
    executor = _executor(
        store=store,
        planner=RoomSupervisorPlannerAdapter(raw_action_provider=raw_action_provider),
        user_message=user_message,
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert result.trajectory is None
    assert result.run_state is not None
    assert result.run_state.status == OrchestrationStatus.COMPLETED
    assert result.run_state.facts[0]["fact_id"] == "fact-1"


@pytest.mark.asyncio
async def test_run_reentry_reconciles_inflight_dispatch_before_planning():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope=user_message.extend_info,
        goal="Coordinate this",
    )
    state.status = OrchestrationStatus.DISPATCHING
    state.state_version = 0
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.dispatch_intents.append(
        DispatchIntent(
            step_id="message-1:step-1",
            step_target_id="message-1:step-1:target-1",
            dispatch_intent_id="message-1:step-1:target-1:intent",
            planned_agent_message_id="message-1:step-1:target-1:message",
            agent_id="agent-1",
            task="Handle",
            task_hash="hash",
        )
    )
    await store.create_run(state)
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="summarize recovered output",
            synthesis_instruction="Summarize",
        )
    )
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        side_effect=lambda message_id: SimpleNamespace(
            message_id=message_id,
            last_notified_state="completed",
            message_content=SimpleNamespace(message_text="Recovered output"),
        )
        if message_id == "message-1:step-1:target-1:message"
        else None
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert planner.contexts[0].state_context.current_step.steps_used == 1
    assert planner.contexts[0].state_context.agent_outputs[0]["text"] == (
        "Recovered output"
    )
    executor.agent_message_processor.process_single_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_reentry_replays_planned_intent_without_created_message():
    refs = _dispatch_refs_payload()
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope=user_message.extend_info,
        goal="Coordinate this",
    )
    state.status = OrchestrationStatus.DISPATCHING
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.dispatch_intents.append(
        DispatchIntent(
            step_id="message-1:step-1",
            step_target_id="message-1:step-1:target-1",
            dispatch_intent_id="message-1:step-1:target-1:intent",
            planned_agent_message_id="message-1:step-1:target-1:message",
            agent_id="agent-1",
            task="Handle",
            task_hash="hash",
            status="planned",
            context_refs=refs["context_refs"],
            artifact_refs=refs["artifact_refs"],
            attachment_refs=refs["attachment_refs"],
            expected_outputs=refs["expected_outputs"],
        )
    )
    await store.create_run(state)
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="summarize replayed output",
            synthesis_instruction="Summarize",
        )
    )
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    executor.agent_message_processor.process_single_message.assert_awaited_once()
    added_message_ids = [
        call.args[0].message_id
        for call in executor.message_writer.add_room_agent_message.await_args_list
    ]
    assert "message-1:step-1:target-1:message" in added_message_ids
    replayed_message = executor.message_writer.add_room_agent_message.await_args_list[
        1
    ].args[0]
    assert replayed_message.extend_info["attachment_forwarding_policy"] == (
        "explicit_refs_only"
    )
    assert replayed_message.extend_info["dispatch_payload_refs"] == {
        "context_refs": [
            ref.model_dump(mode="json") for ref in refs["context_refs"]
        ],
        "artifact_refs": [
            ref.model_dump(mode="json") for ref in refs["artifact_refs"]
        ],
        "attachment_refs": [
            ref.model_dump(mode="json") for ref in refs["attachment_refs"]
        ],
        "expected_outputs": [
            output.model_dump(mode="json") for output in refs["expected_outputs"]
        ],
    }
    assert planner.contexts[0].state_context.current_step.steps_used == 1


@pytest.mark.asyncio
async def test_run_replay_waits_when_planned_message_already_exists():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    state = await store.reconstruct_from_envelope(
        run_id="message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope=user_message.extend_info,
        goal="Coordinate this",
    )
    state.status = OrchestrationStatus.DISPATCHING
    state.summary_intent_id = "message-1:summary"
    state.summary_message_id = "sys-message-1"
    state.dispatch_intents.append(
        DispatchIntent(
            step_id="message-1:step-1",
            step_target_id="message-1:step-1:target-1",
            dispatch_intent_id="message-1:step-1:target-1:intent",
            planned_agent_message_id="message-1:step-1:target-1:message",
            agent_id="agent-1",
            task="Handle",
            task_hash="hash",
            status="planned",
        )
    )
    await store.create_run(state)
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="should not be reached while existing dispatch is pending",
            synthesis_instruction="Summarize",
        )
    )
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.message_writer.add_room_agent_message = AsyncMock(
        side_effect=[True, False]
    )
    existing_message = SimpleNamespace(
        message_id="message-1:step-1:target-1:message",
        last_notified_state="working",
        message_content=SimpleNamespace(message_text=""),
    )
    executor.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        side_effect=[None, existing_message]
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.PAUSED
    executor.agent_message_processor.process_single_message.assert_not_awaited()
    assert planner.contexts == []
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.WAITING_AGENT


@pytest.mark.asyncio
async def test_run_dispatch_failure_without_message_id_is_visible_to_planner():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="dispatch unhealthy agent",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Try agent",
                )
            ],
        ),
        PlannerAction(
            action=PlannerActionType.FAIL,
            reasoning="fail after visible dispatch error",
            failure_reason="agent unavailable",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_dispatcher.resolve_agent = AsyncMock(return_value=None)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    second_context = planner.contexts[1]
    assert second_context.state_context.agent_outputs[0]["status"] == (
        StepStatus.FAILED.value
    )
    assert "Agent not found" in (
        second_context.state_context.agent_outputs[0]["error"] or ""
    )
    state = await store.get_run("message-1")
    assert state is not None
    assert state.dispatch_intents[0].status == StepStatus.FAILED.value


@pytest.mark.asyncio
async def test_run_synthesis_persists_system_message_content():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Use selected agent",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-1",
                    agent_name="Agent One",
                    task="Handle",
                )
            ],
        ),
        PlannerAction(
            action=PlannerActionType.SYNTHESIZE,
            reasoning="summarize",
            synthesis_instruction="Summarize",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    system_db_msg = _agent_message("sys-message-1")
    executor.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=system_db_msg
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    executor.message_writer.update_room_agent_message_with_new_message_content_by_message_id.assert_awaited_once_with(
        "sys-message-1",
        system_db_msg.message_content,
    )
    assert system_db_msg.message_content.message_text == "Final summary"


@pytest.mark.asyncio
async def test_run_existing_terminal_state_returns_idempotently():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    store = InMemoryOrchestrationRunStore()
    first_executor = _executor(
        store=store,
        planner=RecordingPlanner(
            PlannerAction(
                action=PlannerActionType.COMPLETE,
                reasoning="already done",
            )
        ),
        user_message=user_message,
    )
    state = await store.reconstruct_from_envelope(
        run_id="message-1",
        room_id="room-1",
        user_message_id="message-1",
        envelope=user_message.extend_info,
        goal="Coordinate this",
    )
    state.status = OrchestrationStatus.COMPLETED
    state.terminal_reason = "previously completed"
    await store.create_run(state)

    result = await first_executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert first_executor.orchestration_planner.contexts == []


def _supervisor_agent(agent_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=agent_id,
        agent_card=SimpleNamespace(name=name, description="", skills=[]),
        call_count=0,
        call_success_count=0,
        agent_status=AgentStatus.active,
    )


@pytest.mark.asyncio
async def test_persisted_schema_version_routes_to_run_without_current_flag():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    token = SimpleNamespace(is_cancelled=False)
    supervisor_result = SimpleNamespace(
        status=RunStatus.COMPLETED,
        trajectory=SimpleNamespace(clarify_original_message_id=None),
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message),
        get_quoted_snippet_by_id=AsyncMock(return_value=None),
    )
    rmc.message_writer = SimpleNamespace()
    rmc._turn_event_appender = None
    rmc.delivery = SimpleNamespace(
        get_token=MagicMock(return_value=token),
        create_token=MagicMock(return_value=token),
        remove_token=MagicMock(),
    )
    rmc.room_runtime = SimpleNamespace(
        inquiry_agent_messages_by_related_message_id=AsyncMock(
            side_effect=AssertionError(
                "orchestration envelope should not use queue path"
            )
        )
    )
    rmc.agent_lookup = SimpleNamespace(
        get_agent_by_agent_id=AsyncMock(
            return_value=_supervisor_agent("agent-1", "Agent One")
        )
    )
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(
            return_value=Room(
                room_id="room-1",
                room_name="Room",
                room_owner_id="user-1",
                room_owner_name="User",
                room_agent_set={"agent-1": "Agent One"},
                extend_info={"use_supervisor": True, "debateMode": False},
            )
        )
    )
    rmc.supervisor_executor = SimpleNamespace(
        run=AsyncMock(return_value=supervisor_result),
    )
    rmc.supervisor_planning_error_cls = RuntimeError
    rmc.build_turn_content = None
    rmc._handle_supervisor_run_result = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()

    response = await rmc._process_room_user_message_locked(
        SimpleNamespace(
            room_id="room-1",
            room_user_message_id="message-1",
            user_id="user-1",
            client_request_id="client-1",
        ),
        "room-1",
        "message-1",
    )

    assert response == OrchestrationResponse(
        room_id="room-1",
        success=True,
        error=None,
        status_code=200,
    )
    rmc.supervisor_executor.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestration_initial_run_receives_context_memory():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    token = SimpleNamespace(is_cancelled=False)
    supervisor_result = SimpleNamespace(
        status=RunStatus.COMPLETED,
        trajectory=SimpleNamespace(clarify_original_message_id=None),
    )
    room_memory = SimpleNamespace(memory_content={"turns": []})
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message),
        get_quoted_snippet_by_id=AsyncMock(return_value=None),
    )
    rmc.memory_reader = SimpleNamespace(
        get_room_memory_by_room_id=AsyncMock(return_value=room_memory)
    )
    rmc.message_writer = SimpleNamespace()
    rmc._turn_event_appender = None
    rmc.delivery = SimpleNamespace(
        get_token=MagicMock(return_value=token),
        create_token=MagicMock(return_value=token),
        remove_token=MagicMock(),
    )
    rmc.room_runtime = SimpleNamespace(
        inquiry_agent_messages_by_related_message_id=AsyncMock()
    )
    rmc.agent_lookup = SimpleNamespace(
        get_agent_by_agent_id=AsyncMock(
            return_value=_supervisor_agent("agent-1", "Agent One")
        )
    )
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(
            return_value=Room(
                room_id="room-1",
                room_name="Room",
                room_owner_id="user-1",
                room_owner_name="User",
                room_agent_set={"agent-1": "Agent One"},
                extend_info={"use_supervisor": True, "debateMode": False},
            )
        )
    )
    rmc.context_memory_runtime = SimpleNamespace(
        legacy_search=AsyncMock(return_value={"results": []}),
        assemble_supervisor_context_from_memory=MagicMock(
            return_value=SimpleNamespace(
                metadata={"context": "Context from room memory", "occupancy_pct": 12.5}
            )
        ),
    )
    rmc.supervisor_executor = SimpleNamespace(
        run=AsyncMock(return_value=supervisor_result),
    )
    rmc.supervisor_planning_error_cls = RuntimeError
    rmc.build_turn_content = None
    rmc._handle_supervisor_run_result = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()

    response = await rmc._process_room_user_message_locked(
        SimpleNamespace(
            room_id="room-1",
            room_user_message_id="message-1",
            user_id="user-1",
            client_request_id="client-1",
        ),
        "room-1",
        "message-1",
    )

    assert response.success is True
    run_kwargs = rmc.supervisor_executor.run.await_args.kwargs
    assert run_kwargs["conversation_context"] == "Context from room memory"


def test_orchestration_envelope_requires_candidate_scope():
    assert (
        RoomMessageCenter._is_v2_supervisor_envelope(
            {
                "orchestration_schema_version": 2,
            }
        )
        is False
    )
    assert (
        RoomMessageCenter._is_v2_supervisor_envelope(
            {
                "orchestration": True,
                "orchestration_schema_version": 2,
                "orchestration_run_id": "message-1",
                "candidate_agent_ids": ["agent-1"],
            }
        )
        is True
    )


@pytest.mark.asyncio
async def test_room_message_center_orchestration_result_keeps_user_extend_info_lightweight():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "supervisor_trajectory": {"status": "legacy-running"},
        },
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message)
    )
    rmc.message_writer = SimpleNamespace(
        update_room_user_message_by_message_id=AsyncMock()
    )
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    rmc.delivery = SimpleNamespace(remove_token=MagicMock())
    rmc._emit_processing_status = AsyncMock()

    await rmc._handle_supervisor_run_result(
        SupervisorRunResult(
            status=RunStatus.PAUSED,
            trajectory=SupervisorTrajectory(status="running"),
        ),
        room_id="room-1",
        user_message_id="message-1",
        user_message=user_message,
    )

    assert "supervisor_trajectory" not in user_message.extend_info
    assert user_message.extend_info["orchestration_run_id"] == "message-1"
    assert user_message.extend_info["orchestration_status"] == "running"


@pytest.mark.asyncio
async def test_room_message_center_legacy_trajectory_status_updates_without_payload():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "supervisor_trajectory": {"status": "running", "entries": []},
            "message_notes": "legacy",
        },
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message)
    )
    rmc.message_writer = SimpleNamespace(
        update_room_user_message_by_message_id=AsyncMock()
    )
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    rmc.delivery = SimpleNamespace(remove_token=MagicMock())
    rmc._emit_processing_status = AsyncMock()

    await rmc._handle_supervisor_run_result(
        SupervisorRunResult(
            status=RunStatus.COMPLETED,
            trajectory=None,
        ),
        room_id="room-1",
        user_message_id="message-1",
        user_message=user_message,
    )

    assert user_message.extend_info["supervisor_trajectory"]["status"] == "completed"
    assert user_message.extend_info["orchestration_status"] == "completed"


@pytest.mark.parametrize(
    "run_status,expected_trajectory_status,expected_orchestration_status",
    [
        (
            OrchestrationStatus.BUDGET_EXHAUSTED,
            "failed",
            "budget_exhausted",
        ),
        (
            OrchestrationStatus.AWAITING_USER,
            "awaiting_input",
            "awaiting_user",
        ),
    ],
    ids=["budget_exhausted_to_failed", "awaiting_user_to_awaiting_input"],
)
@pytest.mark.asyncio
async def test_room_message_center_legacy_trajectory_status_maps_orchestration_values(
    run_status,
    expected_trajectory_status,
    expected_orchestration_status,
):
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={"supervisor_trajectory": {"status": "running", "entries": []}},
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message)
    )
    rmc.message_writer = SimpleNamespace(
        update_room_user_message_by_message_id=AsyncMock()
    )
    rmc._run_supervisor_terminal_post_loop_integration = AsyncMock()
    rmc.delivery = SimpleNamespace(remove_token=MagicMock())
    rmc._emit_processing_status = AsyncMock()

    await rmc._handle_supervisor_run_result(
        SupervisorRunResult(
            status=(
                RunStatus.AWAITING_INPUT
                if run_status == OrchestrationStatus.AWAITING_USER
                else RunStatus.FAILED
            ),
            trajectory=None,
            run_state=OrchestrationRunState(
                run_id="run-1",
                room_id="room-1",
                user_message_id="message-1",
                goal="Coordinate this",
                candidate_agent_ids=["agent-1"],
                status=run_status,
            ),
        ),
        room_id="room-1",
        user_message_id="message-1",
        user_message=user_message,
    )

    assert (
        user_message.extend_info["supervisor_trajectory"]["status"]
        == expected_trajectory_status
    )
    assert (
        user_message.extend_info["orchestration_status"]
        == expected_orchestration_status
    )


@pytest.mark.asyncio
async def test_room_message_center_orchestration_resume_preserves_serialized_candidate_registry():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    trajectory = SupervisorTrajectory()
    continuation = {
        "supervisor": True,
        "interrupt_kind": "push_notification",
        "trajectory": trajectory.model_dump(mode="json"),
        "room_id": "room-1",
        "user_message_id": "message-1",
        "message_text": "Coordinate this",
        "agent_registry": [
            {
                "agent_id": "agent-2",
                "agent_name": "Selected External Agent",
                "description": "",
                "skills": [],
                "is_healthy": True,
            }
        ],
        "room_config": {
            "room_agent_set": {"agent-2": "Selected External Agent"},
            "is_debate_mode": False,
        },
        "conversation_context": None,
        "request_user_id": "user-1",
        "quoted_text": None,
    }
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-2"],
        },
    )
    rmc.message_reader = SimpleNamespace(
        get_room_user_message_by_message_id=AsyncMock(return_value=user_message),
    )
    room_memory = SimpleNamespace(room_id="room-1")
    assemble_context = MagicMock(
        return_value=SimpleNamespace(
            metadata={"context": "Context from selected agent", "occupancy_pct": 10.0}
        )
    )
    rmc.memory_reader = SimpleNamespace(
        get_room_memory_by_room_id=AsyncMock(return_value=room_memory)
    )
    rmc.context_memory_runtime = SimpleNamespace(
        legacy_search=AsyncMock(return_value={"results": []}),
        assemble_supervisor_context_from_memory=assemble_context,
    )
    rmc.room_reader = SimpleNamespace(
        get_room_by_room_id=AsyncMock(
            return_value=Room(
                room_id="room-1",
                room_name="Room",
                room_owner_id="user-1",
                room_owner_name="User",
                room_agent_set={"agent-1": "Room Agent"},
                extend_info={"use_supervisor": True, "debateMode": False},
            )
        )
    )
    rmc.agent_lookup = SimpleNamespace(
        get_agent_by_agent_id=AsyncMock(return_value=_supervisor_agent("agent-1", "Room Agent"))
    )
    token = SimpleNamespace(is_cancelled=False)
    rmc.delivery = SimpleNamespace(
        get_token=MagicMock(return_value=token),
        create_token=MagicMock(return_value=token),
        clear_cancellation=MagicMock(),
    )
    supervisor_result = SupervisorRunResult(
        status=RunStatus.PAUSED,
        trajectory=trajectory,
    )
    rmc.supervisor_executor = SimpleNamespace(
        run=AsyncMock(return_value=supervisor_result),
    )
    rmc._handle_supervisor_run_result = AsyncMock()
    rmc._log_room_memory_stats = AsyncMock()
    rmc._notify_all_non_terminal_tasks_failed = AsyncMock()
    rmc._emit_processing_status = AsyncMock()
    rmc._turn_event_appender = None

    result = await rmc._resume_supervisor(
        continuation=continuation,
        paused_message_id="message-1:step-1:target-1:message",
        task_result_text=None,
    )

    assert result == RunStatus.PAUSED
    run_kwargs = rmc.supervisor_executor.run.await_args.kwargs
    assert [agent.agent_id for agent in run_kwargs["agent_registry"]] == ["agent-2"]
    assert run_kwargs["room_config"].room_agent_set == {
        "agent-2": "Selected External Agent"
    }
    assert run_kwargs["conversation_context"] == "Context from selected agent"
    assert assemble_context.call_args.kwargs["agent_registry"] == [
        {"agent_id": "agent-2", "agent_name": "Selected External Agent"}
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("off", False),
        ("", False),
    ],
)
def test_execution_orchestration_v2_flag_parses_legacy_values(raw, expected):
    settings = Settings(_env_file=None, execution_orchestration_v2=raw)

    assert settings.execution_orchestration_v2 is expected


@pytest.mark.asyncio
async def test_adapter_rejection_replans_and_resolves_failure_after_valid_action():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Need coordination"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = SimpleNamespace(
        plan=AsyncMock(
            side_effect=[
                ValueError("planner adapter expected a JSON object"),
                PlannerAction(
                    action=PlannerActionType.DELEGATE,
                    reasoning="Use the selected agent after recovery.",
                    targets=[
                        PlannedDelegateTarget(
                            agent_id="agent-1",
                            task="Handle the request.",
                        )
                    ],
                ),
                PlannerAction(
                    action=PlannerActionType.SYNTHESIZE,
                    reasoning="The agent output supports a final response.",
                    synthesis_instruction="Summarize the agent output.",
                ),
            ]
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Need coordination",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    assert planner.plan.await_count == 3
    assert result.run_state.open_failures[0].source == "planner_validator"
    assert result.run_state.open_failures[0].status == "resolved"


@pytest.mark.asyncio
async def test_state_validation_rejection_replans_with_failure_context():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Need coordination"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Choose an invalid target first.",
            targets=[
                PlannedDelegateTarget(agent_id="agent-2", task="Handle the request.")
            ],
        ),
        PlannerAction(
            action=PlannerActionType.FAIL,
            reasoning="Stop after observing the rejection.",
            failure_reason="test stop",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id=user_message.message_id,
        message_text="Need coordination",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert len(planner.contexts) == 2
    assert planner.contexts[1].state_context.open_failures[0]["error_code"] == (
        "target_out_of_scope"
    )
    assert result.run_state.open_failures[0].status == "resolved"


@pytest.mark.asyncio
async def test_nonrecoverable_adapter_validation_error_is_terminal():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Need coordination"),
    )
    planner = SimpleNamespace(
        plan=AsyncMock(
            side_effect=PlannerActionValidationError(
                "step budget exhausted",
                code="step_budget_exhausted",
                recoverable=False,
            )
        )
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)

    result = await executor.run(
        room_id="room-1",
        user_message_id=user_message.message_id,
        message_text="Need coordination",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    assert result.run_state.status == OrchestrationStatus.BUDGET_EXHAUSTED
    assert result.run_state.open_failures == []
    assert not [
        failure
        for failure in result.run_state.open_failures
        if failure.error_code == "step_budget_exhausted"
    ]


@pytest.mark.asyncio
async def test_input_required_replans_without_user_facing_awaiting_input():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded PDF"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="ask broker",
            targets=[PlannedDelegateTarget(agent_id="agent-1", task="Read input")],
        ),
        PlannerAction(
            action=PlannerActionType.FAIL,
            reasoning="stop after observing failure",
            failure_reason="test stop",
        ),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(store=store, planner=planner, user_message=user_message)
    executor.agent_message_processor.process_single_message = AsyncMock(
        return_value=ProcessingResult(
            ProcessingStatus.AWAITING_INPUT,
            response_text="",
            message_id="agent-msg-1",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
            status_message="Need the selected text projection.",
        )
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Use uploaded PDF",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status != RunStatus.AWAITING_INPUT
    assert result.run_state.open_failures[0].error_code == "agent_input_required"
    assert result.run_state.agent_outputs[0].status == StepStatus.AWAITING_INPUT.value
