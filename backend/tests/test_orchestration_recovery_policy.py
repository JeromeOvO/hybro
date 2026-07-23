from execution.orchestration.action_validator import PlannerActionValidator
from execution.orchestration.blocker_resolver import validate_hitl_answered_blockers
from execution.orchestration.goal_fingerprinting import target_goal_fingerprints
from execution.orchestration.goal_progress import rebuild_goal_progress
from execution.orchestration.recovery_policy import (
    action_for_rejected_delegate,
    normalize_delegate_repair_lineage,
    recovery_directives,
)
from models.orchestration import (
    BlockerRecord,
    DelegationOutcomeRecord,
    DispatchExpectedOutput,
    DispatchIntent,
    OrchestrationRunState,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
    PlannerQuestion,
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


def test_recovery_directives_prefer_validated_blocker_question():
    state = _state()
    state.blockers = [
        BlockerRecord(
            key="blocker-1",
            description="Need requested limit.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        )
    ]

    directives = recovery_directives(state)

    assert directives == [
        {
            "code": "ask_user_for_validated_blocker",
            "blocker_keys": ["blocker-1"],
            "reason": "Validated user-only blocker is open.",
        }
    ]


def test_rejected_delegate_falls_back_to_ask_user_for_validated_blocker():
    state = _state()
    state.delegation_outcomes[-1].remaining_required_obligations = [
        "quote:requested_limit"
    ]
    state.blockers = [
        BlockerRecord(
            key="blocker-1",
            description="Need requested limit.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        )
    ]

    action = action_for_rejected_delegate(
        state,
        error_code="delegate_blocked_pending_user",
    )

    assert action is not None
    assert action.action == PlannerActionType.ASK_USER
    assert action.questions == [
        PlannerQuestion(
            prompt="Need requested limit.",
            reason="blocker",
            blocker_keys=["blocker-1"],
            required_obligation_keys=["quote:requested_limit"],
            blocker_obligations={"blocker-1": ["quote:requested_limit"]},
        )
    ]


def test_rejected_delegate_maps_obligations_from_structured_blocker_fields():
    state = _state()
    state.delegation_outcomes[-1].remaining_required_obligations = [
        "quote:industry",
        "quote:requested_limit",
    ]
    state.blockers = [
        BlockerRecord(
            key="agent_blocker:agent-1:client.industry",
            description="Need industry and requested limit.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        ),
        BlockerRecord(
            key="agent_blocker:agent-1:requested_coverage.limit",
            description="Need industry and requested limit.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        ),
    ]

    action = action_for_rejected_delegate(
        state,
        error_code="delegate_blocked_pending_user",
    )

    assert action is not None
    assert action.questions[0].blocker_obligations == {
        "agent_blocker:agent-1:client.industry": ["quote:industry"],
        "agent_blocker:agent-1:requested_coverage.limit": ["quote:requested_limit"],
    }


def test_rejected_delegate_preserves_presence_only_obligation():
    state = _state()
    state.blockers = [
        BlockerRecord(
            key="agent_blocker:agent-1:quote",
            description="Need the quote.",
            blocked_output_keys=["quote"],
            source="agent",
            claimed_user_only=True,
            validated_user_only=True,
            validation_status="validated",
        )
    ]

    action = action_for_rejected_delegate(
        state,
        error_code="delegate_blocked_pending_user",
    )

    assert action is not None
    assert action.questions[0].blocker_obligations == {
        "agent_blocker:agent-1:quote": ["quote:$present"]
    }


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


def test_hitl_resolution_allows_same_agent_repair_and_fulfilled_progress():
    state = _state(status="blocked")
    state.delegation_outcomes[-1].remaining_required_obligations = [
        "quote:requested_limit"
    ]
    blocker = BlockerRecord(
        key="blocker-1",
        description="Need requested limit.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        status="open",
    )
    state.blockers = [blocker]
    state.delegation_outcomes[-1].blockers = [blocker.model_copy(deep=True)]
    state.open_questions = [
        {
            "request_id": "hitl-1",
            "status": "resolved",
            "resolved": True,
            "blocker_keys": ["blocker-1"],
            "blocker_obligations": {
                "blocker-1": ["quote:requested_limit"],
            },
        }
    ]

    validate_hitl_answered_blockers(
        state,
        resolved_request_ids={"hitl-1"},
        answer_fact={"fact_id": "hitl-fact-1", "text": "$5M"},
    )
    state = rebuild_goal_progress(state)
    repair = normalize_delegate_repair_lineage(
        PlannerAction(
            action=PlannerActionType.DELEGATE,
            reasoning="Continue with the supplied limit.",
            targets=[_target()],
        ),
        state,
        {},
    )

    assert state.blockers[0].status == "resolved"
    assert state.goal_progress[0].status == "partial"
    assert repair.targets[0].repair_of_intent_id == "intent-1"
    assert (
        PlannerActionValidator.validate(
            repair,
            run_state=state,
            resource_fingerprints={},
            guardrails_enabled=True,
        )
        is repair
    )

    state.delegation_outcomes.append(
        state.delegation_outcomes[-1].model_copy(
            update={
                "outcome_id": "outcome-2",
                "dispatch_intent_id": "intent-2",
                "attempt_fingerprint": "attempt-2",
                "status": "fulfilled",
                "remaining_required_obligations": [],
                "newly_satisfied_required_obligations": ["quote:requested_limit"],
                "blockers": [],
            }
        )
    )

    completed = rebuild_goal_progress(state)

    assert completed.goal_progress[0].status == "fulfilled"
    assert completed.goal_progress[0].remaining_required_obligations == []


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
