from execution.orchestration.recovery_policy import normalize_delegate_repair_lineage
from execution.orchestration.goal_fingerprinting import target_goal_fingerprints
from models.orchestration import (
    DelegationOutcomeRecord,
    DispatchExpectedOutput,
    DispatchIntent,
    OrchestrationRunState,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)


def _target():
    return PlannedDelegateTarget(
        agent_id="agent-1",
        task="Produce quote.",
        expected_outputs=[
            DispatchExpectedOutput(output_key="quote", kind="artifact", required=True)
        ],
    )


def _state(*, status="partial", goal_revision_fingerprint=None):
    expected_outputs = [
        DispatchExpectedOutput(output_key="quote", kind="artifact", required=True)
    ]
    fingerprints = target_goal_fingerprints(_target(), {})
    goal_revision_fingerprint = (
        fingerprints.goal_revision_fingerprint
        if goal_revision_fingerprint is None
        else goal_revision_fingerprint
    )
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="produce quote",
        candidate_agent_ids=["agent-1"],
        dispatch_intents=[
            DispatchIntent(
                step_id="step-1",
                step_target_id="target-1",
                dispatch_intent_id="intent-1",
                planned_agent_message_id="agent-msg-1",
                agent_id="agent-1",
                task="Produce quote.",
                task_hash="hash-1",
                goal_family_fingerprint=fingerprints.goal_family_fingerprint,
                goal_revision_fingerprint=goal_revision_fingerprint,
                expected_outputs=expected_outputs,
            )
        ],
        delegation_outcomes=[
            DelegationOutcomeRecord(
                outcome_id="outcome-1",
                dispatch_intent_id="intent-1",
                agent_id="agent-1",
                goal_family_fingerprint=fingerprints.goal_family_fingerprint,
                goal_revision_fingerprint=goal_revision_fingerprint,
                attempt_fingerprint="attempt-1",
                status=status,
                remaining_required_obligations=["quote:$present"],
            )
        ],
    )


def test_normalizes_missing_repair_of_intent_for_same_agent_unfulfilled_revision():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="repair",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(action, _state(), {})

    assert normalized.targets[0].repair_of_intent_id == "intent-1"
    assert action.targets[0].repair_of_intent_id is None


def test_does_not_set_repair_lineage_for_failed_operational_retry():
    state = _state(status="failed")
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="retry",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(action, state, {})

    assert normalized.targets[0].repair_of_intent_id is None


def test_does_not_set_repair_lineage_for_blocked_user_input():
    state = _state(status="blocked")
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="repeat blocked work",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(action, state, {})

    assert normalized.targets[0].repair_of_intent_id is None


def test_normalizes_missing_repair_of_intent_for_no_progress_revision():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="repair no progress",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(
        action, _state(status="no_progress"), {}
    )

    assert normalized.targets[0].repair_of_intent_id == "intent-1"


def test_does_not_set_repair_lineage_for_same_shape_different_recorded_revision():
    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="new goal revision",
        targets=[_target()],
    )

    normalized = normalize_delegate_repair_lineage(
        action,
        _state(goal_revision_fingerprint="historical-revision"),
        {},
    )

    assert normalized.targets[0].repair_of_intent_id is None
