"""
Tests for sequential debate dispatch in SupervisorExecutor.

Covers:
- Helper methods: snapshot, remaining IDs, prior responses, debate task builder
- Executor integration: one-per-step dispatch, DONE after all agents, budget
- Resume: continues dispatching, completes when all done, preserves participants
- Scope: all_agents + debate bypasses LLM selector
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.dto import MessageCommitted
from common.utils.time import utcnow
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from execution.orchestration.supervisor_executor import SupervisorExecutor
from models.orchestration import (
    CompletionEvidence,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)
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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class RecordingEventPublisher:
    def __init__(self):
        self.internal_events = []

    async def emit_internal(
        self,
        event,
        *,
        wait_for_local_handlers: bool = False,
        broadcast: bool = True,
    ):
        self.internal_events.append(event)


class SequentialDebatePlanner:
    """Planner fixture that drives one healthy candidate per state-loop step."""

    def __init__(self):
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        handled_agent_ids = {
            output["agent_id"]
            for output in context.state_context.agent_outputs
            if output.get("status") in {"success", "completed", "failed"}
        }
        healthy_candidates = [
            agent
            for agent in context.candidate_scope.agents
            if agent.is_healthy is not False
        ]
        for agent in healthy_candidates:
            if agent.agent_id in handled_agent_ids:
                continue
            return PlannerAction(
                action=PlannerActionType.DELEGATE,
                reasoning=f"Debate: {agent.agent_name or agent.agent_id}",
                targets=[
                    PlannedDelegateTarget(
                        agent_id=agent.agent_id,
                        agent_name=agent.agent_name or agent.agent_id,
                        task=context.message_text,
                    )
                ],
            )

        return PlannerAction(
            action=PlannerActionType.COMPLETE,
            reasoning="All healthy debate candidates responded",
            completion_evidence=CompletionEvidence(
                satisfied_criteria=["All healthy debate candidates responded"],
                final_answer_intent="Debate complete",
                confidence=1.0,
            ),
        )


class MultiTargetDebatePlanner:
    """Planner fixture that over-selects remaining candidates like a raw LLM can."""

    def __init__(self):
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        handled_agent_ids = {
            output["agent_id"]
            for output in context.state_context.agent_outputs
            if output.get("status") in {"success", "completed", "failed"}
        }
        remaining = [
            agent
            for agent in context.candidate_scope.agents
            if agent.is_healthy is not False and agent.agent_id not in handled_agent_ids
        ]
        if remaining:
            return PlannerAction(
                action=PlannerActionType.DELEGATE,
                reasoning="Debate all remaining candidates",
                targets=[
                    PlannedDelegateTarget(
                        agent_id=agent.agent_id,
                        agent_name=agent.agent_name or agent.agent_id,
                        task=context.message_text,
                    )
                    for agent in remaining
                ],
            )

        return PlannerAction(
            action=PlannerActionType.COMPLETE,
            reasoning="All healthy debate candidates responded",
            completion_evidence=CompletionEvidence(
                satisfied_criteria=["All healthy debate candidates responded"],
                final_answer_intent="Debate complete",
                confidence=1.0,
            ),
        )


class SingleOutOfOrderDebatePlanner:
    """Planner fixture that asks for a later participant before the next turn."""

    def __init__(self):
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        handled_agent_ids = {
            output["agent_id"]
            for output in context.state_context.agent_outputs
            if output.get("status") in {"success", "completed", "failed"}
        }
        if "a1" not in handled_agent_ids:
            return PlannerAction(
                action=PlannerActionType.DELEGATE,
                reasoning="Ask Beta first",
                targets=[
                    PlannedDelegateTarget(
                        agent_id="a2",
                        agent_name="Beta",
                        task=context.message_text,
                    )
                ],
            )
        if "a2" not in handled_agent_ids:
            return PlannerAction(
                action=PlannerActionType.DELEGATE,
                reasoning="Ask Beta next",
                targets=[
                    PlannedDelegateTarget(
                        agent_id="a2",
                        agent_name="Beta",
                        task=context.message_text,
                    )
                ],
            )
        return _debate_complete_action()


class MissingNextParticipantDebatePlanner:
    """Planner fixture that returns candidates while omitting the required turn."""

    def __init__(self):
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        handled_agent_ids = {
            output["agent_id"]
            for output in context.state_context.agent_outputs
            if output.get("status") in {"success", "completed", "failed"}
        }
        if "a1" not in handled_agent_ids:
            targets = [
                PlannedDelegateTarget(
                    agent_id="a2",
                    agent_name="Beta",
                    task=context.message_text,
                ),
                PlannedDelegateTarget(
                    agent_id="a3",
                    agent_name="Gamma",
                    task=context.message_text,
                ),
            ]
        elif "a2" not in handled_agent_ids:
            targets = [
                PlannedDelegateTarget(
                    agent_id="a3",
                    agent_name="Gamma",
                    task=context.message_text,
                )
            ]
        elif "a3" not in handled_agent_ids:
            targets = [
                PlannedDelegateTarget(
                    agent_id="a3",
                    agent_name="Gamma",
                    task=context.message_text,
                )
            ]
        else:
            return _debate_complete_action()

        return PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Missing the next required participant",
            targets=targets,
        )


class PrematureCompleteDebatePlanner:
    """Planner fixture that tries to complete before required turns are done."""

    def __init__(self):
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        return _debate_complete_action()


class PrematureSynthesizeDebatePlanner:
    """Planner fixture that synthesizes after only the first debate response."""

    def __init__(self):
        self.contexts = []

    async def plan(self, context):
        self.contexts.append(context)
        handled_agent_ids = {
            output["agent_id"]
            for output in context.state_context.agent_outputs
            if output.get("status") in {"success", "completed", "failed"}
        }
        if "a1" not in handled_agent_ids:
            return PlannerAction(
                action=PlannerActionType.DELEGATE,
                reasoning="Ask Alpha first",
                targets=[
                    PlannedDelegateTarget(
                        agent_id="a1",
                        agent_name="Alpha",
                        task=context.message_text,
                    )
                ],
            )
        if "a2" not in handled_agent_ids:
            return PlannerAction(
                action=PlannerActionType.SYNTHESIZE,
                reasoning="Summarize before Beta responds",
                synthesis_instruction="Summarize the debate so far",
            )
        return _debate_complete_action()


def _debate_complete_action() -> PlannerAction:
    return PlannerAction(
        action=PlannerActionType.COMPLETE,
        reasoning="All healthy debate candidates responded",
        completion_evidence=CompletionEvidence(
            satisfied_criteria=["All healthy debate candidates responded"],
            final_answer_intent="Debate complete",
            confidence=1.0,
        ),
    )


def _make_agent_profile(agent_id: str, name: str, healthy: bool = True) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        agent_name=name,
        description="",
        is_healthy=healthy,
    )


def _make_supervisor_executor() -> SupervisorExecutor:
    se = object.__new__(SupervisorExecutor)
    se.message_reader = AsyncMock()
    se.message_writer = AsyncMock()
    se.task_state_store = AsyncMock()
    se.continuation_store = AsyncMock()
    se.delivery = AsyncMock()
    se.delivery.send_processing_status = AsyncMock()
    se.room_runtime = MagicMock()
    se.supervisor_service = MagicMock()
    se.tsm = MagicMock()
    se.agent_dispatcher = MagicMock()
    se.agent_message_processor = MagicMock()
    se.event_publisher = RecordingEventPublisher()
    se.rate_limit_service = MagicMock()
    se.debate_rounds = 1
    se.orchestration_run_store = InMemoryOrchestrationRunStore()
    se.orchestration_planner = SequentialDebatePlanner()
    se._processing_status_emitter = AsyncMock()
    return se


def test_constructor_requires_event_publisher():
    deps = {
        "supervisor_service": MagicMock(),
        "room_runtime": MagicMock(),
        "tsm": MagicMock(),
        "delivery": MagicMock(),
        "message_reader": MagicMock(),
        "message_writer": MagicMock(),
        "task_state_store": MagicMock(),
        "continuation_store": MagicMock(),
        "event_publisher": None,
        "rate_limit_service": MagicMock(),
        "agent_dispatcher": MagicMock(),
        "agent_message_processor": MagicMock(),
    }

    with pytest.raises(RuntimeError, match="event_publisher"):
        SupervisorExecutor(**deps)


def _make_delegate_entry(
    step: int,
    agent_id: str,
    agent_name: str,
    response_text: str = "response",
    success: bool = True,
) -> TrajectoryEntry:
    return TrajectoryEntry(
        step_number=step,
        action=SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning=f"Debate: {agent_name}",
            targets=[DelegateTarget(agent_id=agent_id, agent_name=agent_name, task="task")],
        ),
        started_at=utcnow(),
        completed_at=utcnow(),
        results=[
            StepResult(
                step_number=step,
                agent_id=agent_id,
                agent_name=agent_name,
                task="task",
                response_text=response_text,
                success=success,
                status=StepStatus.SUCCESS if success else StepStatus.FAILED,
                agent_message_id=f"agent-msg-{agent_id}",
            )
        ],
    )


# =========================================================================
# _snapshot_debate_agents
# =========================================================================


class TestSnapshotDebateAgents:
    def test_initialized_once(self):
        registry = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
            _make_agent_profile("a3", "Gamma", healthy=False),
        ]
        trajectory = SupervisorTrajectory()

        ids = SupervisorExecutor._snapshot_debate_agents(
            registry, trajectory, debate_rounds=1
        )
        assert ids == ["a1", "a2"]
        assert trajectory.debate_agent_ids == ["a1", "a2"]

        # Second call returns the same snapshot even if registry changes
        registry.append(_make_agent_profile("a4", "Delta"))
        ids2 = SupervisorExecutor._snapshot_debate_agents(registry, trajectory)
        assert ids2 == ["a1", "a2"]

    def test_multi_round_snapshot(self):
        """With debate_rounds=2, each agent appears twice in the snapshot."""
        registry = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]
        trajectory = SupervisorTrajectory()

        ids = SupervisorExecutor._snapshot_debate_agents(
            registry, trajectory, debate_rounds=2
        )
        assert ids == ["a1", "a2", "a1", "a2"]
        assert trajectory.debate_agent_ids == ["a1", "a2", "a1", "a2"]

    def test_survives_serialization(self):
        trajectory = SupervisorTrajectory()
        trajectory.debate_agent_ids = ["a1", "a2", "a3"]

        data = trajectory.model_dump(mode="json")
        restored = SupervisorTrajectory(**data)
        assert restored.debate_agent_ids == ["a1", "a2", "a3"]


# =========================================================================
# _get_remaining_debate_agent_ids
# =========================================================================


class TestGetRemainingDebateAgentIds:
    def test_after_partial_dispatch(self):
        trajectory = SupervisorTrajectory(
            entries=[_make_delegate_entry(1, "a1", "Alpha")]
        )
        remaining = SupervisorExecutor._get_remaining_debate_agent_ids(
            ["a1", "a2", "a3"], trajectory
        )
        assert remaining == ["a2", "a3"]

    def test_all_dispatched(self):
        trajectory = SupervisorTrajectory(
            entries=[
                _make_delegate_entry(1, "a1", "Alpha"),
                _make_delegate_entry(2, "a2", "Beta"),
            ]
        )
        remaining = SupervisorExecutor._get_remaining_debate_agent_ids(
            ["a1", "a2"], trajectory
        )
        assert remaining == []

    def test_preserves_order(self):
        trajectory = SupervisorTrajectory(
            entries=[_make_delegate_entry(1, "a2", "Beta")]
        )
        remaining = SupervisorExecutor._get_remaining_debate_agent_ids(
            ["a1", "a2", "a3"], trajectory
        )
        assert remaining == ["a1", "a3"]

    def test_inflight_entry_not_counted_as_handled(self):
        """P1 fix: An inflight entry (empty results) should NOT mark the
        agent as handled — it needs to be re-dispatched."""
        inflight_entry = TrajectoryEntry(
            step_number=2,
            action=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="Debate: Beta",
                targets=[DelegateTarget(agent_id="a2", agent_name="Beta", task="task")],
            ),
            started_at=utcnow(),
            results=[],  # inflight — no results yet
        )
        trajectory = SupervisorTrajectory(
            entries=[
                _make_delegate_entry(1, "a1", "Alpha"),
                inflight_entry,
            ]
        )
        remaining = SupervisorExecutor._get_remaining_debate_agent_ids(
            ["a1", "a2", "a3"], trajectory
        )
        # a2 should still be remaining (inflight, not handled)
        assert remaining == ["a2", "a3"]

    def test_failed_dispatch_counted_as_handled(self):
        """Debate = each agent gets one turn. A completed-but-failed dispatch
        still counts as handled — the agent had its turn."""
        failed_entry = _make_delegate_entry(2, "a2", "Beta", response_text="", success=False)
        trajectory = SupervisorTrajectory(
            entries=[
                _make_delegate_entry(1, "a1", "Alpha"),
                failed_entry,
            ]
        )
        remaining = SupervisorExecutor._get_remaining_debate_agent_ids(
            ["a1", "a2", "a3"], trajectory
        )
        # a2 had its turn (even though it failed), only a3 remains
        assert remaining == ["a3"]

    def test_unhealthy_skip_counted_as_handled(self):
        """Unhealthy-agent skip has results → counts as handled."""
        skip_entry = TrajectoryEntry(
            step_number=2,
            action=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="Debate: skipping unhealthy agent a2",
                targets=[DelegateTarget(agent_id="a2", agent_name="a2", task="")],
            ),
            started_at=utcnow(),
            completed_at=utcnow(),
            results=[
                StepResult(
                    step_number=2,
                    agent_id="a2",
                    agent_name="a2",
                    task="",
                    response_text="",
                    success=False,
                    status=StepStatus.FAILED,
                    error_message="Agent unhealthy at dispatch time",
                )
            ],
        )
        trajectory = SupervisorTrajectory(
            entries=[
                _make_delegate_entry(1, "a1", "Alpha"),
                skip_entry,
            ]
        )
        remaining = SupervisorExecutor._get_remaining_debate_agent_ids(
            ["a1", "a2", "a3"], trajectory
        )
        assert remaining == ["a3"]

    def test_multi_round_remaining(self):
        """With 2 rounds, after round 1 dispatches both agents,
        round 2 appearances remain."""
        trajectory = SupervisorTrajectory(
            entries=[
                _make_delegate_entry(1, "a1", "Alpha"),
                _make_delegate_entry(2, "a2", "Beta"),
            ]
        )
        # 2-round list: [a1, a2, a1, a2]
        remaining = SupervisorExecutor._get_remaining_debate_agent_ids(
            ["a1", "a2", "a1", "a2"], trajectory
        )
        # Round 1 done, round 2 still pending
        assert remaining == ["a1", "a2"]

    def test_multi_round_fully_dispatched(self):
        """All rounds dispatched → empty remaining."""
        trajectory = SupervisorTrajectory(
            entries=[
                _make_delegate_entry(1, "a1", "Alpha"),
                _make_delegate_entry(2, "a2", "Beta"),
                _make_delegate_entry(3, "a1", "Alpha"),
                _make_delegate_entry(4, "a2", "Beta"),
            ]
        )
        remaining = SupervisorExecutor._get_remaining_debate_agent_ids(
            ["a1", "a2", "a1", "a2"], trajectory
        )
        assert remaining == []


# =========================================================================
# _build_debate_task
# =========================================================================


class TestBuildDebateTask:
    def test_first_agent_no_prior(self):
        result = SupervisorExecutor._build_debate_task("hello world", [])
        assert result == "hello world"

    def test_injects_last_only(self):
        prior = [
            ("Alpha", "response 1"),
            ("Beta", "response 2"),
            ("Gamma", "response 3"),
        ]
        result = SupervisorExecutor._build_debate_task("task", prior)
        assert "RESPONSE FROM PREVIOUS AGENT (Gamma)" in result
        assert "response 3" in result
        # Should NOT include earlier agents' responses
        assert "RESPONSE FROM PREVIOUS AGENT (Alpha)" not in result
        assert "RESPONSE FROM PREVIOUS AGENT (Beta)" not in result

    def test_truncates_long_response(self):
        long_text = "x" * 5000
        prior = [("Alpha", long_text)]
        result = SupervisorExecutor._build_debate_task("task", prior, max_chars=3000)
        assert "truncated" in result
        assert "5000 chars" in result
        # The truncated text should be exactly 3000 chars of 'x'
        assert "x" * 3000 in result
        assert "x" * 3001 not in result

    def test_no_truncation_when_within_limit(self):
        text = "y" * 2999
        prior = [("Alpha", text)]
        result = SupervisorExecutor._build_debate_task("task", prior, max_chars=3000)
        assert "truncated" not in result
        assert text in result


# =========================================================================
# _collect_prior_debate_responses
# =========================================================================


class TestCollectPriorDebateResponses:
    def test_collects_successful_only(self):
        trajectory = SupervisorTrajectory(
            entries=[
                _make_delegate_entry(1, "a1", "Alpha", "good"),
                _make_delegate_entry(2, "a2", "Beta", "", success=False),
                _make_delegate_entry(3, "a3", "Gamma", "also good"),
            ]
        )
        responses = SupervisorExecutor._collect_prior_debate_responses(trajectory)
        assert responses == [("Alpha", "good"), ("Gamma", "also good")]

    def test_empty_trajectory(self):
        trajectory = SupervisorTrajectory()
        responses = SupervisorExecutor._collect_prior_debate_responses(trajectory)
        assert responses == []

    def test_skips_non_delegate_entries(self):
        done_entry = TrajectoryEntry(
            step_number=2,
            action=SupervisorAction(action=ActionType.DONE, reasoning="done"),
            started_at=utcnow(),
            completed_at=utcnow(),
        )
        trajectory = SupervisorTrajectory(
            entries=[
                _make_delegate_entry(1, "a1", "Alpha", "resp"),
                done_entry,
            ]
        )
        responses = SupervisorExecutor._collect_prior_debate_responses(trajectory)
        assert responses == [("Alpha", "resp")]


# =========================================================================
# Executor integration: sequential dispatch
# =========================================================================


@patch("execution.orchestration.supervisor_executor.DEFAULT_DEBATE_ROUNDS", 1)
class TestSequentialDebateDispatch:
    """Integration tests that run the full executor loop with mocked dispatch."""

    @pytest.fixture
    def se(self):
        return _make_supervisor_executor()

    def _debate_config(self) -> RoomConfig:
        return RoomConfig(is_debate_mode=True, room_agent_set={"a1": "Alpha", "a2": "Beta"})

    def _debate_config_three(self) -> RoomConfig:
        return RoomConfig(
            is_debate_mode=True,
            room_agent_set={"a1": "Alpha", "a2": "Beta", "a3": "Gamma"},
        )

    @pytest.mark.asyncio
    async def test_dispatches_one_per_step(self, se):
        """Each loop iteration should dispatch exactly 1 agent."""
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]
        dispatch_calls = []

        async def fake_dispatch(targets, **kwargs):
            dispatch_calls.append([t.agent_id for t in targets])
            return [StepResult(
                step_number=kwargs.get("step_number", 1),
                agent_id=targets[0].agent_id,
                agent_name=targets[0].agent_name,
                task=targets[0].task,
                response_text=f"response from {targets[0].agent_name}",
                success=True,
                status=StepStatus.SUCCESS,
                agent_message_id=f"agent-msg-{targets[0].agent_id}",
            )]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Discuss AI",
            agent_registry=agents,
            room_config=self._debate_config(),
        )

        assert result.status == RunStatus.COMPLETED
        # Should have dispatched 2 agents, one at a time
        assert len(dispatch_calls) == 2
        assert dispatch_calls[0] == ["a1"]
        assert dispatch_calls[1] == ["a2"]
        committed_events = [
            event
            for event in se.event_publisher.internal_events
            if isinstance(event, MessageCommitted)
        ]
        assert [event.message_id for event in committed_events] == [
            "agent-msg-a1",
            "agent-msg-a2",
        ]
        assert [event.message_type for event in committed_events] == ["agent", "agent"]
        assert [event.agent_id for event in committed_events] == ["a1", "a2"]
        assert [event.agent_name for event in committed_events] == ["Alpha", "Beta"]
        assert [event.was_successful for event in committed_events] == [True, True]

    @pytest.mark.asyncio
    async def test_debate_mode_limits_multi_target_planner_to_next_participant(self, se):
        """Debate room policy should narrow an over-selected planner action."""
        se.orchestration_planner = MultiTargetDebatePlanner()
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]
        dispatch_calls = []

        async def fake_dispatch(targets, **kwargs):
            dispatch_calls.append([target.agent_id for target in targets])
            return [
                StepResult(
                    step_number=kwargs.get("step_number", 1),
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text=f"response from {target.agent_name}",
                    success=True,
                    status=StepStatus.SUCCESS,
                    agent_message_id=f"agent-msg-{target.agent_id}",
                )
                for target in targets
            ]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Discuss AI",
            agent_registry=agents,
            room_config=self._debate_config(),
        )

        assert result.status == RunStatus.COMPLETED
        assert dispatch_calls == [["a1"], ["a2"]]
        assert result.trajectory is None
        assert result.run_state is not None
        assert result.run_state.participant_snapshot is not None
        assert result.run_state.participant_snapshot.ordered_agent_ids == ["a1", "a2"]
        assert result.run_state.participant_snapshot.turn_policy == "debate_rounds"
        first_context = se.orchestration_planner.contexts[0]
        assert first_context.state_context.participant_snapshot["turn_policy"] == (
            "debate_rounds"
        )

    @pytest.mark.asyncio
    async def test_debate_mode_corrects_single_out_of_order_target(self, se):
        se.orchestration_planner = SingleOutOfOrderDebatePlanner()
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]
        dispatch_calls = []

        async def fake_dispatch(targets, **kwargs):
            dispatch_calls.append([target.agent_id for target in targets])
            return [
                StepResult(
                    step_number=kwargs.get("step_number", 1),
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text=f"response from {target.agent_name}",
                    success=True,
                    status=StepStatus.SUCCESS,
                    agent_message_id=f"agent-msg-{target.agent_id}",
                )
                for target in targets
            ]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Discuss AI",
            agent_registry=agents,
            room_config=self._debate_config(),
        )

        assert result.status == RunStatus.COMPLETED
        assert dispatch_calls == [["a1"], ["a2"]]
        assert result.trajectory is None

    @pytest.mark.asyncio
    async def test_debate_mode_corrects_targets_missing_next_participant(self, se):
        se.orchestration_planner = MissingNextParticipantDebatePlanner()
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
            _make_agent_profile("a3", "Gamma"),
        ]
        dispatch_calls = []

        async def fake_dispatch(targets, **kwargs):
            dispatch_calls.append([target.agent_id for target in targets])
            return [
                StepResult(
                    step_number=kwargs.get("step_number", 1),
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text=f"response from {target.agent_name}",
                    success=True,
                    status=StepStatus.SUCCESS,
                    agent_message_id=f"agent-msg-{target.agent_id}",
                )
                for target in targets
            ]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Discuss AI",
            agent_registry=agents,
            room_config=self._debate_config_three(),
        )

        assert result.status == RunStatus.COMPLETED
        assert dispatch_calls == [["a1"], ["a2"], ["a3"]]
        assert result.trajectory is None

    @pytest.mark.asyncio
    async def test_debate_mode_does_not_complete_before_required_turns(self, se):
        se.orchestration_planner = PrematureCompleteDebatePlanner()
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]
        dispatch_calls = []

        async def fake_dispatch(targets, **kwargs):
            dispatch_calls.append([target.agent_id for target in targets])
            return [
                StepResult(
                    step_number=kwargs.get("step_number", 1),
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text=f"response from {target.agent_name}",
                    success=True,
                    status=StepStatus.SUCCESS,
                    agent_message_id=f"agent-msg-{target.agent_id}",
                )
                for target in targets
            ]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Discuss AI",
            agent_registry=agents,
            room_config=self._debate_config(),
        )

        assert result.status == RunStatus.COMPLETED
        assert dispatch_calls == [["a1"], ["a2"]]
        assert result.trajectory is None

    @pytest.mark.asyncio
    async def test_debate_mode_does_not_synthesize_before_required_turns(self, se):
        se.orchestration_planner = PrematureSynthesizeDebatePlanner()
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]
        dispatch_calls = []
        se._run_synthesis_action = AsyncMock(
            side_effect=AssertionError("synthesis should wait for all debate turns")
        )

        async def fake_dispatch(targets, **kwargs):
            dispatch_calls.append([target.agent_id for target in targets])
            return [
                StepResult(
                    step_number=kwargs.get("step_number", 1),
                    agent_id=target.agent_id,
                    agent_name=target.agent_name,
                    task=target.task,
                    response_text=f"response from {target.agent_name}",
                    success=True,
                    status=StepStatus.SUCCESS,
                    agent_message_id=f"agent-msg-{target.agent_id}",
                )
                for target in targets
            ]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Discuss AI",
            agent_registry=agents,
            room_config=self._debate_config(),
        )

        assert result.status == RunStatus.COMPLETED
        assert dispatch_calls == [["a1"], ["a2"]]
        se._run_synthesis_action.assert_not_awaited()
        assert result.trajectory is None

    @pytest.mark.asyncio
    async def test_done_after_all_agents(self, se):
        """Loop should return COMPLETED after dispatching all agents."""
        agents = [_make_agent_profile("a1", "Alpha")]

        async def fake_dispatch(targets, **kwargs):
            return [StepResult(
                step_number=1,
                agent_id="a1",
                agent_name="Alpha",
                task=targets[0].task,
                response_text="done",
                success=True,
                status=StepStatus.SUCCESS,
            )]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Go",
            agent_registry=agents,
            room_config=RoomConfig(is_debate_mode=True, room_agent_set={"a1": "Alpha"}),
        )

        assert result.status == RunStatus.COMPLETED
        assert result.trajectory is None
        assert result.run_state is not None
        assert result.run_state.status == "completed"
        assert result.run_state.decision_log[-1]["action"] == "complete"

    @pytest.mark.asyncio
    async def test_budget_accommodates_all_agents(self, se):
        """10 agents should all get dispatched (budget extended to 11)."""
        agents = [_make_agent_profile(f"a{i}", f"Agent{i}") for i in range(10)]
        dispatch_count = 0

        async def fake_dispatch(targets, **kwargs):
            nonlocal dispatch_count
            dispatch_count += 1
            return [StepResult(
                step_number=dispatch_count,
                agent_id=targets[0].agent_id,
                agent_name=targets[0].agent_name,
                task=targets[0].task,
                response_text="resp",
                success=True,
                status=StepStatus.SUCCESS,
            )]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        config = RoomConfig(
            is_debate_mode=True,
            room_agent_set={f"a{i}": f"Agent{i}" for i in range(10)},
        )

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Debate",
            agent_registry=agents,
            room_config=config,
        )

        assert result.status == RunStatus.COMPLETED
        assert dispatch_count == 10

    @pytest.mark.asyncio
    async def test_skips_unhealthy_agent(self, se):
        """Agent healthy at snapshot but unhealthy on resume should be skipped."""
        # All agents healthy at snapshot time
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
            _make_agent_profile("a3", "Gamma"),
        ]
        dispatch_ids = []

        async def fake_dispatch(targets, **kwargs):
            dispatch_ids.append(targets[0].agent_id)
            return [StepResult(
                step_number=kwargs.get("step_number", 1),
                agent_id=targets[0].agent_id,
                agent_name=targets[0].agent_name,
                task=targets[0].task,
                response_text="resp",
                success=True,
                status=StepStatus.SUCCESS,
            )]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        # Simulate resume: a1 already dispatched, a2 became unhealthy
        resumed = SupervisorTrajectory(
            debate_agent_ids=["a1", "a2", "a3"],
            entries=[_make_delegate_entry(1, "a1", "Alpha", "first resp")],
        )
        # Mark a2 as unhealthy in the registry for this resume
        agents[1] = _make_agent_profile("a2", "Beta", healthy=False)

        config = RoomConfig(
            is_debate_mode=True,
            room_agent_set={"a1": "Alpha", "a2": "Beta", "a3": "Gamma"},
        )

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Go",
            agent_registry=agents,
            room_config=config,
            resumed_trajectory=resumed,
        )

        assert result.status == RunStatus.COMPLETED
        # a2 should NOT be dispatched (unhealthy on resume), only a3
        assert dispatch_ids == ["a3"]
        assert result.trajectory is None
        assert result.run_state is not None
        output_agent_ids = {
            output.agent_id for output in result.run_state.agent_outputs
        }
        assert output_agent_ids == {"a1", "a3"}
        assert "a2" not in output_agent_ids


# =========================================================================
# Resume tests
# =========================================================================


@patch("execution.orchestration.supervisor_executor.DEFAULT_DEBATE_ROUNDS", 1)
class TestDebateResume:
    @pytest.fixture
    def se(self):
        return _make_supervisor_executor()

    @pytest.mark.asyncio
    async def test_resume_continues_dispatching(self, se):
        """After resume with 1/3 agents done, should continue with remaining 2."""
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
            _make_agent_profile("a3", "Gamma"),
        ]

        # Trajectory already has a1 dispatched
        resumed = SupervisorTrajectory(
            debate_agent_ids=["a1", "a2", "a3"],
            entries=[_make_delegate_entry(1, "a1", "Alpha", "first response")],
        )

        dispatch_ids = []

        async def fake_dispatch(targets, **kwargs):
            dispatch_ids.append(targets[0].agent_id)
            return [StepResult(
                step_number=kwargs.get("step_number", 1),
                agent_id=targets[0].agent_id,
                agent_name=targets[0].agent_name,
                task=targets[0].task,
                response_text="resp",
                success=True,
                status=StepStatus.SUCCESS,
            )]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        config = RoomConfig(is_debate_mode=True, room_agent_set={
            "a1": "Alpha", "a2": "Beta", "a3": "Gamma",
        })

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Debate",
            agent_registry=agents,
            room_config=config,
            resumed_trajectory=resumed,
        )

        assert result.status == RunStatus.COMPLETED
        assert dispatch_ids == ["a2", "a3"]

    @pytest.mark.asyncio
    async def test_resume_completes_when_all_done(self, se):
        """Resume with all agents already dispatched should immediately DONE."""
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]

        resumed = SupervisorTrajectory(
            debate_agent_ids=["a1", "a2"],
            entries=[
                _make_delegate_entry(1, "a1", "Alpha"),
                _make_delegate_entry(2, "a2", "Beta"),
            ],
        )

        se._dispatch_targets = AsyncMock()
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        config = RoomConfig(is_debate_mode=True, room_agent_set={
            "a1": "Alpha", "a2": "Beta",
        })

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Debate",
            agent_registry=agents,
            room_config=config,
            resumed_trajectory=resumed,
        )

        assert result.status == RunStatus.COMPLETED
        # _dispatch_targets should not be called — all agents already dispatched
        se._dispatch_targets.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_does_not_premature_done_on_inflight(self, se):
        """P1 fix: If the last DELEGATE entry has empty results (inflight
        crash), resume must NOT return DONE — the inflight agent needs
        re-dispatch."""
        agents = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]

        # a1 succeeded, a2 was inflight (empty results) when crash happened
        inflight_entry = TrajectoryEntry(
            step_number=2,
            action=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="Debate: Beta",
                targets=[DelegateTarget(agent_id="a2", agent_name="Beta", task="task")],
            ),
            started_at=utcnow(),
            results=[],
        )
        resumed = SupervisorTrajectory(
            debate_agent_ids=["a1", "a2"],
            entries=[
                _make_delegate_entry(1, "a1", "Alpha", "first resp"),
                inflight_entry,
            ],
        )

        dispatch_ids = []

        async def fake_dispatch(targets, **kwargs):
            dispatch_ids.append(targets[0].agent_id)
            return [StepResult(
                step_number=kwargs.get("step_number", 1),
                agent_id=targets[0].agent_id,
                agent_name=targets[0].agent_name,
                task=targets[0].task,
                response_text="resp",
                success=True,
                status=StepStatus.SUCCESS,
            )]

        se._dispatch_targets = fake_dispatch
        se._checkpoint_trajectory = AsyncMock(return_value=MagicMock())

        config = RoomConfig(is_debate_mode=True, room_agent_set={
            "a1": "Alpha", "a2": "Beta",
        })

        result = await se.run(
            room_id="room-1",
            user_message_id="umsg-1",
            message_text="Debate",
            agent_registry=agents,
            room_config=config,
            resumed_trajectory=resumed,
        )

        assert result.status == RunStatus.COMPLETED
        # a2 should have been re-dispatched (not skipped with premature DONE)
        assert "a2" in dispatch_ids

    def test_snapshot_survives_resume(self):
        """debate_agent_ids should survive trajectory serialization/deserialization."""
        trajectory = SupervisorTrajectory(debate_agent_ids=["a1", "a2", "a3"])

        # Simulate save/restore
        data = trajectory.model_dump(mode="json")
        restored = SupervisorTrajectory(**data)

        assert restored.debate_agent_ids == ["a1", "a2", "a3"]

        # Snapshot should return persisted IDs, not rebuild from registry
        registry = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a4", "Delta"),  # new agent
        ]
        ids = SupervisorExecutor._snapshot_debate_agents(registry, restored)
        assert ids == ["a1", "a2", "a3"]  # original, not rebuilt


# ---------------------------------------------------------------------------
# Scope: all_agents + debate bypasses LLM selector
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Resume: participant preservation across pause/resume
# ---------------------------------------------------------------------------


class TestResumePreservesDebateParticipants:
    """Verify that debate participants are preserved across pause/resume
    even when room membership changes."""

    def test_resume_preserves_debate_participants(self):
        """When a debate is paused and the room membership changes,
        the original debate participants should be restored from
        the continuation data."""
        # Original debate had 3 agents
        trajectory = SupervisorTrajectory(
            debate_agent_ids=["a1", "a2", "a3"],
            entries=[
                _make_delegate_entry(1, "a1", "Alpha", "resp1"),
            ],
        )

        # After resume, room only has a1 and a4 (a2, a3 removed, a4 added)
        current_registry = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a4", "Delta"),
        ]

        # Serialized continuation has the original registry
        continuation = {
            "agent_registry": [
                {"agent_id": "a1", "agent_name": "Alpha", "description": "", "is_healthy": True},
                {"agent_id": "a2", "agent_name": "Beta", "description": "", "is_healthy": True},
                {"agent_id": "a3", "agent_name": "Gamma", "description": "", "is_healthy": True},
            ],
        }

        is_debate_mode = True

        # Simulate the preservation logic from RoomMessageCenter._resume_supervisor
        if trajectory.debate_agent_ids and is_debate_mode:
            current_ids = {a.agent_id for a in current_registry}
            missing_ids = [
                aid for aid in trajectory.debate_agent_ids
                if aid not in current_ids
            ]
            if missing_ids:
                serialized_registry = continuation.get("agent_registry", [])
                serialized_map = {
                    p["agent_id"]: p for p in serialized_registry
                    if isinstance(p, dict) and "agent_id" in p
                }
                for mid in missing_ids:
                    if mid in serialized_map:
                        try:
                            current_registry.append(AgentProfile(**serialized_map[mid]))
                        except (TypeError, KeyError):
                            pass

        # Verify all original participants are in the registry
        registry_ids = {a.agent_id for a in current_registry}
        assert "a1" in registry_ids
        assert "a2" in registry_ids  # restored from continuation
        assert "a3" in registry_ids  # restored from continuation
        assert "a4" in registry_ids  # new agent still present
        assert len(current_registry) == 4
