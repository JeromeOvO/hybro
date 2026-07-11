from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.config.settings import Settings
from common.utils.time import utcnow
from execution.orchestration import supervisor_executor as supervisor_executor_module
from execution.orchestration.action_validator import (
    PlannerActionValidationError,
    PlannerActionValidator,
)
from execution.orchestration.dispatch_payload import (
    ResolvedDispatchPayload,
    ResolvedResourcePayload,
)
from execution.orchestration.outcome_evaluator import (
    canonical_content_fingerprint,
    goal_fingerprints,
)
from execution.orchestration.outcome_policy import (
    BlockerPolicyValidator,
    evaluate_retry,
)
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
    ActiveDispatchRef,
    AgentOutputRecord,
    BlockerRecord,
    BlockerResolutionAttempt,
    CompletionEvidence,
    DelegationOutcomeRecord,
    DispatchContentRef,
    DispatchExpectedOutput,
    DispatchIntent,
    DispatchRefKind,
    GoalFamilyDispositionRecord,
    OpenFailureRecord,
    OrchestrationEventType,
    OrchestrationRunState,
    OrchestrationStatus,
    PendingAgentContinuation,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
    PlannerQuestion,
    UnknownRecord,
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


def _claimed_continuation() -> PendingAgentContinuation:
    return PendingAgentContinuation(
        continuation_id="cont-1",
        source_intent_id="intent-1",
        source_agent_message_id="agent-msg-1",
        agent_id="agent-1",
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
        status="resuming",
    )


def _executor(
    *,
    store: InMemoryOrchestrationRunStore,
    planner: RecordingPlanner,
    user_message: RoomUserMessage,
    guardrails_enabled: bool | None = None,
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
        guardrails_enabled=guardrails_enabled,
    )
    executor.bind_execution_event_deps(AsyncMock())
    executor._stream_supervisor_synthesis = AsyncMock(return_value="Final summary")
    return executor


def _duplicate_generic_delegate_action() -> PlannerAction:
    return PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Ask the generic producer twice.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Produce the requested structured result.",
                parallel_group="generic-work",
            ),
            PlannedDelegateTarget(
                agent_id="agent-1",
                task="Produce the requested structured result again.",
                parallel_group="generic-work",
            ),
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guardrails_enabled", "expected_dispatch_count"),
    [(False, 2), (True, 0)],
)
async def test_injected_outcome_guardrails_atomically_control_duplicate_delegate_enforcement(
    monkeypatch,
    caplog,
    guardrails_enabled: bool,
    expected_dispatch_count: int,
):
    """Shadow mode records generic-agent outcomes without rejecting the action."""
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate generic work."),
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
        planner=RecordingPlanner(
            _duplicate_generic_delegate_action(),
            PlannerAction(
                action=PlannerActionType.FAIL,
                reasoning="End the regression fixture.",
                failure_reason="fixture complete",
            ),
        ),
        user_message=user_message,
        guardrails_enabled=guardrails_enabled,
    )
    monkeypatch.setattr(
        supervisor_executor_module._settings,
        "orchestration_outcome_guardrails",
        not guardrails_enabled,
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate generic work.",
        agent_registry=[
            AgentProfile(agent_id="agent-1", agent_name="Generic Producer"),
            AgentProfile(agent_id="agent-2", agent_name="Generic Consumer"),
        ],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.FAILED
    assert len(result.run_state.dispatch_intents) == expected_dispatch_count
    if guardrails_enabled:
        assert not result.run_state.delegation_outcomes
        assert "orchestration_delegate_retry_rejected" in caplog.text
    else:
        assert len(result.run_state.delegation_outcomes) == 2
        assert "orchestration_delegate_outcome_evaluated" in caplog.text


def test_generic_agents_cover_conditional_no_progress_and_failed_retry_contracts():
    producer = "generic-producer"
    consumer = "generic-consumer"
    family = "generic-family"
    revision = "generic-revision"
    first_intent = DispatchIntent(
        step_id="step-1",
        step_target_id="target-1",
        dispatch_intent_id="intent-1",
        planned_agent_message_id="producer-msg-1",
        agent_id=producer,
        task="Produce a structured generic artifact.",
        task_hash="producer-task",
        status="completed",
    )
    repeated_intent = first_intent.model_copy(
        update={
            "step_id": "step-2",
            "step_target_id": "target-2",
            "dispatch_intent_id": "intent-2",
            "planned_agent_message_id": "producer-msg-2",
            "status": "completed",
            "repair_of_intent_id": "intent-1",
        }
    )
    partial = DelegationOutcomeRecord(
        outcome_id="outcome-1",
        dispatch_intent_id="intent-1",
        agent_id=producer,
        goal_family_fingerprint=family,
        goal_revision_fingerprint=revision,
        attempt_fingerprint="producer-attempt-1",
        status="partial",
        remaining_required_obligations=["generic:required"],
        newly_satisfied_required_obligations=["generic:artifact"],
        unknowns=[
            UnknownRecord(
                key="generic:unknown",
                description="The remaining generic field is unknown.",
                source_agent_message_id="producer-msg-1",
            )
        ],
    )
    repeated = partial.model_copy(
        update={
            "outcome_id": "outcome-2",
            "dispatch_intent_id": "intent-2",
            "attempt_fingerprint": "producer-attempt-2",
            "status": "no_progress",
            "newly_satisfied_required_obligations": [],
        }
    )
    state = _run_state(
        candidate_agent_ids=[producer, consumer],
        dispatch_intents=[first_intent, repeated_intent],
        delegation_outcomes=[partial, repeated],
        facts=[
            {
                "fact_id": "consumer-msg-1:conditional",
                "source_agent_id": consumer,
                "kind": "conditional_result",
                "text": "The partial artifact is conditionally usable.",
            }
        ],
    )

    assert state.delegation_outcomes[0].unknowns[0].key == "generic:unknown"
    assert state.facts[0]["source_agent_id"] == consumer
    no_progress = evaluate_retry(
        state,
        PlannedDelegateTarget(
            agent_id=producer,
            task=first_intent.task,
            repair_of_intent_id="intent-2",
        ),
        goal_family_fingerprint=family,
        goal_revision_fingerprint=revision,
    )
    assert no_progress.code == "delegate_no_progress_repeat"

    failed_state = _run_state(
        candidate_agent_ids=[producer, consumer],
        dispatch_intents=[first_intent.model_copy(update={"status": "failed"})],
        delegation_outcomes=[
            partial.model_copy(
                update={
                    "status": "failed",
                    "newly_satisfied_required_obligations": [],
                }
            )
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="transport-failure",
                fingerprint="generic-transport",
                source="runtime",
                agent_id=producer,
                agent_message_id="producer-msg-1",
                dispatch_intent_id="intent-1",
                error_code="transport_error",
                error_message="Connection reset.",
                recoverable=True,
            )
        ],
    )
    retry = evaluate_retry(
        failed_state,
        PlannedDelegateTarget(agent_id=producer, task=first_intent.task),
        goal_family_fingerprint=family,
        goal_revision_fingerprint=revision,
    )
    alternate = evaluate_retry(
        state,
        PlannedDelegateTarget(agent_id=consumer, task=first_intent.task),
        goal_family_fingerprint=family,
        goal_revision_fingerprint=revision,
    )

    assert retry.allowed is True
    assert retry.kind == "operational_retry"
    assert alternate.kind == "alternate_agent"
    assert alternate.allowed is True


def test_generic_user_only_blocker_allows_one_hitl_and_rejects_fulfilled_repeat():
    producer = "generic-producer"
    consumer = "generic-consumer"
    target = PlannedDelegateTarget(agent_id=producer, task="Produce a generic result.")
    fingerprints = PlannerActionValidator._target_goal_fingerprints(target, {})
    blocker = BlockerRecord(
        key="generic:missing-user-value",
        description="Only the user can provide the missing value.",
        blocked_output_keys=["generic-result"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        resolution_attempts=[
            BlockerResolutionAttempt(
                kind="resource",
                reference_id="generic-resource",
                outcome="unavailable",
                applies_to_output_keys=["generic-result"],
            ),
            BlockerResolutionAttempt(
                kind="agent",
                reference_id=consumer,
                outcome="failed",
                applies_to_output_keys=["generic-result"],
            ),
            BlockerResolutionAttempt(
                kind="conditional_result",
                reference_id="generic-result",
                outcome="insufficient",
                applies_to_output_keys=["generic-result"],
            ),
        ],
    )
    assert BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"generic-result"},
        available_resource_refs={"generic-resource"},
        eligible_alternate_agent_ids={consumer},
        conditional_result_viable=False,
    ).code == "blocker_user_only_validated"

    ask_user = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="Request the one remaining user value.",
        questions=[
            PlannerQuestion(
                prompt="Provide the missing generic value.",
                reason="blocker",
                blocker_keys=[blocker.key],
            )
        ],
    )
    blocked_state = _run_state(
        candidate_agent_ids=[producer, consumer],
        blockers=[blocker],
    )
    assert PlannerActionValidator.validate(
        ask_user,
        run_state=blocked_state,
        guardrails_enabled=True,
    ) is ask_user
    assert len(ask_user.questions) == 1

    fulfilled_state = _run_state(
        candidate_agent_ids=[producer, consumer],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="fulfilled-outcome",
                dispatch_intent_id="fulfilled-intent",
                agent_id=producer,
                goal_family_fingerprint=fingerprints.goal_family_fingerprint,
                goal_revision_fingerprint=fingerprints.goal_revision_fingerprint,
                attempt_fingerprint="fulfilled-attempt",
                status="fulfilled",
            )
        ],
    )
    with pytest.raises(PlannerActionValidationError) as error:
        PlannerActionValidator.validate(
            PlannerAction(
                action=PlannerActionType.DELEGATE,
                reasoning="Repeat the fulfilled generic result.",
                targets=[target],
            ),
            run_state=fulfilled_state,
            guardrails_enabled=True,
        )
    assert error.value.code == "delegate_goal_already_fulfilled"


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
    resource_fingerprints=None,
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
        selected_resource_fingerprints=sorted(
            {
                (resource_fingerprints or {})[ref.ref_id]
                for ref in (
                    *target.context_refs,
                    *target.artifact_refs,
                    *target.attachment_refs,
                )
                if ref.ref_id in (resource_fingerprints or {})
            }
        ),
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
@pytest.mark.parametrize(
    ("interactive_state", "requires_auth", "requires_policy"),
    [
        ("auth-required", True, False),
        ("policy-required", False, True),
    ],
)
async def test_run_awaiting_input_status_is_not_persisted_without_hitl_request_ids(
    monkeypatch,
    interactive_state: str,
    requires_auth: bool,
    requires_policy: bool,
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
                status_message="Please complete the requested action.",
                interactive_state=interactive_state,
                requires_auth=requires_auth,
                requires_policy=requires_policy,
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
            interactive_state="auth-required",
            requires_auth=True,
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
            interactive_state="auth-required",
            requires_auth=True,
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
            interactive_state="auth-required",
            requires_auth=True,
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
            interactive_state="auth-required",
            requires_auth=True,
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
                interactive_state="auth-required",
                requires_auth=True,
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
                interactive_state="auth-required",
                requires_auth=True,
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
                        interactive_state="auth-required",
                        requires_auth=True,
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
                        interactive_state="auth-required",
                        requires_auth=True,
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
                        interactive_state="auth-required",
                        requires_auth=True,
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
                        interactive_state="auth-required",
                        requires_auth=True,
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
    assert any(
        event.type == OrchestrationEventType.OUTCOME_EVALUATED for event in events
    )


@pytest.mark.asyncio
async def test_agent_hitl_resume_persists_outcomes_for_terminal_and_awaiting_results():
    user_message = _state_unification_user_message(message_id="message-1")
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
        guardrails_enabled=True,
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
                        a2a_task_id="task-2",
                        a2a_context_id="context-2",
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

    assert blocking_status == RunStatus.PAUSED
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
        guardrails_enabled=True,
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
                        a2a_task_id="task-1",
                        a2a_context_id="context-1",
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

    assert blocking_status == RunStatus.PAUSED
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
        guardrails_enabled=True,
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
                        a2a_task_id="task-1",
                        a2a_context_id="context-1",
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

    assert blocking_status == RunStatus.PAUSED
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
                        interactive_state="auth-required",
                        requires_auth=True,
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
            interactive_state="auth-required",
            requires_auth=True,
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
async def test_recover_v2_inflight_dispatch_ingests_plain_a2a_input_required_for_planner():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
        guardrails_enabled=True,
    )
    executor._run_agent_awaiting_input_action = AsyncMock()

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
            text="Needs an available resource",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
            status_message="Provide the selected resource.",
            interactive_state="input-required",
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

    executor._run_agent_awaiting_input_action.assert_not_awaited()
    assert run_status is None
    assert recovered_state.status == OrchestrationStatus.RUNNING
    assert recovered_state.agent_outputs[0].status == StepStatus.AWAITING_INPUT.value
    assert recovered_state.open_failures[0].error_code == "agent_input_required"


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
        guardrails_enabled=False,
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

    assert run_status is None
    assert recovered_state.status == OrchestrationStatus.RUNNING
    assert recovered_state.pending_hitl_request_ids == []
    executor.hitl_coordinator.request_input.assert_not_awaited()
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
async def test_recover_v2_inflight_dispatch_rehydrates_a2a_task_fields_for_auth_required():
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
    executor.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            message_id="message-1:step-1:target-1:message",
            message_content=SimpleNamespace(
                message_text="",
                message_task={
                    "id": "task-from-task",
                    "context_id": "ctx-from-task",
                    "status": {
                        "state": "auth-required",
                        "message": {
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": "Please provide missing details.",
                                }
                            ]
                        },
                    },
                },
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
    executor.hitl_coordinator.request_input.assert_awaited_once()
    request_input_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert request_input_kwargs["a2a_task_id"] == "task-from-task"
    assert request_input_kwargs["a2a_context_id"] == "ctx-from-task"
    assert request_input_kwargs["prompt"] == "Please provide missing details."
    recovered_output = recovered_state.agent_outputs[0]
    assert recovered_output.a2a_task_id == "task-from-task"
    assert recovered_output.a2a_context_id == "ctx-from-task"
    assert recovered_output.status_message == "Please provide missing details."


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
            interactive_state="auth-required",
            requires_auth=True,
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
            interactive_state="auth-required",
            requires_auth=True,
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
                        interactive_state="auth-required",
                        requires_auth=True,
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
                        interactive_state="auth-required",
                        requires_auth=True,
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
async def test_resource_backed_outcome_blocks_matching_delegate_revision():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Review the projected resource"),
    )
    resource_ref = DispatchContentRef(
        kind=DispatchRefKind.CONTEXT,
        ref_id="ctx:file-1:text",
        source_agent_message_id="source-msg-1",
    )
    target = PlannedDelegateTarget(
        agent_id="agent-1",
        task="Review the projected resource",
        context_refs=[resource_ref],
    )
    intent = DispatchIntent(
        step_id="step-1",
        step_target_id="step-1:target-1",
        dispatch_intent_id="intent-1",
        planned_agent_message_id="agent-msg-1",
        agent_id="agent-1",
        task=target.task,
        task_hash="hash-1",
        context_refs=[resource_ref],
        selected_resource_fingerprints=["resource-fingerprint-1"],
    )
    store = InMemoryOrchestrationRunStore()
    state = _run_state(dispatch_intents=[intent])
    await store.create_run(state)
    executor = _executor(store=store, planner=RecordingPlanner(), user_message=user_message)

    persisted = await executor._ingest_v2_results(
        state,
        [
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="Agent One",
                task=target.task,
                response_text="Reviewed",
                success=True,
                status=StepStatus.SUCCESS,
                agent_message_id="agent-msg-1",
            )
        ],
        status=OrchestrationStatus.RUNNING,
        advance_step=True,
    )

    assert len(persisted.delegation_outcomes) == 1
    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            PlannerAction(
                action=PlannerActionType.DELEGATE,
                reasoning="Repeat the selected-resource review",
                targets=[target],
            ),
            run_state=persisted,
            resource_fingerprints={resource_ref.ref_id: "resource-fingerprint-1"},
            guardrails_enabled=True,
        )

    assert exc_info.value.code == "delegate_goal_already_fulfilled"


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
async def test_goal_family_disposition_terminalizes_related_work_and_records_event():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="produce quote",
                task_hash="hash-1",
                status="planned",
            )
        ],
        active_dispatches=[
            ActiveDispatchRef(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="running",
            )
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-1",
                fingerprint="failure-fingerprint-1",
                source="runtime",
                dispatch_intent_id="intent-1",
                error_code="transport_error",
                error_message="Connection reset.",
                recoverable=True,
            )
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="failed",
            )
        ],
    )
    await store.create_run(state)

    saved = await executor._dispose_v2_goal_family(
        await store.get_run("run-1"),
        goal_family_fingerprint="family-1",
        through_goal_revision_fingerprint="revision-1",
        status="abandoned",
        reason="The user withdrew the request.",
        replacement_goal_family_fingerprint="family-2",
    )

    assert saved.dispatch_intents[0].status == "abandoned"
    assert saved.active_dispatches[0].status == "abandoned"
    assert saved.open_failures[0].status == "abandoned"
    assert saved.goal_family_dispositions == [
        GoalFamilyDispositionRecord(
            event_id=saved.goal_family_dispositions[0].event_id,
            goal_family_fingerprint="family-1",
            through_goal_revision_fingerprint="revision-1",
            status="abandoned",
            reason="The user withdrew the request.",
            replacement_goal_family_fingerprint="family-2",
        )
    ]
    event = store._events_by_run["run-1"][-1]
    assert event.type == OrchestrationEventType.GOAL_FAMILY_DISPOSED
    assert event.payload["event_id"] == saved.goal_family_dispositions[0].event_id


@pytest.mark.asyncio
async def test_goal_family_disposition_requires_nonempty_reason():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    await store.create_run(_run_state())

    with pytest.raises(ValueError, match="reason"):
        await executor._dispose_v2_goal_family(
            await store.get_run("run-1"),
            goal_family_fingerprint="family-1",
            through_goal_revision_fingerprint="revision-1",
            status="abandoned",
            reason=" ",
        )


@pytest.mark.asyncio
async def test_goal_family_disposition_covers_inflight_same_revision_repair():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="partial",
            ),
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Produce the quote.",
                task_hash="task-hash-1",
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Repair the quote.",
                task_hash="task-hash-2",
                repair_of_intent_id="intent-1",
            ),
        ],
        active_dispatches=[
            ActiveDispatchRef(
                agent_message_id="agent-msg-2", agent_id="agent-1", status="running"
            )
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-2",
                fingerprint="failure-fingerprint-2",
                source="runtime",
                dispatch_intent_id="intent-2",
                agent_id="agent-1",
                agent_message_id="agent-msg-2",
                error_code="transport_error",
                error_message="Connection reset.",
                recoverable=True,
                status="open",
            )
        ],
    )
    await store.create_run(state)

    saved = await executor._dispose_v2_goal_family(
        await store.get_run("run-1"),
        goal_family_fingerprint="family-1",
        through_goal_revision_fingerprint="revision-1",
        status="abandoned",
        reason="The user withdrew the request.",
    )

    assert [intent.status for intent in saved.dispatch_intents] == [
        "abandoned",
        "abandoned",
    ]
    assert saved.active_dispatches[0].status == "abandoned"
    assert saved.open_failures[0].status == "abandoned"


@pytest.mark.asyncio
async def test_goal_family_disposition_preserves_inflight_later_revision_repair():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="partial",
            )
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Produce the quote.",
                task_hash="task-hash-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Repair the original quote.",
                task_hash="task-hash-2",
                repair_of_intent_id="intent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
            ),
            DispatchIntent(
                step_id="step-3",
                step_target_id="step-3:target-1",
                dispatch_intent_id="intent-3",
                planned_agent_message_id="agent-msg-3",
                agent_id="agent-1",
                task="Repair the revised quote.",
                task_hash="task-hash-3",
                repair_of_intent_id="intent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-2",
            ),
        ],
        active_dispatches=[
            ActiveDispatchRef(
                agent_message_id="agent-msg-2", agent_id="agent-1", status="running"
            ),
            ActiveDispatchRef(
                agent_message_id="agent-msg-3", agent_id="agent-1", status="running"
            ),
        ],
    )
    await store.create_run(state)

    saved = await executor._dispose_v2_goal_family(
        await store.get_run("run-1"),
        goal_family_fingerprint="family-1",
        through_goal_revision_fingerprint="revision-1",
        status="abandoned",
        reason="The original revision is no longer needed.",
    )

    assert [intent.status for intent in saved.dispatch_intents] == [
        "abandoned",
        "abandoned",
        "planned",
    ]
    assert [dispatch.status for dispatch in saved.active_dispatches] == [
        "abandoned",
        "running",
    ]


@pytest.mark.asyncio
async def test_goal_family_disposition_does_not_terminalize_later_revision_repair():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="partial",
            ),
            DelegationOutcomeRecord(
                outcome_id="outcome-2",
                dispatch_intent_id="intent-2",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-2",
                attempt_fingerprint="attempt-2",
                status="partial",
            ),
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Produce the quote.",
                task_hash="task-hash-1",
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Repair the quote for the revised goal.",
                task_hash="task-hash-2",
                repair_of_intent_id="intent-1",
            ),
        ],
        active_dispatches=[
            ActiveDispatchRef(
                agent_message_id="agent-msg-2", agent_id="agent-1", status="running"
            )
        ],
    )
    await store.create_run(state)

    saved = await executor._dispose_v2_goal_family(
        await store.get_run("run-1"),
        goal_family_fingerprint="family-1",
        through_goal_revision_fingerprint="revision-1",
        status="abandoned",
        reason="The original revision is no longer needed.",
    )

    assert saved.dispatch_intents[0].status == "abandoned"
    assert saved.dispatch_intents[1].status == "planned"
    assert saved.active_dispatches[0].status == "running"


@pytest.mark.asyncio
async def test_goal_family_disposition_through_revision_terminalizes_earlier_work():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="partial",
            ),
            DelegationOutcomeRecord(
                outcome_id="outcome-2",
                dispatch_intent_id="intent-2",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-2",
                attempt_fingerprint="attempt-2",
                status="partial",
            ),
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Produce the original quote.",
                task_hash="task-hash-1",
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="step-2:target-1",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Produce the revised quote.",
                task_hash="task-hash-2",
            ),
        ],
        active_dispatches=[
            ActiveDispatchRef(
                agent_message_id="agent-msg-1", agent_id="agent-1", status="running"
            )
        ],
    )
    await store.create_run(state)

    saved = await executor._dispose_v2_goal_family(
        await store.get_run("run-1"),
        goal_family_fingerprint="family-1",
        through_goal_revision_fingerprint="revision-2",
        status="abandoned",
        reason="The full goal family is no longer needed.",
    )

    assert saved.dispatch_intents[0].status == "abandoned"
    assert saved.active_dispatches[0].status == "abandoned"


@pytest.mark.asyncio
async def test_run_complete_creates_referenced_goal_family_disposition_before_terminalizing(
    monkeypatch,
):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Complete the active work"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        candidate_agent_ids=["agent-1"],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                text="The answer is ready.",
            )
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                attempt_fingerprint="attempt-1",
                status="partial",
                remaining_required_obligations=["quote"],
            )
        ],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="step-1:target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Produce the quote.",
                task_hash="task-hash-1",
            )
        ],
    )
    store = InMemoryOrchestrationRunStore()
    await store.create_run(state)
    executor = _executor(
        store=store,
        planner=RecordingPlanner(
            PlannerAction(
                action=PlannerActionType.COMPLETE,
                reasoning="The remaining goal family was abandoned.",
                completion_evidence=CompletionEvidence(
                    satisfied_criteria=["The completed output is sufficient."],
                    referenced_fact_ids=[],
                    referenced_artifact_keys=[],
                    unresolved_questions=[],
                    final_answer_intent="answer_user",
                    confidence=0.9,
                    abandoned_goal_disposition_event_ids=["dispose-1"],
                    requested_goal_family_dispositions=[
                        {
                            "event_id": "dispose-1",
                            "goal_family_fingerprint": "family-1",
                            "through_goal_revision_fingerprint": "revision-1",
                            "status": "abandoned",
                            "reason": "The user no longer needs this quote.",
                            "replacement_goal_family_fingerprint": "family-2",
                        }
                    ],
                ),
            )
        ),
        user_message=user_message,
    )
    monkeypatch.setattr(
        supervisor_executor_module._settings,
        "orchestration_outcome_guardrails",
        True,
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Complete the active work",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    saved = await store.get_run("message-1")
    assert saved is not None
    assert saved.dispatch_intents[0].status == "abandoned"
    assert saved.goal_family_dispositions[0].event_id == "dispose-1"
    assert (
        saved.goal_family_dispositions[0].replacement_goal_family_fingerprint
        == "family-2"
    )
    event_types = [event.type for event in store._events_by_run["message-1"]]
    assert OrchestrationEventType.GOAL_FAMILY_DISPOSED in event_types
    assert event_types.index(OrchestrationEventType.GOAL_FAMILY_DISPOSED) < event_types.index(
        OrchestrationEventType.RUN_TERMINAL
    )


@pytest.mark.asyncio
async def test_run_complete_ignores_unknown_disposition_when_guardrails_disabled(
    monkeypatch,
):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Complete the work"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "message-1",
            "candidate_agent_ids": ["agent-1"],
            "client_request_id": "client-1",
        },
    )
    state = _run_state(
        run_id="message-1",
        user_message_id="message-1",
        candidate_agent_ids=["agent-1"],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status="completed",
                text="The answer is ready.",
            )
        ],
    )
    store = InMemoryOrchestrationRunStore()
    await store.create_run(state)
    executor = _executor(
        store=store,
        planner=RecordingPlanner(
            PlannerAction(
                action=PlannerActionType.COMPLETE,
                reasoning="The answer is ready.",
                completion_evidence=CompletionEvidence(
                    satisfied_criteria=["answer_ready"],
                    referenced_fact_ids=[],
                    referenced_artifact_keys=[],
                    unresolved_questions=[],
                    final_answer_intent="answer_user",
                    confidence=0.9,
                    abandoned_goal_disposition_event_ids=["unknown-disposition"],
                ),
            )
        ),
        user_message=user_message,
    )
    monkeypatch.setattr(
        supervisor_executor_module._settings,
        "orchestration_outcome_guardrails",
        False,
    )

    result = await executor.run(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Complete the work",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )

    assert result.status == RunStatus.COMPLETED
    saved = await store.get_run("message-1")
    assert saved is not None
    assert saved.goal_family_dispositions == []
    assert all(
        event.type != OrchestrationEventType.GOAL_FAMILY_DISPOSED
        for event in store._events_by_run["message-1"]
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
    assert len(planner.contexts) == 2
    assert (
        planner.contexts[1].state_context.open_failures[0]["error_code"]
        == "agent_input_required"
    )


@pytest.mark.parametrize(
    ("interactive_state", "requires_auth", "requires_policy"),
    [
        ("auth-required", True, False),
        ("policy-required", False, True),
    ],
)
def test_awaiting_result_requires_hitl_from_structured_metadata(
    interactive_state: str,
    requires_auth: bool,
    requires_policy: bool,
):
    result = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="Handle the request",
        response_text="",
        success=False,
        status=StepStatus.AWAITING_INPUT,
        status_message="Please complete the requested action.",
        interactive_state=interactive_state,
        requires_auth=requires_auth,
        requires_policy=requires_policy,
    )

    assert SupervisorExecutor._awaiting_result_requires_hitl(result) is True


def test_plain_a2a_input_required_is_recoverable_only_with_task_ownership():
    result = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="Handle the request",
        response_text="",
        success=False,
        status=StepStatus.AWAITING_INPUT,
        interactive_state="input-required",
        a2a_task_id="task-1",
        a2a_context_id="context-1",
    )

    assert SupervisorExecutor._awaiting_result_requires_hitl(result) is False
    assert (
        SupervisorExecutor._awaiting_result_requires_hitl(
            result.model_copy(update={"a2a_task_id": None})
        )
        is True
    )
    assert (
        SupervisorExecutor._awaiting_result_requires_hitl(
            result.model_copy(update={"interactive_state": None})
        )
        is False
    )


@pytest.mark.asyncio
async def test_same_agent_retry_continues_existing_input_required_task():
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
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    reply_to_task = AsyncMock(
        return_value={
            "blocking": True,
            "task_state": "completed",
            "response_text": "Recovered answer",
        }
    )
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(reply_to_task=reply_to_task)
    )
    state = _run_state(
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status=StepStatus.AWAITING_INPUT.value,
                a2a_task_id="task-1",
                a2a_context_id="ctx-1",
                status_message="Need projection",
            )
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-1",
                fingerprint="agent-1:agent-msg-1:agent_input_required",
                source="a2a_adapter",
                agent_id="agent-1",
                agent_message_id="agent-msg-1",
                error_code="agent_input_required",
                error_message="Need projection",
                recoverable=True,
                recovery_hints=["retry_with_available_resource_refs"],
            )
        ],
    )
    resolved_payload = ResolvedDispatchPayload(
        selected_context_refs=["ctx:file-file-1:text"],
        resource_payloads=[
            ResolvedResourcePayload(
                ref_id="ctx:file-file-1:text",
                kind="context",
                mime_type="text/plain",
                text="Projected input",
            )
        ],
    )

    result = await executor._continue_agent_task_with_resolved_refs(
        claimed_continuation=_claimed_continuation(),
        awaiting_output=state.agent_outputs[0],
        target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="retry"),
        resolved_payload=resolved_payload,
    )

    assert result.status == StepStatus.SUCCESS
    assert result.response_text == "Recovered answer"
    reply_to_task.assert_awaited_once_with(
        message_id="agent-msg-1",
        task_id="task-1",
        context_id="ctx-1",
        user_input="Projected input",
    )


@pytest.mark.asyncio
async def test_unclaimed_continuation_does_not_call_remote_reply():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded PDF"),
    )
    executor = _executor(
        store=InMemoryOrchestrationRunStore(),
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    reply_to_task = AsyncMock(
        return_value={"blocking": True, "task_state": "completed", "response_text": "Done"}
    )
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(reply_to_task=reply_to_task)
    )

    result = await executor._continue_agent_task_with_resolved_refs(
        claimed_continuation=None,
        awaiting_output=AgentOutputRecord(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
        ),
        target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="retry"),
        resolved_payload=ResolvedDispatchPayload(
            resource_payloads=[
                ResolvedResourcePayload(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    mime_type="text/plain",
                    text="Projected input",
                )
            ]
        ),
    )

    assert result is None
    reply_to_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_agent_retry_that_still_needs_input_requires_hitl():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded PDF"),
    )
    executor = _executor(
        store=InMemoryOrchestrationRunStore(),
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(
            reply_to_task=AsyncMock(
                return_value={
                    "blocking": True,
                    "task_state": "input-required",
                    "response_text": "Still need the broker submission pack.",
                }
            )
        )
    )
    result = await executor._continue_agent_task_with_resolved_refs(
        claimed_continuation=_claimed_continuation(),
        awaiting_output=AgentOutputRecord(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
        ),
        target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="retry"),
        resolved_payload=ResolvedDispatchPayload(
            selected_context_refs=["ctx:file-file-1:text"],
            resource_payloads=[
                ResolvedResourcePayload(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    mime_type="text/plain",
                    text="Projected input",
                )
            ],
        ),
    )

    assert result is not None
    assert result.status == StepStatus.AWAITING_INPUT
    assert result.paused_message_id == "agent-msg-1"
    assert result.status_message == "Still need the broker submission pack."
    assert SupervisorExecutor._awaiting_result_requires_hitl(result) is False


def test_awaiting_result_requires_hitl_only_for_auth_or_policy():
    plain_input_required = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="Need a resource",
        response_text="",
        status=StepStatus.AWAITING_INPUT,
        interactive_state="input-required",
        a2a_task_id="task-1",
        a2a_context_id="context-1",
    )
    auth_required = plain_input_required.model_copy(
        update={"interactive_state": "auth-required", "requires_auth": True}
    )
    policy_required = plain_input_required.model_copy(
        update={"interactive_state": "policy-required", "requires_policy": True}
    )

    assert SupervisorExecutor._awaiting_result_requires_hitl(plain_input_required) is False
    assert SupervisorExecutor._awaiting_result_requires_hitl(auth_required) is True
    assert SupervisorExecutor._awaiting_result_requires_hitl(policy_required) is True


@pytest.mark.asyncio
async def test_persisted_continuation_claim_allows_one_remote_reply_under_race():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded PDF"),
    )
    store = InMemoryOrchestrationRunStore()
    state = _run_state(
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="cont-1",
                source_intent_id="intent-1",
                source_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1",
                a2a_context_id="context-1",
            )
        ]
    )
    await store.create_run(state)
    executor = _executor(store=store, planner=RecordingPlanner(), user_message=user_message)
    target = PlannedDelegateTarget(
        agent_id="agent-1",
        task="Continue with projected resource",
        repair_of_intent_id="intent-1",
    )

    claims = await asyncio.gather(
        executor._claim_matching_continuation(
            state=state,
            target=target,
            goal_family_fingerprint="family-1",
            selected_resource_fingerprints={"resource-new"},
        ),
        executor._claim_matching_continuation(
            state=state,
            target=target,
            goal_family_fingerprint="family-1",
            selected_resource_fingerprints={"resource-new"},
        ),
    )

    assert sum(claim is not None for claim in claims) == 1
    persisted = await store.get_run("run-1")
    assert persisted is not None
    assert persisted.pending_agent_continuations[0].status == "resuming"


@pytest.mark.asyncio
async def test_concurrent_delegate_recovery_claims_before_one_remote_reply():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use the selected resource"),
    )
    resource = ResolvedResourcePayload(
        ref_id="ctx:file-1:text",
        kind="context",
        mime_type="text/plain",
        text="Projected input",
    )
    resource_fingerprints = {
        canonical_content_fingerprint(resource.model_dump(mode="json"))
    }
    goal_family_fingerprint = goal_fingerprints(
        agent_id="agent-1",
        expected_outputs=[],
        selected_content_fingerprints=list(resource_fingerprints),
        dependency_family_fingerprints=[],
        upstream_output_fingerprints=[],
    ).goal_family_fingerprint
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Initial task",
                task_hash="hash-1",
                status="awaiting_input",
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="target-2",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="repair-msg",
                agent_id="agent-1",
                task="Use the selected resource",
                task_hash="hash-2",
                repair_of_intent_id="intent-1",
            ),
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status=StepStatus.AWAITING_INPUT.value,
                a2a_task_id="task-1",
                a2a_context_id="context-1",
            )
        ],
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="cont-1",
                source_intent_id="intent-1",
                source_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                goal_family_fingerprint=goal_family_fingerprint,
                goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1",
                a2a_context_id="context-1",
            )
        ],
    )
    store = InMemoryOrchestrationRunStore()
    await store.create_run(state)
    executor = _executor(store=store, planner=RecordingPlanner(), user_message=user_message)
    executor.orchestration_resource_provider = SimpleNamespace(
        resolve_ref=AsyncMock(return_value=resource)
    )
    reply_to_task = AsyncMock(
        return_value={"blocking": True, "task_state": "completed", "response_text": "Done"}
    )
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(reply_to_task=reply_to_task)
    )
    target = DelegateTarget(
        agent_id="agent-1",
        agent_name="Agent One",
        task="Use the selected resource",
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="ctx:file-1:text",
                source_agent_message_id="source-msg-1",
                mime_type="text/plain",
            )
        ],
    )

    await asyncio.gather(
        executor._dispatch_targets(
            targets=[target], agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
            room_id="room-1", user_message_id="message-1", step_number=2, token=None,
            request_user_id="user-1", quoted_text=None, planned_message_ids=["repair-msg"],
            run_state=state, original_attachments=[],
        ),
        executor._dispatch_targets(
            targets=[target], agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
            room_id="room-1", user_message_id="message-1", step_number=2, token=None,
            request_user_id="user-1", quoted_text=None, planned_message_ids=["repair-msg"],
            run_state=state, original_attachments=[],
        ),
    )

    reply_to_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_input_required_reopens_persisted_continuation():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded PDF"),
    )
    store = InMemoryOrchestrationRunStore()
    state = _run_state(
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="cont-1",
                source_intent_id="intent-1",
                source_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1",
                a2a_context_id="context-1",
                status="resuming",
            )
        ]
    )
    await store.create_run(state)
    executor = _executor(store=store, planner=RecordingPlanner(), user_message=user_message)

    saved = await executor._reconcile_persisted_continuation(
        state=state,
        continuation_id="cont-1",
        status="open",
    )

    assert saved.pending_agent_continuations[0].status == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_result", [
    {"task_state": "failed", "response_text": "Unable to continue"},
])
async def test_failed_continuation_reply_reopens_persisted_claim(reply_result):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Continue"),
    )
    store = InMemoryOrchestrationRunStore()
    state = _run_state(pending_agent_continuations=[_claimed_continuation()])
    await store.create_run(state)
    executor = _executor(store=store, planner=RecordingPlanner(), user_message=user_message)
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(reply_to_task=AsyncMock(return_value=reply_result))
    )

    result = await executor._continue_agent_task_with_resolved_refs(
        claimed_continuation=_claimed_continuation(),
        continuation_state=state,
        awaiting_output=AgentOutputRecord(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
        ),
        target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="retry"),
        resolved_payload=ResolvedDispatchPayload(
            resource_payloads=[
                ResolvedResourcePayload(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    mime_type="text/plain",
                    text="Projected input",
                )
            ]
        ),
    )

    assert result is not None
    assert result.status == StepStatus.FAILED
    persisted = await store.get_run("run-1")
    assert persisted is not None
    assert persisted.pending_agent_continuations[0].status == "open"


@pytest.mark.asyncio
async def test_exceptional_continuation_reply_reopens_persisted_claim():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Continue"),
    )
    store = InMemoryOrchestrationRunStore()
    state = _run_state(pending_agent_continuations=[_claimed_continuation()])
    await store.create_run(state)
    executor = _executor(store=store, planner=RecordingPlanner(), user_message=user_message)
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(reply_to_task=AsyncMock(side_effect=RuntimeError("remote unavailable")))
    )

    with pytest.raises(RuntimeError, match="remote unavailable"):
        await executor._continue_agent_task_with_resolved_refs(
            claimed_continuation=_claimed_continuation(),
            continuation_state=state,
            awaiting_output=AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status=StepStatus.AWAITING_INPUT.value,
                a2a_task_id="task-1",
                a2a_context_id="ctx-1",
            ),
            target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="retry"),
            resolved_payload=ResolvedDispatchPayload(
                resource_payloads=[
                    ResolvedResourcePayload(
                        ref_id="ctx:file-file-1:text",
                        kind="context",
                        mime_type="text/plain",
                        text="Projected input",
                    )
                ]
            ),
        )

    persisted = await store.get_run("run-1")
    assert persisted is not None
    assert persisted.pending_agent_continuations[0].status == "open"


@pytest.mark.asyncio
async def test_ingest_v2_results_reopens_repeated_plain_input_and_resolves_continuation():
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=_state_unification_user_message(message_id="msg-1"),
    )
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Initial",
                task_hash="hash-1",
                status="dispatching",
            )
        ]
    )
    await store.create_run(state)

    plain_input = StepResult(
        step_number=1,
        agent_id="agent-1",
        agent_name="Agent One",
        task="Initial",
        response_text="Need more input",
        success=True,
        status=StepStatus.AWAITING_INPUT,
        agent_message_id="agent-msg-1",
        a2a_task_id="task-1",
        a2a_context_id="ctx-1",
    )
    current = await executor._ingest_v2_results(
        state, [plain_input], status=OrchestrationStatus.RUNNING, advance_step=False
    )
    assert current.pending_agent_continuations[0].status == "open"

    current = await executor._claim_matching_continuation(
        state=current,
        target=PlannedDelegateTarget(
            agent_id="agent-1", task="Repair", repair_of_intent_id="intent-1"
        ),
        goal_family_fingerprint=current.pending_agent_continuations[0].goal_family_fingerprint,
        selected_resource_fingerprints={"resource-new"},
    )
    assert current is not None
    current = await executor._ingest_v2_results(
        await store.get_run("run-1"), [plain_input], status=OrchestrationStatus.RUNNING, advance_step=False
    )
    assert current.pending_agent_continuations[0].status == "open"

    claimed = await executor._claim_matching_continuation(
        state=current,
        target=PlannedDelegateTarget(
            agent_id="agent-1", task="Repair", repair_of_intent_id="intent-1"
        ),
        goal_family_fingerprint=current.pending_agent_continuations[0].goal_family_fingerprint,
        selected_resource_fingerprints={"resource-newer"},
    )
    assert claimed is not None
    current = await executor._ingest_v2_results(
        await store.get_run("run-1"),
        [plain_input.model_copy(update={"status": StepStatus.SUCCESS, "success": True})],
        status=OrchestrationStatus.RUNNING,
        advance_step=False,
    )
    assert current.pending_agent_continuations[0].status == "resolved"


@pytest.mark.asyncio
async def test_persisted_resource_fingerprints_seed_continuation_attempts():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded PDF"),
    )
    executor = _executor(
        store=InMemoryOrchestrationRunStore(),
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=SimpleNamespace(
            extend_info={
                "resolved_dispatch_payload_refs": {
                    "resource_payloads": [
                        {
                            "ref_id": "ctx:file-1:text",
                            "kind": "context",
                            "mime_type": "text/plain",
                            "text": "Projected input",
                        }
                    ]
                }
            }
        )
    )

    fingerprints = await executor._v2_persisted_resource_fingerprints(
        "agent-msg-1"
    )

    assert len(fingerprints) == 1


@pytest.mark.asyncio
async def test_resolving_continuation_completes_nonterminal_lineage():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Continue"),
    )
    store = InMemoryOrchestrationRunStore()
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Initial",
                task_hash="hash-1",
                status="awaiting_input",
            ),
            DispatchIntent(
                step_id="step-2",
                step_target_id="target-2",
                dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-2",
                agent_id="agent-1",
                task="Repair",
                task_hash="hash-2",
                repair_of_intent_id="intent-1",
                status="planned",
            ),
        ],
        active_dispatches=[
            {"agent_message_id": "agent-msg-1", "agent_id": "agent-1", "status": "awaiting_input"},
            {"agent_message_id": "agent-msg-2", "agent_id": "agent-1", "status": "dispatching"},
        ],
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="cont-1",
                source_intent_id="intent-1",
                source_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1",
                a2a_context_id="context-1",
                status="resuming",
            )
        ],
    )
    await store.create_run(state)
    executor = _executor(store=store, planner=RecordingPlanner(), user_message=user_message)

    saved = await executor._reconcile_persisted_continuation(
        state=state,
        continuation_id="cont-1",
        status="resolved",
    )

    assert [intent.status for intent in saved.dispatch_intents] == ["completed", "completed"]
    assert [dispatch.status for dispatch in saved.active_dispatches] == ["completed", "completed"]
    assert saved.pending_agent_continuations[0].status == "resolved"


@pytest.mark.asyncio
async def test_reconciling_continuation_retries_store_conflict(monkeypatch):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Continue"),
    )
    store = InMemoryOrchestrationRunStore()
    state = _run_state(
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="cont-1",
                source_intent_id="intent-1",
                source_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1",
                a2a_context_id="context-1",
                status="resuming",
            )
        ],
    )
    await store.create_run(state)
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    original_save_state = store.save_state
    save_attempts = 0

    async def save_with_one_conflict(updated, *, expected_version):
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts == 1:
            raise OrchestrationStoreConflict("competing continuation update")
        return await original_save_state(
            updated,
            expected_version=expected_version,
        )

    monkeypatch.setattr(store, "save_state", save_with_one_conflict)

    saved = await executor._reconcile_persisted_continuation(
        state=state,
        continuation_id="cont-1",
        status="resolved",
    )

    assert save_attempts == 2
    assert saved.pending_agent_continuations[0].status == "resolved"


@pytest.mark.asyncio
async def test_plain_continuation_preserves_auth_required_classification():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded PDF"),
    )
    executor = _executor(
        store=InMemoryOrchestrationRunStore(),
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(
            reply_to_task=AsyncMock(
                return_value={
                    "task_state": "auth-required",
                    "response_text": "Sign in to continue",
                }
            )
        )
    )

    result = await executor._continue_agent_task_with_resolved_refs(
        claimed_continuation=_claimed_continuation(),
        awaiting_output=AgentOutputRecord(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
        ),
        target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="retry"),
        resolved_payload=ResolvedDispatchPayload(
            selected_context_refs=["ctx:file-file-1:text"],
            resource_payloads=[
                ResolvedResourcePayload(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    mime_type="text/plain",
                    text="Projected input",
                )
            ],
        ),
    )

    assert result is not None
    assert result.status == StepStatus.AWAITING_INPUT
    assert result.interactive_state == "auth-required"
    assert result.requires_auth is True
    assert SupervisorExecutor._awaiting_result_requires_hitl(result) is True
    assert SupervisorExecutor._is_plain_a2a_input_output(
        AgentOutputRecord(
            agent_message_id=result.agent_message_id,
            agent_id=result.agent_id,
            status=result.status.value,
            a2a_task_id=result.a2a_task_id,
            a2a_context_id=result.a2a_context_id,
            interactive_state=result.interactive_state,
            requires_auth=result.requires_auth,
        )
    ) is False


@pytest.mark.asyncio
async def test_plain_continuation_preserves_policy_required_classification():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded PDF"),
    )
    executor = _executor(
        store=InMemoryOrchestrationRunStore(),
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(
            reply_to_task=AsyncMock(
                return_value={
                    "task_state": "input-required",
                    "response_text": "Approve policy to continue",
                    "requires_policy": True,
                }
            )
        )
    )

    result = await executor._continue_agent_task_with_resolved_refs(
        claimed_continuation=_claimed_continuation(),
        awaiting_output=AgentOutputRecord(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
        ),
        target=DelegateTarget(agent_id="agent-1", agent_name="Agent One", task="retry"),
        resolved_payload=ResolvedDispatchPayload(
            selected_context_refs=["ctx:file-file-1:text"],
            resource_payloads=[
                ResolvedResourcePayload(
                    ref_id="ctx:file-file-1:text",
                    kind="context",
                    mime_type="text/plain",
                    text="Projected input",
                )
            ],
        ),
    )

    assert result is not None
    assert result.status == StepStatus.AWAITING_INPUT
    assert result.interactive_state == "policy-required"
    assert result.requires_policy is True
    assert SupervisorExecutor._awaiting_result_requires_hitl(result) is True


@pytest.mark.asyncio
async def test_supersede_unresolved_input_required_outputs_for_other_agents():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use another agent"),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
        guardrails_enabled=True,
    )
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1", step_target_id="target-1", dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1", agent_id="agent-1", task="Initial",
                task_hash="hash-1", status="awaiting_input",
            ),
            DispatchIntent(
                step_id="step-2", step_target_id="target-2", dispatch_intent_id="intent-2",
                planned_agent_message_id="agent-msg-1-repair", agent_id="agent-1", task="Repair",
                task_hash="hash-2", repair_of_intent_id="intent-1", status="dispatching",
            ),
        ],
        active_dispatches=[
            {"agent_message_id": "agent-msg-1", "agent_id": "agent-1", "status": "awaiting_input"},
            {"agent_message_id": "agent-msg-1-repair", "agent_id": "agent-1", "status": "dispatching"},
        ],
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="cont-1", source_intent_id="intent-1",
                source_agent_message_id="agent-msg-1", agent_id="agent-1",
                goal_family_fingerprint="family-1", goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1", a2a_context_id="context-1",
            )
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status=StepStatus.AWAITING_INPUT.value,
                a2a_task_id="task-1",
                a2a_context_id="ctx-1",
            ),
            AgentOutputRecord(
                agent_message_id="agent-msg-2",
                agent_id="agent-2",
                status=StepStatus.AWAITING_INPUT.value,
                a2a_task_id="task-2",
                a2a_context_id="ctx-2",
            ),
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-1",
                fingerprint="agent-1:agent-msg-1:agent_input_required",
                source="a2a_adapter",
                agent_id="agent-1",
                agent_message_id="agent-msg-1",
                error_code="agent_input_required",
                error_message="Need input",
                recoverable=True,
            ),
            OpenFailureRecord(
                failure_id="failure-2",
                fingerprint="agent-2:agent-msg-2:agent_input_required",
                source="a2a_adapter",
                agent_id="agent-2",
                agent_message_id="agent-msg-2",
                error_code="agent_input_required",
                error_message="Need input",
                recoverable=True,
            ),
        ],
    )
    await store.create_run(state)

    saved = await executor._supersede_unresolved_input_required_outputs(
        state,
        chosen_targets=[PlannedDelegateTarget(agent_id="agent-2", task="New task")],
    )

    assert [output.status for output in saved.agent_outputs] == [
        "abandoned",
        StepStatus.AWAITING_INPUT.value,
    ]
    assert [failure.status for failure in saved.open_failures] == [
        "abandoned",
        "open",
    ]
    assert [intent.status for intent in saved.dispatch_intents] == ["abandoned", "abandoned"]
    assert [dispatch.status for dispatch in saved.active_dispatches] == ["abandoned", "abandoned"]
    assert saved.pending_agent_continuations[0].status == "abandoned"


@pytest.mark.asyncio
async def test_supersede_abandons_same_agent_fresh_nonrepair_continuation_lineage():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Start a fresh task"),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
        guardrails_enabled=True,
    )
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Old task",
                task_hash="hash-1",
                status="awaiting_input",
            )
        ],
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="cont-1",
                source_intent_id="intent-1",
                source_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1",
                a2a_context_id="context-1",
            )
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status=StepStatus.AWAITING_INPUT.value,
                a2a_task_id="task-1",
                a2a_context_id="context-1",
            )
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-1",
                fingerprint="agent-1:agent-msg-1:agent_input_required",
                source="a2a_adapter",
                agent_id="agent-1",
                agent_message_id="agent-msg-1",
                error_code="agent_input_required",
                error_message="Need input",
                recoverable=True,
            )
        ],
    )
    await store.create_run(state)

    saved = await executor._supersede_unresolved_input_required_outputs(
        state,
        chosen_targets=[
            PlannedDelegateTarget(agent_id="agent-1", task="Fresh unrelated task")
        ],
    )

    assert saved.pending_agent_continuations[0].status == "abandoned"
    assert saved.agent_outputs[0].status == "abandoned"
    assert saved.open_failures[0].status == "abandoned"
    assert saved.dispatch_intents[0].status == "abandoned"
    assert any(
        event.type == OrchestrationEventType.CONTINUATION_ABANDONED
        for event in store._events_by_run["run-1"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guardrails_enabled", "expected_status"),
    [(False, "open"), (True, "abandoned")],
)
async def test_continuation_supersession_respects_injected_guardrail_flag(
    guardrails_enabled: bool,
    expected_status: str,
):
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Delegate generic work"),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
        guardrails_enabled=guardrails_enabled,
    )
    state = _run_state(
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Produce a generic artifact",
                task_hash="hash-1",
                status="awaiting_input",
            )
        ],
        pending_agent_continuations=[
            PendingAgentContinuation(
                continuation_id="cont-1",
                source_intent_id="intent-1",
                source_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                goal_family_fingerprint="family-1",
                goal_revision_fingerprint="revision-1",
                a2a_task_id="task-1",
                a2a_context_id="context-1",
            )
        ],
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="agent-msg-1",
                agent_id="agent-1",
                status=StepStatus.AWAITING_INPUT.value,
                a2a_task_id="task-1",
                a2a_context_id="context-1",
            )
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="failure-1",
                fingerprint="agent-1:agent-msg-1:agent_input_required",
                source="a2a_adapter",
                agent_id="agent-1",
                agent_message_id="agent-msg-1",
                error_code="agent_input_required",
                error_message="Need input",
                recoverable=True,
            )
        ],
    )
    await store.create_run(state)

    saved = await executor._supersede_unresolved_input_required_outputs(
        state,
        chosen_targets=[PlannedDelegateTarget(agent_id="agent-2", task="Consume it")],
    )

    assert saved.pending_agent_continuations[0].status == expected_status
    if guardrails_enabled:
        assert saved.agent_outputs[0].status == "abandoned"
        assert saved.open_failures[0].status == "abandoned"
    else:
        assert saved.agent_outputs[0].status == StepStatus.AWAITING_INPUT.value
        assert saved.open_failures[0].status == "open"


@pytest.mark.asyncio
async def test_supersede_preserves_structured_auth_and_policy_hitl_outputs():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use another agent"),
    )
    store = InMemoryOrchestrationRunStore()
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
        guardrails_enabled=True,
    )
    state = _run_state(
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id="plain-msg",
                agent_id="plain-agent",
                status=StepStatus.AWAITING_INPUT.value,
                a2a_task_id="plain-task",
                a2a_context_id="plain-context",
            ),
            AgentOutputRecord(
                agent_message_id="auth-msg",
                agent_id="auth-agent",
                status=StepStatus.AWAITING_INPUT.value,
                interactive_state="auth-required",
                requires_auth=True,
            ),
            AgentOutputRecord(
                agent_message_id="policy-msg",
                agent_id="policy-agent",
                status=StepStatus.AWAITING_INPUT.value,
                interactive_state="policy-required",
                requires_policy=True,
            ),
        ],
        open_failures=[
            OpenFailureRecord(
                failure_id="plain-failure",
                fingerprint="plain-agent:plain-msg:agent_input_required",
                source="a2a_adapter",
                agent_id="plain-agent",
                agent_message_id="plain-msg",
                error_code="agent_input_required",
                error_message="Need input",
                recoverable=True,
            ),
            OpenFailureRecord(
                failure_id="auth-failure",
                fingerprint="auth-agent:auth-msg:auth_required",
                source="a2a_adapter",
                agent_id="auth-agent",
                agent_message_id="auth-msg",
                error_code="auth_required",
                error_message="Authorize access",
                recoverable=True,
            ),
            OpenFailureRecord(
                failure_id="policy-failure",
                fingerprint="policy-agent:policy-msg:policy_required",
                source="a2a_adapter",
                agent_id="policy-agent",
                agent_message_id="policy-msg",
                error_code="policy_required",
                error_message="Approve policy",
                recoverable=True,
            ),
        ],
    )
    await store.create_run(state)

    saved = await executor._supersede_unresolved_input_required_outputs(
        state,
        chosen_targets=[
            PlannedDelegateTarget(agent_id="chosen-agent", task="New task")
        ],
    )

    assert [output.status for output in saved.agent_outputs] == [
        "abandoned",
        StepStatus.AWAITING_INPUT.value,
        StepStatus.AWAITING_INPUT.value,
    ]
    assert [failure.status for failure in saved.open_failures] == [
        "abandoned",
        "open",
        "open",
    ]


@pytest.mark.asyncio
async def test_delegate_recovery_reuses_message_and_closes_current_intent():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Use uploaded text"),
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
        goal="Use uploaded text",
    )
    state.dispatch_intents.append(
        DispatchIntent(
            step_id="step-1",
            step_target_id="target-1",
            dispatch_intent_id="intent-1",
            planned_agent_message_id="agent-msg-1",
            agent_id="agent-1",
            task="Initial task",
            task_hash="hash-1",
            status=StepStatus.AWAITING_INPUT.value,
        )
    )
    state.agent_outputs.append(
        AgentOutputRecord(
            agent_message_id="agent-msg-1",
            agent_id="agent-1",
            status=StepStatus.AWAITING_INPUT.value,
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
        )
    )
    state.open_failures.append(
        OpenFailureRecord(
            failure_id="failure-1",
            fingerprint="agent-1:agent-msg-1:agent_input_required",
            source="a2a_adapter",
            agent_id="agent-1",
            agent_message_id="agent-msg-1",
            error_code="agent_input_required",
            error_message="Need input",
            recoverable=True,
        )
    )
    state.pending_agent_continuations.append(
        PendingAgentContinuation(
            continuation_id="continuation-1",
            source_intent_id="intent-1",
            source_agent_message_id="agent-msg-1",
            agent_id="agent-1",
            goal_family_fingerprint=goal_fingerprints(
                agent_id="agent-1",
                expected_outputs=[],
                selected_content_fingerprints=[],
                dependency_family_fingerprints=[],
                upstream_output_fingerprints=[],
            ).goal_family_fingerprint,
            goal_revision_fingerprint="revision-1",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
        )
    )
    await store.create_run(state)
    planner_action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Continue the existing task.",
        targets=[
            PlannedDelegateTarget(
                agent_id="agent-1",
                agent_name="Agent One",
                task="Use the projected input",
                repair_of_intent_id="intent-1",
                context_refs=[
                    DispatchContentRef(
                        kind=DispatchRefKind.CONTEXT,
                        ref_id="ctx:file-file-1:text",
                        source_agent_message_id="source-msg-1",
                        mime_type="text/plain",
                    )
                ],
            )
        ],
    )
    executor = _executor(
        store=store,
        planner=RecordingPlanner(),
        user_message=user_message,
    )
    executor.orchestration_resource_provider = SimpleNamespace(
        resolve_ref=AsyncMock(
            return_value=ResolvedResourcePayload(
                ref_id="ctx:file-file-1:text",
                kind="context",
                mime_type="text/plain",
                text="Projected input",
            )
        )
    )
    visible_message = _agent_message("agent-msg-1")
    executor.message_reader.get_room_agent_message_by_message_id = AsyncMock(
        return_value=visible_message
    )
    executor.message_writer.update_room_agent_message_by_message_id = AsyncMock(
        return_value=True
    )
    executor.hitl_coordinator = SimpleNamespace(
        agent_reply=SimpleNamespace(
            reply_to_task=AsyncMock(
                return_value={
                    "blocking": True,
                    "task_state": "completed",
                    "response_text": "Recovered answer",
                }
            )
        )
    )

    saved, status = await executor._run_delegate_action(
        state=state,
        planner_action=planner_action,
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        room_id="room-1",
        user_message_id="message-1",
        message_text="Use uploaded text",
        conversation_context=None,
        token=None,
        request_user_id="user-1",
        quoted_text=None,
        user_message=user_message,
    )

    assert status is None
    executor.message_writer.add_room_agent_message.assert_not_awaited()
    assert visible_message.extend_info["orchestration_recovery"] == {
        "type": "continued_a2a_task",
        "a2a_task_id": "task-1",
        "a2a_context_id": "ctx-1",
        "selected_context_refs": ["ctx:file-file-1:text"],
        "selected_artifact_refs": [],
        "selected_attachment_refs": [],
    }
    assert saved.agent_outputs[0].status == "completed"
    assert saved.dispatch_intents[0].status == "completed"
