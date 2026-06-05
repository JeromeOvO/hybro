"""
Unit tests for RoomSupervisorService._guard_consecutive_redelegation.

Covers:
- No offenders → original action returned unchanged
- Mixed targets (offender + new agent) → DELEGATE with only non-offenders
- All targets are offenders → DONE
- Multiple offenders with one non-offender → DELEGATE with the survivor
- Below threshold → no filtering
- Consecutive failure guard — strips agents that keep failing
"""

from datetime import UTC, datetime

from execution.orchestration.room_supervisor_service import RoomSupervisorService
from models.supervisor import (
    ActionType,
    DelegateTarget,
    StepResult,
    SupervisorAction,
    SupervisorTrajectory,
    TrajectoryEntry,
)


def _make_service() -> RoomSupervisorService:
    return RoomSupervisorService(openai_service=None, database_service=None)


def _target(agent_id: str, name: str, task: str = "do something") -> DelegateTarget:
    return DelegateTarget(agent_id=agent_id, agent_name=name, task=task)


def _entry_with_successes(agent_ids: list[str]) -> TrajectoryEntry:
    """Create a trajectory entry that delegated to the given agents, all successful."""
    now = datetime.now(tz=UTC)
    return TrajectoryEntry(
        step_number=1,
        action=SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="test",
            targets=[_target(aid, f"Agent-{aid}") for aid in agent_ids],
        ),
        results=[
            StepResult(
                step_number=1,
                agent_id=aid,
                agent_name=f"Agent-{aid}",
                task="test",
                response_text="ok",
                success=True,
            )
            for aid in agent_ids
        ],
        started_at=now,
        completed_at=now,
    )


def _entry_with_failures(agent_ids: list[str]) -> TrajectoryEntry:
    """Create a trajectory entry that delegated to the given agents, all failed."""
    now = datetime.now(tz=UTC)
    return TrajectoryEntry(
        step_number=1,
        action=SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="test",
            targets=[_target(aid, f"Agent-{aid}") for aid in agent_ids],
        ),
        results=[
            StepResult(
                step_number=1,
                agent_id=aid,
                agent_name=f"Agent-{aid}",
                task="test",
                response_text="error",
                success=False,
            )
            for aid in agent_ids
        ],
        started_at=now,
        completed_at=now,
    )


class TestGuardNoOffenders:
    def test_returns_action_unchanged_when_no_prior_history(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="go",
            targets=[_target("A", "Alpha")],
        )
        trajectory = SupervisorTrajectory(entries=[])
        result = svc._guard_consecutive_redelegation(action, trajectory)
        assert result is action

    def test_returns_action_unchanged_when_below_threshold(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="go",
            targets=[_target("A", "Alpha")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_successes(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)
        assert result is action


class TestGuardPartialFiltering:
    """When some targets are offenders but others are not, the guard should
    strip offenders and return DELEGATE with only the remaining targets."""

    def test_offender_A_plus_new_C_delegates_to_C_only(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="need both",
            targets=[_target("A", "Alpha"), _target("C", "Charlie")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_successes(["A"]),
            _entry_with_successes(["A"]),
            _entry_with_successes(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)

        assert result.action == ActionType.DELEGATE
        assert len(result.targets) == 1
        assert result.targets[0].agent_id == "C"
        assert result.targets[0].agent_name == "Charlie"
        assert "Alpha" in result.reasoning

    def test_two_offenders_one_survivor(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="all three",
            targets=[_target("A", "Alpha"), _target("B", "Bravo"), _target("C", "Charlie")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_successes(["A", "B"]),
            _entry_with_successes(["A", "B"]),
            _entry_with_successes(["A", "B"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)

        assert result.action == ActionType.DELEGATE
        assert len(result.targets) == 1
        assert result.targets[0].agent_id == "C"


class TestGuardAllOffenders:
    """When every proposed target is an offender, the guard should return DONE."""

    def test_single_offender_returns_done(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="again",
            targets=[_target("A", "Alpha")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_successes(["A"]),
            _entry_with_successes(["A"]),
            _entry_with_successes(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)

        assert result.action == ActionType.DONE
        assert "Alpha" in result.reasoning

    def test_multiple_offenders_returns_done(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="again",
            targets=[_target("A", "Alpha"), _target("B", "Bravo")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_successes(["A", "B"]),
            _entry_with_successes(["A", "B"]),
            _entry_with_successes(["A", "B"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)

        assert result.action == ActionType.DONE
        assert "Alpha" in result.reasoning
        assert "Bravo" in result.reasoning


class TestGuardCustomThreshold:
    def test_below_custom_threshold_returns_unchanged(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="retry",
            targets=[_target("A", "Alpha")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_successes(["A"]),
            _entry_with_successes(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory, max_consecutive=3)
        assert result is action

    def test_at_custom_threshold_returns_done(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="retry",
            targets=[_target("A", "Alpha")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_successes(["A"]),
            _entry_with_successes(["A"]),
            _entry_with_successes(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory, max_consecutive=3)
        assert result.action == ActionType.DONE


class TestGuardTrajectoryBreaks:
    """The consecutive count should break when a non-DELEGATE entry or a
    non-matching entry is encountered."""

    def test_non_delegate_entry_breaks_chain(self):
        svc = _make_service()
        now = datetime.now(tz=UTC)
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="go",
            targets=[_target("A", "Alpha")],
        )
        done_entry = TrajectoryEntry(
            step_number=1,
            action=SupervisorAction(action=ActionType.DONE, reasoning="done"),
            results=[],
            started_at=now,
            completed_at=now,
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_successes(["A"]),
            _entry_with_successes(["A"]),
            done_entry,
            _entry_with_successes(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)
        assert result is action


# ============================================================
# Consecutive FAILURE guard tests
# ============================================================


class TestFailureGuardSingleAgent:
    """When a single agent fails repeatedly, it should be stopped."""

    def test_single_agent_two_consecutive_failures_returns_done(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="retry",
            targets=[_target("A", "Alpha")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_failures(["A"]),
            _entry_with_failures(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)
        assert result.action == ActionType.DONE
        assert "Alpha" in result.reasoning

    def test_single_failure_below_threshold_is_allowed(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="retry",
            targets=[_target("A", "Alpha")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_failures(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)
        assert result is action


class TestFailureGuardPartialFiltering:
    """When some agents fail and others are new, only failed agents are stripped."""

    def test_failing_A_plus_new_C_delegates_to_C_only(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="need both",
            targets=[_target("A", "Alpha"), _target("C", "Charlie")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_failures(["A"]),
            _entry_with_failures(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(action, trajectory)
        assert result.action == ActionType.DELEGATE
        assert len(result.targets) == 1
        assert result.targets[0].agent_id == "C"


class TestFailureGuardCustomThreshold:
    def test_custom_failure_threshold(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="retry",
            targets=[_target("A", "Alpha")],
        )
        trajectory = SupervisorTrajectory(entries=[
            _entry_with_failures(["A"]),
            _entry_with_failures(["A"]),
        ])
        result = svc._guard_consecutive_redelegation(
            action, trajectory, max_consecutive_failures=3,
        )
        assert result is action


class TestFailureAndSuccessGuardsCombined:
    """Both guards should work in sequence: failure guard first, then success guard."""

    def test_failure_offender_stripped_before_success_check(self):
        svc = _make_service()
        action = SupervisorAction(
            action=ActionType.DELEGATE,
            reasoning="delegate",
            targets=[
                _target("A", "Alpha"),
                _target("B", "Bravo"),
                _target("C", "Charlie"),
            ],
        )
        now = datetime.now(tz=UTC)
        mixed_entry = TrajectoryEntry(
            step_number=1,
            action=SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning="test",
                targets=[
                    _target("A", "Alpha"),
                    _target("B", "Bravo"),
                ],
            ),
            results=[
                StepResult(
                    step_number=1, agent_id="A", agent_name="Alpha",
                    task="test", response_text="err", success=False,
                ),
                StepResult(
                    step_number=1, agent_id="B", agent_name="Bravo",
                    task="test", response_text="ok", success=True,
                ),
            ],
            started_at=now, completed_at=now,
        )
        trajectory = SupervisorTrajectory(entries=[mixed_entry, mixed_entry])
        result = svc._guard_consecutive_redelegation(action, trajectory)
        assert result.action == ActionType.DELEGATE
        target_ids = {t.agent_id for t in result.targets}
        assert "A" not in target_ids
        assert "C" in target_ids
