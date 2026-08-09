import pytest

from execution.orchestration.completion_policy import (
    CompletionPolicyError,
    FinalizationMode,
    determine_finalization_mode,
)
from execution.orchestration.run_reducer import mark_terminal
from models.orchestration import (
    ActiveDispatchRef,
    AgentOutputRecord,
    CompletionEvidence,
    DelegationOutcomeRecord,
    GoalProgressRecord,
    OrchestrationRunState,
    OrchestrationStatus,
    PlannerActionType,
)


def _state(*outputs: tuple[str, str]) -> OrchestrationRunState:
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="message-1",
        goal="answer",
        candidate_agent_ids=[agent_id for agent_id, _ in outputs],
        status=OrchestrationStatus.RUNNING,
        agent_outputs=[
            AgentOutputRecord(
                agent_message_id=message_id,
                agent_id=agent_id,
                status="completed",
                text=f"result from {agent_id}",
            )
            for agent_id, message_id in outputs
        ],
    )


def test_platform_answer_never_requires_agent_output() -> None:
    assert (
        determine_finalization_mode(_state(), PlannerActionType.PLATFORM_ANSWER)
        == FinalizationMode.PLATFORM
    )


def test_one_successful_agent_is_direct_and_two_are_synthesized() -> None:
    assert (
        determine_finalization_mode(
            _state(("agent-1", "message-1")), PlannerActionType.COMPLETE
        )
        == FinalizationMode.DIRECT_AGENT
    )
    assert (
        determine_finalization_mode(
            _state(("agent-1", "message-1"), ("agent-2", "message-2")),
            PlannerActionType.COMPLETE,
        )
        == FinalizationMode.SYNTHESIS
    )


def test_complete_rejects_pending_dispatch() -> None:
    state = _state(("agent-1", "message-1"))
    state.active_dispatches = [
        ActiveDispatchRef(agent_message_id="pending", agent_id="agent-1", status="sent")
    ]
    try:
        determine_finalization_mode(state, PlannerActionType.COMPLETE)
    except CompletionPolicyError as exc:
        assert "dispatches are pending" in str(exc)
    else:
        raise AssertionError("completion gate accepted a pending dispatch")


def test_complete_rejects_partial_outcome_without_completion_evidence() -> None:
    state = _state(("agent-1", "message-1"))
    state.delegation_outcomes = [
        DelegationOutcomeRecord(
            outcome_id="outcome-1",
            dispatch_intent_id="intent-1",
            agent_id="agent-1",
            goal_family_fingerprint="family-1",
            goal_revision_fingerprint="revision-1",
            attempt_fingerprint="attempt-1",
            status="partial",
            remaining_required_obligations=["quote:$present"],
        )
    ]

    with pytest.raises(CompletionPolicyError, match="remaining required obligations"):
        determine_finalization_mode(state, PlannerActionType.COMPLETE)


def test_complete_rejects_goal_progress_gap_even_after_fulfilled_outcome() -> None:
    state = _state(("agent-1", "message-1"))
    state.goal_progress = [
        GoalProgressRecord(
            progress_id="progress-1",
            goal_family_fingerprint="family-1",
            through_goal_revision_fingerprint="revision-1",
            latest_outcome_id="outcome-1",
            status="partial",
            remaining_required_obligations=["quote:requested_limit"],
        )
    ]

    with pytest.raises(CompletionPolicyError, match="quote:requested_limit"):
        determine_finalization_mode(state, PlannerActionType.COMPLETE)


def test_completion_evidence_can_satisfy_a_matching_required_gap() -> None:
    state = _state(("agent-1", "message-1"))
    state.goal_progress = [
        GoalProgressRecord(
            progress_id="progress-1",
            goal_family_fingerprint="family-1",
            through_goal_revision_fingerprint="revision-1",
            latest_outcome_id="outcome-1",
            status="partial",
            remaining_required_obligations=["quote:$present"],
        )
    ]
    evidence = CompletionEvidence(
        satisfied_criteria=["Quote returned."],
        final_answer_intent="Return the quote.",
        confidence=1.0,
        satisfied_output_keys=["quote"],
    )

    assert (
        determine_finalization_mode(
            state,
            PlannerActionType.COMPLETE,
            completion_evidence=evidence,
        )
        == FinalizationMode.DIRECT_AGENT
    )


def test_cancel_is_allowed_from_finalizing() -> None:
    state = _state(("agent-1", "message-1"))
    state.status = OrchestrationStatus.FINALIZING
    canceled = mark_terminal(
        state,
        OrchestrationStatus.CANCELED,
        reason="user canceled",
    )
    assert canceled.status == OrchestrationStatus.CANCELED
