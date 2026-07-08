from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.config.settings import Settings
from common.utils.time import utcnow
from execution.orchestration.planner import RoomSupervisorPlannerAdapter
from execution.orchestration.resources import (
    OrchestrationResourceProvider,
    ResourcePayload,
    ResourceProjectionRef,
)
from execution.orchestration.room_message_center import RoomMessageCenter
from execution.orchestration.run_store import (
    InMemoryOrchestrationRunStore,
)
from execution.orchestration.supervisor_executor import SupervisorExecutor
from models.agent import AgentStatus
from models.orchestration import (
    CompletionEvidence,
    DispatchIntent,
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
async def test_run_validates_complete_against_run_state_after_dispatch():
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
    assert state.terminal_reason == "complete action requires completion evidence"


@pytest.mark.asyncio
async def test_run_resume_ingests_paused_result_before_planning():
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
        assert state.status == OrchestrationStatus.INGESTING
        assert state.pending_hitl_request_ids == [
            "message-1:step-1:supervisor-hitl-1"
        ]
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

    assert result.status == RunStatus.AWAITING_INPUT
    assert planner.contexts == []
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.AWAITING_USER
    assert state.pending_hitl_request_ids == ["hitl-1"]


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
            status_message="Please authenticate.",
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
    assert hitl_kwargs["prompt"] == "Please authenticate."
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
                ),
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="Needs user input",
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
    executor.hitl_coordinator.request_input.assert_awaited_once()
    hitl_kwargs = executor.hitl_coordinator.request_input.await_args.kwargs
    assert hitl_kwargs["prompt"] == "Need approval."
    assert hitl_kwargs["continuation_message_id"] == (
        "message-1:step-1:target-2:message"
    )
    save_kinds = [
        call.kwargs["kind"].value
        for call in executor._save_interrupted_state.await_args_list
    ]
    assert save_kinds == ["push_notification", "hitl_agent"]
    state = await store.get_run("message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.AWAITING_USER
    assert state.pending_hitl_request_ids == ["hitl-agent-1"]


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
                ),
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="Second task",
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
                ),
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="Second task",
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
    assert state.open_questions == []


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
    assert state.open_questions == []


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
async def test_run_invalid_planner_action_marks_sidecar_failed():
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
            reasoning="invalid target",
            targets=[
                PlannedDelegateTarget(
                    agent_id="agent-2",
                    agent_name="Agent Two",
                    task="Not in scope",
                )
            ],
        )
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
        (OrchestrationStatus.BUDGET_EXHAUSTED, "failed", "failed"),
        (OrchestrationStatus.AWAITING_USER, "awaiting_input", "awaiting_input"),
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
