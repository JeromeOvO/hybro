"""
Tests for Supervisor Improvements:
- Part 1: Expanded response preview + quality evaluation prompt
- Part 2: SSE stage notifications in SupervisorExecutor
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.supervisor import (
    ActionType,
    AgentProfile,
    DelegateTarget,
    RoomConfig,
    RunStatus,
    StepStatus,
    SupervisorAction,
    SupervisorTrajectory,
    TrajectoryEntry,
    StepResult,
)
from modules.SupervisorExecutor import SupervisorExecutor
from services.room_supervisor_service import RoomSupervisorService

# =============================================================================
# Part 1a: Trajectory response preview (3000-char cap)
# =============================================================================


class TestTrajectoryResponsePreview:
    """Verify _format_trajectory uses 3000-char preview (not 500)."""

    def _make_trajectory_with_response(self, response_text: str) -> SupervisorTrajectory:
        trajectory = SupervisorTrajectory()
        entry = TrajectoryEntry(
            step_number=1,
            started_at=datetime.now(UTC),
            action=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="test",
                targets=[
                    DelegateTarget(
                        agent_id="agent-1",
                        agent_name="TestAgent",
                        task="do something",
                    )
                ],
            ),
        )
        entry.results = [
            StepResult(
                step_number=1,
                agent_id="agent-1",
                agent_name="TestAgent",
                task="do something",
                success=True,
                status=StepStatus.SUCCESS,
                response_text=response_text,
            )
        ]
        trajectory.entries.append(entry)
        return trajectory

    def test_short_response_not_truncated(self):
        """A response under 3000 chars should appear in full."""
        text = "x" * 2000
        trajectory = self._make_trajectory_with_response(text)
        formatted = RoomSupervisorService._format_trajectory(trajectory)
        assert text in formatted
        assert "truncated" not in formatted

    def test_response_at_3000_chars_not_truncated(self):
        """A response of exactly 3000 chars should not be truncated."""
        text = "y" * 3000
        trajectory = self._make_trajectory_with_response(text)
        formatted = RoomSupervisorService._format_trajectory(trajectory)
        assert text in formatted
        assert "truncated" not in formatted

    def test_response_over_3000_chars_truncated(self):
        """A response over 3000 chars should be truncated with length note."""
        text = "z" * 5000
        trajectory = self._make_trajectory_with_response(text)
        formatted = RoomSupervisorService._format_trajectory(trajectory)
        assert "z" * 3000 in formatted
        assert "z" * 3001 not in formatted
        assert "truncated" in formatted
        assert "5000" in formatted

    def test_old_500_limit_no_longer_applies(self):
        """A 1500-char response must NOT be truncated (old limit was 500)."""
        text = "a" * 1500
        trajectory = self._make_trajectory_with_response(text)
        formatted = RoomSupervisorService._format_trajectory(trajectory)
        assert text in formatted
        assert "truncated" not in formatted


# =============================================================================
# Part 1b: Quality evaluation prompt
# =============================================================================


class TestQualityEvaluationPrompt:
    """Verify the system prompt includes quality evaluation instructions."""

    def test_system_prompt_contains_quality_evaluation_block(self):
        from services.room_supervisor_service import SUPERVISOR_SYSTEM_PROMPT

        assert "QUALITY EVALUATION" in SUPERVISOR_SYSTEM_PROMPT
        assert "unsatisfactory" in SUPERVISOR_SYSTEM_PROMPT

    def test_system_prompt_mentions_re_delegation_criteria(self):
        from services.room_supervisor_service import SUPERVISOR_SYSTEM_PROMPT

        assert "couldn't\n  find anything" in SUPERVISOR_SYSTEM_PROMPT or \
               "couldn't find anything" in SUPERVISOR_SYSTEM_PROMPT or \
               "couldn" in SUPERVISOR_SYSTEM_PROMPT


# =============================================================================
# Part 2: SSE stage notifications in SupervisorExecutor
# =============================================================================


def _make_executor() -> SupervisorExecutor:
    """Create a SupervisorExecutor with all dependencies mocked."""
    se = object.__new__(SupervisorExecutor)
    se.database_service = AsyncMock()
    se.sse_manager = AsyncMock()
    se.room_services = MagicMock()
    se.supervisor_service = AsyncMock()
    se.tsm = MagicMock()
    se.agent_dispatcher = MagicMock()
    se.agent_message_processor = MagicMock()
    se.room_memory_service = AsyncMock()
    se.rate_limit_service = MagicMock()
    se.room_coordinator_service = MagicMock()
    se.MAX_STEPS = 8
    se._emitted_status_details = []

    async def emit_processing_status(**kwargs):
        detail = kwargs.get("details") or kwargs.get("legacy_details")
        if detail:
            se._emitted_status_details.append(detail)

    se.bind_execution_event_deps(emit_processing_status)
    return se


def _get_sse_details(se: SupervisorExecutor) -> list[str]:
    """Extract stage details emitted through the execution event boundary."""
    return se._emitted_status_details


class TestSupervisorSSEStageNotifications:
    """Verify send_processing_status is called with correct stage details."""

    @pytest.mark.asyncio
    async def test_planning_status_emitted_on_done(self):
        """'Planning next action...' emitted even when supervisor says DONE immediately."""
        se = _make_executor()
        se.supervisor_service.decide_next.return_value = SupervisorAction(
            action=ActionType.DONE,
            reasoning="already answered",
        )

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[AgentProfile(agent_id="a1", agent_name="Agent1")],
            room_config=RoomConfig(),
        )

        details = _get_sse_details(se)
        assert "Planning next action..." in details

    @pytest.mark.asyncio
    async def test_delegating_status_with_agent_count(self):
        """'Delegating to N agent(s)...' emitted with correct count."""
        se = _make_executor()
        se.supervisor_service.decide_next.side_effect = [
            SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="delegate",
                targets=[
                    DelegateTarget(agent_id="a1", agent_name="Agent1", task="t1"),
                    DelegateTarget(agent_id="a2", agent_name="Agent2", task="t2"),
                    DelegateTarget(agent_id="a3", agent_name="Agent3", task="t3"),
                ],
            ),
            SupervisorAction(action=ActionType.DONE, reasoning="done"),
        ]
        se._dispatch_targets = AsyncMock(return_value=[
            StepResult(
                step_number=1, agent_id="a1", agent_name="Agent1",
                task="t1", success=True, status=StepStatus.SUCCESS,
                response_text="result",
            ),
            StepResult(
                step_number=1, agent_id="a2", agent_name="Agent2",
                task="t2", success=True, status=StepStatus.SUCCESS,
                response_text="result",
            ),
            StepResult(
                step_number=1, agent_id="a3", agent_name="Agent3",
                task="t3", success=True, status=StepStatus.SUCCESS,
                response_text="result",
            ),
        ])
        se._checkpoint_trajectory = AsyncMock(return_value=None)

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[
                AgentProfile(agent_id="a1", agent_name="Agent1"),
                AgentProfile(agent_id="a2", agent_name="Agent2"),
                AgentProfile(agent_id="a3", agent_name="Agent3"),
            ],
            room_config=RoomConfig(),
        )

        details = _get_sse_details(se)
        assert "Delegating to 3 agent(s)..." in details

    @pytest.mark.asyncio
    async def test_evaluating_status_emitted_after_dispatch(self):
        """'Evaluating agent results...' emitted after dispatch completes."""
        se = _make_executor()
        se.supervisor_service.decide_next.side_effect = [
            SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="delegate",
                targets=[
                    DelegateTarget(agent_id="a1", agent_name="Agent1", task="t1"),
                ],
            ),
            SupervisorAction(action=ActionType.DONE, reasoning="done"),
        ]
        se._dispatch_targets = AsyncMock(return_value=[
            StepResult(
                step_number=1, agent_id="a1", agent_name="Agent1",
                task="t1", success=True, status=StepStatus.SUCCESS,
                response_text="result",
            ),
        ])
        se._checkpoint_trajectory = AsyncMock(return_value=None)

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[AgentProfile(agent_id="a1", agent_name="Agent1")],
            room_config=RoomConfig(),
        )

        details = _get_sse_details(se)
        assert "Evaluating agent results..." in details

    @pytest.mark.asyncio
    async def test_synthesizing_status_emitted_before_synthesis(self):
        """'Synthesizing responses...' emitted before synthesis call."""
        se = _make_executor()
        se.supervisor_service.decide_next.side_effect = [
            SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="delegate",
                targets=[
                    DelegateTarget(agent_id="a1", agent_name="Agent1", task="t1"),
                ],
            ),
            SupervisorAction(
                action=ActionType.SYNTHESIZE,
                reasoning="synthesize",
                synthesis_instruction="combine",
            ),
        ]
        se._dispatch_targets = AsyncMock(return_value=[
            StepResult(
                step_number=1, agent_id="a1", agent_name="Agent1",
                task="t1", success=True, status=StepStatus.SUCCESS,
                response_text="result",
            ),
        ])
        se._checkpoint_trajectory = AsyncMock(return_value=None)
        se.database_service.resolve_client_request_id_for_message_id = AsyncMock(
            return_value=None
        )

        async def synthesize_stream(trajectory, synthesis_instruction):
            yield "synthesized"

        se.supervisor_service.synthesize_stream = synthesize_stream

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[AgentProfile(agent_id="a1", agent_name="Agent1")],
            room_config=RoomConfig(),
        )

        details = _get_sse_details(se)
        assert "Synthesizing responses..." in details

    @pytest.mark.asyncio
    async def test_full_stage_sequence_delegate_then_done(self):
        """A delegate→done flow should emit: planning, delegating, evaluating, planning."""
        se = _make_executor()
        se.supervisor_service.decide_next.side_effect = [
            SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="delegate",
                targets=[
                    DelegateTarget(agent_id="a1", agent_name="Agent1", task="t1"),
                ],
            ),
            SupervisorAction(action=ActionType.DONE, reasoning="done"),
        ]
        se._dispatch_targets = AsyncMock(return_value=[
            StepResult(
                step_number=1, agent_id="a1", agent_name="Agent1",
                task="t1", success=True, status=StepStatus.SUCCESS,
                response_text="result",
            ),
        ])
        se._checkpoint_trajectory = AsyncMock(return_value=None)

        await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[AgentProfile(agent_id="a1", agent_name="Agent1")],
            room_config=RoomConfig(),
        )

        details = _get_sse_details(se)
        assert details == [
            "Planning next action...",
            "Delegating to 1 agent(s)...",
            "Evaluating agent results...",
            "Planning next action...",  # second loop iteration before DONE
        ]

    @pytest.mark.asyncio
    async def test_sse_failure_does_not_crash_loop(self):
        """If send_processing_status raises, the supervisor loop continues."""
        se = _make_executor()
        se.bind_execution_event_deps(AsyncMock(side_effect=Exception("SSE down")))
        se.supervisor_service.decide_next.return_value = SupervisorAction(
            action=ActionType.DONE,
            reasoning="done",
        )

        # Should not raise
        result = await se.run(
            room_id="room-1",
            user_message_id="msg-1",
            message_text="hello",
            agent_registry=[AgentProfile(agent_id="a1", agent_name="Agent1")],
            room_config=RoomConfig(),
        )
        # DONE without prior DELEGATE results in FAILED (no agents delegated)
        assert result.status in (RunStatus.COMPLETED, RunStatus.FAILED)
