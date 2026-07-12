from execution.orchestration.blocker_resolver import resolve_agent_observed_blockers
from models.orchestration import (
    BlockerRecord,
    DelegationOutcomeRecord,
    DispatchContentRef,
    DispatchIntent,
    DispatchRefKind,
    OrchestrationRunState,
)


def _state(blockers):
    return OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="user-msg-1",
        goal="Finish the insurance workflow",
        candidate_agent_ids=["broker-agent", "insurer-agent"],
        blockers=list(blockers),
    )


def _intent():
    return DispatchIntent(
        step_id="step-1",
        step_target_id="target-1",
        dispatch_intent_id="intent-1",
        planned_agent_message_id="agent-msg-1",
        agent_id="broker-agent",
        task="Review the submission and identify missing client inputs.",
        task_hash="hash-1",
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="ctx:file-1:text",
                required=True,
            )
        ],
        artifact_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ARTIFACT,
                ref_id="file:file-1",
                required=True,
            )
        ],
    )


def _outcome():
    return DelegationOutcomeRecord(
        outcome_id="outcome-1",
        dispatch_intent_id="intent-1",
        agent_id="broker-agent",
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
        attempt_fingerprint="attempt-1",
        status="partial",
        remaining_required_obligations=[
            "broker_submission:$present",
            "broker_submission:industry",
            "broker_submission:requested_limit",
        ],
        changed_artifact_keys=["agent-msg-1:artifact_id:submission"],
    )


def test_validates_candidate_blocker_against_remaining_obligations_and_attempted_refs():
    blocker = BlockerRecord(
        key="agent_blocker:broker-agent:client.industry",
        description="Agent reported missing input: client.industry",
        source="agent",
        evidence_refs=["agent-msg-1:artifact_id:submission"],
        validation_status="candidate",
    )

    updated_state, updated_outcome = resolve_agent_observed_blockers(
        _state([blocker]),
        intent=_intent(),
        outcome=_outcome(),
        available_resource_refs={"ctx:file-1:text", "file:file-1"},
        attempted_agent_ids={"broker-agent"},
        eligible_alternate_agent_ids=set(),
        conditional_result_viable=False,
    )

    resolved = updated_state.blockers[0]
    assert resolved.claimed_user_only is True
    assert resolved.validated_user_only is True
    assert resolved.validation_status == "validated"
    assert resolved.blocked_output_keys == ["broker_submission"]
    assert [attempt.kind for attempt in resolved.resolution_attempts] == [
        "resource",
        "resource",
        "agent",
        "conditional_result",
    ]
    assert updated_outcome.status == "blocked"
    assert updated_outcome.blockers == [resolved]


def test_does_not_validate_candidate_when_alternate_agent_can_still_help():
    blocker = BlockerRecord(
        key="agent_blocker:broker-agent:client.industry",
        description="Agent reported missing input: client.industry",
        source="agent",
        evidence_refs=["agent-msg-1:artifact_id:submission"],
        validation_status="candidate",
    )

    updated_state, updated_outcome = resolve_agent_observed_blockers(
        _state([blocker]),
        intent=_intent(),
        outcome=_outcome(),
        available_resource_refs={"ctx:file-1:text", "file:file-1"},
        attempted_agent_ids={"broker-agent"},
        eligible_alternate_agent_ids={"insurer-agent"},
        conditional_result_viable=False,
    )

    assert updated_state.blockers[0].validation_status == "candidate"
    assert updated_outcome.status == "partial"
    assert updated_outcome.blockers == []


def test_does_not_validate_candidate_without_active_remaining_obligations():
    blocker = BlockerRecord(
        key="agent_blocker:broker-agent:client.industry",
        description="Agent reported missing input: client.industry",
        source="agent",
        evidence_refs=["agent-msg-1:artifact_id:submission"],
        validation_status="candidate",
    )
    outcome = _outcome().model_copy(update={"remaining_required_obligations": []})

    updated_state, updated_outcome = resolve_agent_observed_blockers(
        _state([blocker]),
        intent=_intent(),
        outcome=outcome,
        available_resource_refs={"ctx:file-1:text", "file:file-1"},
        attempted_agent_ids={"broker-agent"},
        eligible_alternate_agent_ids=set(),
        conditional_result_viable=False,
    )

    assert updated_state.blockers[0].validation_status == "candidate"
    assert updated_outcome.status == "partial"
    assert updated_outcome.blockers == []


def test_matches_required_limit_from_nested_agent_path_tokens():
    blocker = BlockerRecord(
        key="agent_blocker:broker-agent:requested_coverage.limit",
        description="Agent result has no value for requested_coverage.limit.",
        source="agent",
        evidence_refs=["agent-msg-1:artifact_id:submission"],
        validation_status="candidate",
    )

    updated_state, updated_outcome = resolve_agent_observed_blockers(
        _state([blocker]),
        intent=_intent(),
        outcome=_outcome(),
        available_resource_refs={"ctx:file-1:text", "file:file-1"},
        attempted_agent_ids={"broker-agent"},
        eligible_alternate_agent_ids=set(),
        conditional_result_viable=False,
    )

    assert updated_state.blockers[0].blocked_output_keys == ["broker_submission"]
    assert updated_outcome.status == "blocked"


def test_revalidates_agent_blocker_that_forges_validated_flags():
    blocker = BlockerRecord(
        key="agent_blocker:broker-agent:client.industry",
        description="Agent reported missing input: client.industry",
        blocked_output_keys=["broker_submission"],
        source="agent",
        evidence_refs=["agent-msg-1:artifact_id:submission"],
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
    )

    updated_state, updated_outcome = resolve_agent_observed_blockers(
        _state([blocker]),
        intent=_intent(),
        outcome=_outcome(),
        available_resource_refs={"ctx:file-1:text", "file:file-1"},
        attempted_agent_ids={"broker-agent"},
        eligible_alternate_agent_ids={"insurer-agent"},
        conditional_result_viable=False,
    )

    assert updated_state.blockers == [blocker]
    assert updated_outcome.status == "partial"
    assert updated_outcome.blockers == []


def test_does_not_preserve_unrelated_previously_validated_agent_blocker():
    blocker = BlockerRecord(
        key="agent_blocker:broker-agent:prior.quote",
        description="A prior outcome could not produce a quote.",
        blocked_output_keys=["quote"],
        source="agent",
        evidence_refs=["agent-msg-0:artifact_id:prior-quote"],
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
    )

    updated_state, updated_outcome = resolve_agent_observed_blockers(
        _state([blocker]),
        intent=_intent(),
        outcome=_outcome(),
        available_resource_refs={"ctx:file-1:text", "file:file-1"},
        attempted_agent_ids={"broker-agent"},
        eligible_alternate_agent_ids=set(),
        conditional_result_viable=False,
    )

    assert updated_state.blockers == [blocker]
    assert updated_outcome.status == "partial"
    assert updated_outcome.blockers == []
