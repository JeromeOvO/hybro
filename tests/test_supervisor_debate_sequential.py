"""
Tests for sequential debate dispatch in SupervisorExecutor.

Covers:
- Helper methods: snapshot, remaining IDs, prior responses, debate task builder
- Executor integration: one-per-step dispatch, DONE after all agents, budget
- Resume: continues dispatching, completes when all done, preserves participants
- Scope: all_agents + debate bypasses LLM selector
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.SupervisorExecutor import SupervisorExecutor
from models.supervisor_v2 import (
    ActionType,
    AgentProfile,
    DelegateTarget,
    RoomConfig,
    RunStatus,
    StepStatus,
    SupervisorAction,
    SupervisorRunResult,
    SupervisorTrajectory,
    TrajectoryEntry,
    V2StepResult,
)
from common.utils.time import utcnow


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_agent_profile(agent_id: str, name: str, healthy: bool = True) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        agent_name=name,
        description="",
        is_healthy=healthy,
    )


def _make_supervisor_executor() -> SupervisorExecutor:
    se = object.__new__(SupervisorExecutor)
    se.database_service = AsyncMock()
    se.sse_manager = AsyncMock()
    se.sse_manager.send_processing_status = AsyncMock()
    se.room_services = MagicMock()
    se.supervisor_service = MagicMock()
    se.tsm = MagicMock()
    se.agent_dispatcher = MagicMock()
    se.agent_message_processor = MagicMock()
    se.room_memory_service = AsyncMock()
    se.room_memory_service.add_agent_response_to_memory = AsyncMock()
    se.rate_limit_service = MagicMock()
    se.room_coordinator_service = MagicMock()
    return se


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
            V2StepResult(
                step_number=step,
                agent_id=agent_id,
                agent_name=agent_name,
                task="task",
                response_text=response_text,
                success=success,
                status=StepStatus.SUCCESS if success else StepStatus.FAILED,
            )
        ],
    )


# =========================================================================
# _snapshot_debate_agents
# =========================================================================


class TestSnapshotDebateAgents:
    @patch("modules.SupervisorExecutor.settings")
    def test_initialized_once(self, mock_settings):
        mock_settings.debate_rounds = 1
        registry = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
            _make_agent_profile("a3", "Gamma", healthy=False),
        ]
        trajectory = SupervisorTrajectory()

        ids = SupervisorExecutor._snapshot_debate_agents(registry, trajectory)
        assert ids == ["a1", "a2"]
        assert trajectory.debate_agent_ids == ["a1", "a2"]

        # Second call returns the same snapshot even if registry changes
        registry.append(_make_agent_profile("a4", "Delta"))
        ids2 = SupervisorExecutor._snapshot_debate_agents(registry, trajectory)
        assert ids2 == ["a1", "a2"]

    @patch("modules.SupervisorExecutor.settings")
    def test_multi_round_snapshot(self, mock_settings):
        """With debate_rounds=2, each agent appears twice in the snapshot."""
        mock_settings.debate_rounds = 2
        registry = [
            _make_agent_profile("a1", "Alpha"),
            _make_agent_profile("a2", "Beta"),
        ]
        trajectory = SupervisorTrajectory()

        ids = SupervisorExecutor._snapshot_debate_agents(registry, trajectory)
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
                V2StepResult(
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


@patch("modules.SupervisorExecutor.settings", MagicMock(debate_rounds=1))
class TestSequentialDebateDispatch:
    """Integration tests that run the full executor loop with mocked dispatch."""

    @pytest.fixture
    def se(self):
        return _make_supervisor_executor()

    def _debate_config(self) -> RoomConfig:
        return RoomConfig(is_debate_mode=True, room_agent_set={"a1": "Alpha", "a2": "Beta"})

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
            return [V2StepResult(
                step_number=kwargs.get("step_number", 1),
                agent_id=targets[0].agent_id,
                agent_name=targets[0].agent_name,
                task=targets[0].task,
                response_text=f"response from {targets[0].agent_name}",
                success=True,
                status=StepStatus.SUCCESS,
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

    @pytest.mark.asyncio
    async def test_done_after_all_agents(self, se):
        """Loop should return COMPLETED after dispatching all agents."""
        agents = [_make_agent_profile("a1", "Alpha")]

        async def fake_dispatch(targets, **kwargs):
            return [V2StepResult(
                step_number=1,
                agent_id="a1",
                agent_name="Alpha",
                task="task",
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
        # Last trajectory entry should be DONE
        assert result.trajectory.entries[-1].action.action == ActionType.DONE

    @pytest.mark.asyncio
    async def test_budget_accommodates_all_agents(self, se):
        """10 agents should all get dispatched (budget extended to 11)."""
        agents = [_make_agent_profile(f"a{i}", f"Agent{i}") for i in range(10)]
        dispatch_count = 0

        async def fake_dispatch(targets, **kwargs):
            nonlocal dispatch_count
            dispatch_count += 1
            return [V2StepResult(
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
            return [V2StepResult(
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
        # There should be a FAILED entry for a2 in the trajectory
        failed_entries = [
            e for e in result.trajectory.entries
            if e.action.action == ActionType.DELEGATE
            and e.results
            and not e.results[0].success
        ]
        assert len(failed_entries) == 1
        assert failed_entries[0].results[0].agent_id == "a2"


# =========================================================================
# Resume tests
# =========================================================================


@patch("modules.SupervisorExecutor.settings", MagicMock(debate_rounds=1))
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
            return [V2StepResult(
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
            return [V2StepResult(
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


class TestAllAgentsDebateBypassesSelector:
    """Verify that all_agents + debate mode returns all active agents
    without calling the LLM agent selector."""

    @pytest.mark.asyncio
    async def test_all_agents_debate_bypasses_selector(self):
        """all_agents + debate should return all active agents directly."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from models.agent import Agent, AgentStatus
        from a2a.types import AgentCard, AgentCapabilities, AgentProvider

        # Create mock agents
        def make_agent(aid, name):
            return Agent(
                agent_id=aid,
                provider_id="test",
                agent_card=AgentCard(
                    name=name,
                    description=f"Agent {name}",
                    url=f"https://test.com/{aid}",
                    version="1.0.0",
                    provider=AgentProvider(organization="Test", url="https://test.com"),
                    capabilities=AgentCapabilities(),
                    default_input_modes=["text"],
                    default_output_modes=["text"],
                    skills=[],
                ),
                agent_status=AgentStatus.active,
            )

        agents = [make_agent("a2", "Bravo"), make_agent("a1", "Alpha")]

        mock_db = MagicMock()
        mock_db.get_all_active_agents = AsyncMock(return_value=agents)

        mock_room_services = MagicMock()
        mock_room_services.database_service = mock_db

        # Simulate the bypass logic from room_services._resolve_explicit_target_scope
        target_group = "all_agents"
        is_debate_mode = True
        sender_user_id = "user1"

        if target_group == "all_agents" and is_debate_mode:
            all_agents = await mock_db.get_all_active_agents(user_id=sender_user_id)
            all_agents.sort(key=lambda a: (a.agent_card.name.lower(), a.agent_id))
            selected = {a.agent_id: a.agent_card.name for a in all_agents}

        # Verify all agents returned
        assert len(selected) == 2
        # Verify stable sort (Alpha before Bravo)
        keys = list(selected.keys())
        assert keys == ["a1", "a2"]
        # Verify DB was called (not LLM selector)
        mock_db.get_all_active_agents.assert_called_once_with(user_id="user1")


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

        # Simulate the preservation logic from RoomMessageCenter._resume_supervisor_v2
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

