from execution.orchestration.outcome_policy import (
    BlockerPolicyValidator,
    OutcomeHistoryView,
    active_completion_scope,
    duplicate_delegate_target_code,
    evaluate_retry,
)
from models.orchestration import (
    BlockerRecord,
    BlockerResolutionAttempt,
    DelegationOutcomeRecord,
    DispatchIntent,
    GoalFamilyDispositionRecord,
    OpenFailureRecord,
    OrchestrationRunState,
    PlannedDelegateTarget,
)


def _outcome(
    outcome_id,
    intent_id,
    status,
    remaining,
    newly,
    *,
    agent_id="agent-1",
    family="family-1",
    revision="revision-1",
):
    return DelegationOutcomeRecord(
        outcome_id=outcome_id,
        dispatch_intent_id=intent_id,
        agent_id=agent_id,
        goal_family_fingerprint=family,
        goal_revision_fingerprint=revision,
        attempt_fingerprint=f"attempt-{agent_id}",
        status=status,
        remaining_required_obligations=list(remaining),
        newly_satisfied_required_obligations=list(newly),
    )


def _intent(intent_id, status, repair_of=None, *, agent_id="agent-1"):
    return DispatchIntent(
        step_id=intent_id,
        step_target_id=f"{intent_id}:target",
        dispatch_intent_id=intent_id,
        planned_agent_message_id=f"{intent_id}:message",
        agent_id=agent_id,
        task="produce quote",
        task_hash="shared-task-hash",
        status=status,
        repair_of_intent_id=repair_of,
    )


def _state(outcomes, intents, failures=None, blockers=None, dispositions=None):
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="produce quote",
        candidate_agent_ids=["agent-1", "agent-2"],
        delegation_outcomes=list(outcomes),
        dispatch_intents=list(intents),
        open_failures=list(failures or []),
        blockers=list(blockers or []),
        goal_family_dispositions=list(dispositions or []),
    )


def _target(*, repair_of=None, agent_id="agent-1"):
    return PlannedDelegateTarget(
        agent_id=agent_id,
        task="produce quote",
        repair_of_intent_id=repair_of,
    )


def test_history_derives_epoch_and_repair_usage_without_persisted_counters():
    state = _state(
        [
            _outcome("o1", "i1", "partial", ["quote:retention"], ["quote:limit"]),
            _outcome("o2", "i2", "no_progress", ["quote:retention"], []),
        ],
        [_intent("i1", "completed"), _intent("i2", "completed", repair_of="i1")],
    )

    chain = OutcomeHistoryView.from_state(state).chain("agent-1", "revision-1")

    assert chain.required_progress_epoch == 1
    assert chain.no_progress_repair_used_in_epoch is True


def test_strict_obligation_reduction_advances_epoch_without_newly_satisfied_keys():
    state = _state(
        [
            _outcome("o1", "i1", "partial", ["quote:limit", "quote:retention"], []),
            _outcome("o2", "i2", "partial", ["quote:retention"], []),
        ],
        [_intent("i1", "completed"), _intent("i2", "completed", repair_of="i1")],
    )

    chain = OutcomeHistoryView.from_state(state).chain("agent-1", "revision-1")
    decision = evaluate_retry(
        state,
        _target(repair_of="i2"),
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
    )

    assert chain.required_progress_epoch == 1
    assert chain.no_progress_repair_used_in_epoch is False
    assert decision.allowed is True
    assert decision.code is None
    assert decision.kind == "semantic_repair"


def test_partial_without_obligation_reduction_consumes_epoch_repair_slot():
    state = _state(
        [
            _outcome("o1", "i1", "partial", ["quote:retention"], ["quote:limit"]),
            _outcome("o2", "i2", "partial", ["quote:retention"], []),
        ],
        [_intent("i1", "completed"), _intent("i2", "completed", repair_of="i1")],
    )

    decision = evaluate_retry(
        state,
        _target(repair_of="i2"),
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
    )

    assert decision.code == "delegate_no_progress_repeat"


def test_failed_retry_uses_open_failure_budget_without_repair_lineage():
    failure = OpenFailureRecord(
        failure_id="failure-1",
        fingerprint="failure-fingerprint",
        source="runtime",
        agent_id="agent-1",
        agent_message_id="i1:message",
        dispatch_intent_id="i1",
        error_code="transport_error",
        error_message="connection reset",
        recoverable=True,
        retry_count=0,
        max_retries=2,
        recovery_hints=["retry_with_refined_task"],
    )
    state = _state(
        [_outcome("o1", "i1", "failed", ["quote:$present"], [])],
        [_intent("i1", "failed")],
        [failure],
    )

    decision = evaluate_retry(
        state,
        _target(),
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
    )

    assert decision.allowed is True
    assert decision.kind == "operational_retry"


def test_failed_retry_is_blocked_when_budget_exhausted():
    failure = OpenFailureRecord(
        failure_id="failure-1",
        fingerprint="failure-fingerprint",
        source="runtime",
        agent_id="agent-1",
        agent_message_id="i1:message",
        dispatch_intent_id="i1",
        error_code="transport_error",
        error_message="connection reset",
        recoverable=True,
        retry_count=2,
        max_retries=2,
    )
    state = _state(
        [_outcome("o1", "i1", "failed", ["quote:$present"], [])],
        [_intent("i1", "failed")],
        [failure],
    )

    decision = evaluate_retry(
        state,
        _target(),
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
    )

    assert decision.code == "recovery_retry_exhausted"


def test_duplicate_target_pair_is_rejected_inside_one_action():
    assert (
        duplicate_delegate_target_code([_target(), _target()], ["family-1", "family-1"])
        == "duplicate_delegate_goal_target"
    )


def test_alternate_agent_has_an_independent_attempt_chain():
    state = _state(
        [_outcome("o1", "i1", "no_progress", ["quote:$present"], [])],
        [_intent("i1", "completed")],
    )

    decision = evaluate_retry(
        state,
        _target(agent_id="agent-2"),
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
    )

    assert decision.kind == "alternate_agent"
    assert decision.code is None


def test_candidate_blocker_is_rejected_as_user_only():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
    )

    assert decision.code == "blocker_candidate_unvalidated"


def test_validated_blocker_requires_required_output_linkage():
    blocker = BlockerRecord(
        key="optional-comment",
        description="Optional comment is unavailable.",
        blocked_output_keys=["optional-comment"],
        source="agent",
        claimed_user_only=True,
        validation_status="validated",
        validated_user_only=True,
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
    )

    assert decision.code == "blocker_not_required_output"


def test_disposed_family_is_excluded_only_when_its_event_is_referenced():
    disposition = GoalFamilyDispositionRecord(
        event_id="dispose-1",
        goal_family_fingerprint="family-1",
        through_goal_revision_fingerprint="revision-1",
        status="abandoned",
        reason="A conditional result waived this output.",
    )
    state = _state(
        [_outcome("o1", "i1", "partial", ["quote:$present"], [])],
        [_intent("i1", "completed")],
        dispositions=[disposition],
    )

    assert active_completion_scope(state, set()) == {("family-1", "revision-1")}
    assert active_completion_scope(state, {"dispose-1"}) == set()


def test_completion_scope_retains_latest_revision_after_disposition():
    disposition = GoalFamilyDispositionRecord(
        event_id="dispose-1",
        goal_family_fingerprint="family-1",
        through_goal_revision_fingerprint="revision-1",
        status="superseded",
        reason="New evidence created a revision.",
    )
    state = _state(
        [
            _outcome("o1", "i1", "partial", ["quote:$present"], [], revision="revision-1"),
            _outcome("o2", "i2", "partial", ["quote:$present"], [], revision="revision-2"),
        ],
        [_intent("i1", "completed"), _intent("i2", "completed")],
        dispositions=[disposition],
    )

    assert active_completion_scope(state, {"dispose-1"}) == {("family-1", "revision-2")}


def test_user_only_blocker_requires_exhausted_resolution_paths():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        resolution_attempts=[
            BlockerResolutionAttempt(
                kind="resource",
                reference_id="resource-1",
                outcome="unavailable",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="agent",
                reference_id="agent-2",
                outcome="failed",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="conditional_result",
                reference_id="quote",
                outcome="insufficient",
                applies_to_output_keys=["quote"],
            ),
        ],
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
        available_resource_refs={"resource-1"},
        eligible_alternate_agent_ids={"agent-2"},
        conditional_result_viable=False,
    )

    assert decision.code == "blocker_user_only_validated"


def test_validated_user_only_blocker_requires_resolver_context():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        resolution_attempts=[
            BlockerResolutionAttempt(
                kind="resource",
                reference_id="resource-1",
                outcome="unavailable",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="agent",
                reference_id="agent-2",
                outcome="failed",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="conditional_result", reference_id="quote", outcome="insufficient"
            ),
        ],
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
    )

    assert decision.code == "blocker_resource_resolution_context_required"


def test_validated_user_only_blocker_requires_alternate_agent_context():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        resolution_attempts=[
            BlockerResolutionAttempt(
                kind="resource",
                reference_id="resource-1",
                outcome="unavailable",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="agent",
                reference_id="agent-2",
                outcome="failed",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="conditional_result", reference_id="quote", outcome="insufficient"
            ),
        ],
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
        available_resource_refs={"resource-1"},
    )

    assert decision.code == "blocker_alternate_agent_context_required"


def test_validated_user_only_blocker_requires_terminal_resolution_attempts():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
        available_resource_refs={"resource-1"},
        eligible_alternate_agent_ids={"agent-2"},
        conditional_result_viable=False,
    )

    assert decision.code == "blocker_resource_resolution_required"


def test_validated_user_only_blocker_requires_conditional_result_attempt():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        resolution_attempts=[
            BlockerResolutionAttempt(
                kind="resource",
                reference_id="resource-1",
                outcome="unavailable",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="agent",
                reference_id="agent-2",
                outcome="failed",
                applies_to_output_keys=["quote"],
            ),
        ],
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
        available_resource_refs={"resource-1"},
        eligible_alternate_agent_ids={"agent-2"},
        conditional_result_viable=False,
    )

    assert decision.code == "blocker_conditional_result_resolution_required"


def test_validated_blocker_rejects_unrelated_conditional_result_evidence():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        resolution_attempts=[
            BlockerResolutionAttempt(
                kind="resource",
                reference_id="resource-1",
                outcome="unavailable",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="agent",
                reference_id="agent-2",
                outcome="failed",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="conditional_result",
                reference_id="optional-comment",
                outcome="insufficient",
            ),
        ],
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
        available_resource_refs={"resource-1"},
        eligible_alternate_agent_ids={"agent-2"},
        conditional_result_viable=False,
    )

    assert decision.code == "blocker_conditional_result_output_required"


def test_validated_blocker_rejects_resource_attempt_for_unrelated_output():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        resolution_attempts=[
            BlockerResolutionAttempt(
                kind="resource",
                reference_id="resource-1",
                outcome="unavailable",
                applies_to_output_keys=["optional-comment"],
            ),
            BlockerResolutionAttempt(
                kind="agent",
                reference_id="agent-2",
                outcome="failed",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="conditional_result",
                reference_id="quote",
                outcome="insufficient",
                applies_to_output_keys=["quote"],
            ),
        ],
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
        available_resource_refs={"resource-1"},
        eligible_alternate_agent_ids={"agent-2"},
        conditional_result_viable=False,
    )

    assert decision.code == "blocker_resource_resolution_required"


def test_validated_blocker_rejects_alternate_agent_attempt_for_unrelated_output():
    blocker = BlockerRecord(
        key="missing-retention",
        description="Retention is not available.",
        blocked_output_keys=["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        resolution_attempts=[
            BlockerResolutionAttempt(
                kind="resource",
                reference_id="resource-1",
                outcome="unavailable",
                applies_to_output_keys=["quote"],
            ),
            BlockerResolutionAttempt(
                kind="agent",
                reference_id="agent-2",
                outcome="failed",
                applies_to_output_keys=["optional-comment"],
            ),
            BlockerResolutionAttempt(
                kind="conditional_result",
                reference_id="quote",
                outcome="insufficient",
                applies_to_output_keys=["quote"],
            ),
        ],
    )

    decision = BlockerPolicyValidator().validate(
        blocker,
        required_output_keys={"quote"},
        available_resource_refs={"resource-1"},
        eligible_alternate_agent_ids={"agent-2"},
        conditional_result_viable=False,
    )

    assert decision.code == "blocker_alternate_agent_available"
