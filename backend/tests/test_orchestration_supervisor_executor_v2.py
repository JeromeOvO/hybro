from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.config.settings import Settings
from common.utils.time import utcnow
from execution.orchestration.room_message_center import RoomMessageCenter
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from execution.orchestration.supervisor_executor import SupervisorExecutor
from models.agent import AgentStatus
from models.orchestration import (
    OrchestrationStatus,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)
from models.processing import ProcessingResult, ProcessingStatus
from models.response import OrchestrationResponse
from models.room import MessageContent, Room, RoomUserMessage
from models.supervisor import (
    ActionType,
    AgentProfile,
    DelegateTarget,
    RoomConfig,
    RunStatus,
    StepResult,
    StepStatus,
    SupervisorAction,
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


def _agent_message(message_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        turn_id=None,
        client_request_id="client-1",
        extend_info={},
        message_content=SimpleNamespace(message_text="", message_task=None),
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
async def test_run_v2_uses_sidecar_scope_planner_store_and_planned_message_ids():
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

    result = await executor.run_v2(
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
    assert state.summary_intent_id == "run-message-1:summary"
    assert state.summary_message_id == "sys-message-1"
    assert len(state.dispatch_intents) == 1

    intent = state.dispatch_intents[0]
    assert intent.agent_id == "agent-1"
    assert intent.planned_agent_message_id == "run-message-1:step-1:target-1:message"
    assert state.agent_outputs[0].agent_message_id == intent.planned_agent_message_id

    added_message_ids = [
        call.args[0].message_id
        for call in executor.message_writer.add_room_agent_message.await_args_list
    ]
    assert added_message_ids == ["sys-message-1", intent.planned_agent_message_id]


@pytest.mark.asyncio
async def test_run_v2_resume_ingests_paused_result_before_planning():
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
            message_id="run-message-1:step-1:target-1:message",
        )
    )
    first_executor._save_interrupted_state = AsyncMock(return_value=True)

    paused = await first_executor.run_v2(
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
                        agent_message_id="run-message-1:step-1:target-1:message",
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

    result = await second_executor.run_v2(
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
    state = await store.get_run("run-message-1")
    assert state is not None
    assert state.agent_outputs[0].text == "Webhook result"
    assert state.dispatch_intents[0].status == StepStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_run_v2_ask_user_creates_hitl_prompt_and_continuation():
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

    result = await executor.run_v2(
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
    assert hitl_kwargs["orchestration_run_id"] == "run-message-1"
    executor._save_interrupted_state.assert_awaited_once()
    assert executor._save_interrupted_state.await_args.kwargs["kind"].value == (
        "hitl_supervisor"
    )


@pytest.mark.asyncio
async def test_run_v2_agent_awaiting_input_creates_hitl_prompt_and_continuation():
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
            message_id="run-message-1:step-1:target-1:message",
            a2a_task_id="task-1",
            a2a_context_id="ctx-1",
            status_message="Please authenticate.",
        )
    )
    executor.hitl_coordinator = SimpleNamespace(
        request_input=AsyncMock(return_value=SimpleNamespace(request_id="hitl-agent-1"))
    )
    executor._save_interrupted_state = AsyncMock(return_value=True)

    result = await executor.run_v2(
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
        "run-message-1:step-1:target-1:message"
    )
    assert hitl_kwargs["display_message_id"] == "run-message-1:step-1:target-1:message"
    assert hitl_kwargs["orchestration_run_id"] == "run-message-1"
    executor._save_interrupted_state.assert_awaited_once()
    save_kwargs = executor._save_interrupted_state.await_args.kwargs
    assert save_kwargs["kind"].value == "hitl_agent"
    assert save_kwargs["message_id"] == "run-message-1:step-1:target-1:message"

    state = await store.get_run("run-message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.AWAITING_USER
    assert state.pending_hitl_request_ids == ["hitl-agent-1"]
    assert state.agent_outputs[0].status == StepStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_run_v2_partial_paused_resume_waits_for_remaining_agents():
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration": True,
            "orchestration_schema_version": 2,
            "orchestration_run_id": "run-message-1",
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
                message_id="run-message-1:step-1:target-1:message",
            ),
            ProcessingResult(
                ProcessingStatus.PAUSED,
                message_id="run-message-1:step-1:target-2:message",
            ),
        ]
    )
    first_executor._save_interrupted_state = AsyncMock(return_value=True)

    paused = await first_executor.run_v2(
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
                        agent_message_id="run-message-1:step-1:target-1:message",
                    ),
                    StepResult(
                        step_number=1,
                        agent_id="agent-2",
                        agent_name="Agent Two",
                        task="Second task",
                        response_text="",
                        success=True,
                        status=StepStatus.PAUSED,
                        paused_message_id="run-message-1:step-1:target-2:message",
                        agent_message_id="run-message-1:step-1:target-2:message",
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

    result = await second_executor.run_v2(
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
    state = await store.get_run("run-message-1")
    assert state is not None
    assert state.status == OrchestrationStatus.WAITING_AGENT
    assert state.steps_used == 0
    outputs_by_id = {output.agent_message_id: output for output in state.agent_outputs}
    assert (
        outputs_by_id["run-message-1:step-1:target-1:message"].text
        == "First webhook result"
    )
    assert (
        outputs_by_id["run-message-1:step-1:target-2:message"].status
        == StepStatus.PAUSED.value
    )


@pytest.mark.asyncio
async def test_run_v2_supervisor_hitl_resume_clears_pending_request_ids():
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

    first_result = await first_executor.run_v2(
        room_id="room-1",
        user_message_id="message-1",
        message_text="Coordinate this",
        agent_registry=[AgentProfile(agent_id="agent-1", agent_name="Agent One")],
        room_config=RoomConfig(),
        request_user_id="user-1",
        user_message=user_message,
    )
    assert first_result.status == RunStatus.AWAITING_INPUT
    state = await store.get_run("run-message-1")
    assert state is not None
    assert state.pending_hitl_request_ids == ["hitl-1"]

    resumed_trajectory = first_result.trajectory
    resumed_trajectory.hitl_user_reply = "Account A"
    second_planner = RecordingPlanner(
        PlannerAction(
            action=PlannerActionType.FAIL,
            reasoning="done after user reply",
            failure_reason="stop after inspecting context",
        )
    )
    second_executor = _executor(store=store, planner=second_planner, user_message=user_message)

    second_result = await second_executor.run_v2(
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
    state = await store.get_run("run-message-1")
    assert state is not None
    assert state.pending_hitl_request_ids == []


def _supervisor_agent(agent_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=agent_id,
        agent_card=SimpleNamespace(name=name, description="", skills=[]),
        call_count=0,
        call_success_count=0,
        agent_status=AgentStatus.active,
    )


@pytest.mark.asyncio
async def test_persisted_schema_version_routes_to_run_v2_without_current_flag():
    rmc = RoomMessageCenter.__new__(RoomMessageCenter)
    user_message = RoomUserMessage(
        room_id="room-1",
        message_id="message-1",
        user_id="user-1",
        message_content=MessageContent(message_text="Coordinate this"),
        extend_info={
            "orchestration_schema_version": 2,
            "orchestration_run_id": "run-message-1",
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
            side_effect=AssertionError("v2 supervisor envelope should not use queue path")
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
        run=AsyncMock(side_effect=AssertionError("legacy run should not be used")),
        run_v2=AsyncMock(return_value=supervisor_result),
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
    rmc.supervisor_executor.run_v2.assert_awaited_once()
    rmc.supervisor_executor.run.assert_not_awaited()


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
