import pytest

from execution.orchestration.action_validator import (
    PlannerActionValidationError,
    PlannerActionValidator,
)
from execution.orchestration.blocker_resolver import (
    resolve_agent_observed_blockers,
    validate_hitl_answered_blockers,
)
from models.orchestration import (
    BlockerRecord,
    DelegationOutcomeRecord,
    DispatchContentRef,
    DispatchExpectedOutput,
    DispatchIntent,
    DispatchRefKind,
    OrchestrationRunState,
    PlannerAction,
    PlannerActionType,
    PlannerQuestion,
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

    resolved = updated_state.blockers[0]
    assert resolved.claimed_user_only is True
    assert resolved.validated_user_only is False
    assert resolved.validation_status == "candidate"
    assert updated_outcome.status == "partial"
    assert updated_outcome.blockers == []


def test_sanitized_agent_blocker_cannot_authorize_ask_user():
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
    intent = _intent().model_copy(
        update={
            "status": "completed",
            "expected_outputs": [
                DispatchExpectedOutput(
                    output_key="broker_submission",
                    kind="artifact",
                    required=True,
                )
            ],
        }
    )

    updated_state, _ = resolve_agent_observed_blockers(
        _state([blocker]),
        intent=intent,
        outcome=_outcome(),
        available_resource_refs={"ctx:file-1:text", "file:file-1"},
        attempted_agent_ids={"broker-agent"},
        eligible_alternate_agent_ids={"insurer-agent"},
        conditional_result_viable=False,
    )
    validation_state = updated_state.model_copy(
        update={"dispatch_intents": [intent]}, deep=True
    )
    action = PlannerAction(
        action=PlannerActionType.ASK_USER,
        reasoning="Request the missing industry.",
        questions=[
            PlannerQuestion(
                prompt="What industry is the client in?",
                reason="blocker",
                blocker_keys=[blocker.key],
            )
        ],
    )

    with pytest.raises(PlannerActionValidationError) as exc_info:
        PlannerActionValidator.validate(
            action,
            run_state=validation_state,
            guardrails_enabled=True,
            resource_fingerprints={},
        )

    assert exc_info.value.code == "ask_user_blocker_not_validated"


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


def _validated_blocker(key="blocker-1", blocked_output_keys=None):
    return BlockerRecord(
        key=key,
        description="Need requested limit.",
        blocked_output_keys=blocked_output_keys or ["quote"],
        source="agent",
        claimed_user_only=True,
        validated_user_only=True,
        validation_status="validated",
        status="open",
    )


def test_resolves_hitl_blocker_when_answer_satisfies_referenced_obligation():
    blocker = _validated_blocker()
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Produce quote",
        candidate_agent_ids=["broker-agent"],
        blockers=[blocker],
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "supervisor",
                "status": "resolved",
                "resolved": True,
                "blocker_keys": ["blocker-1"],
                "required_obligation_keys": ["quote:requested_limit"],
                "answer": "Requested limit is 5M.",
            }
        ],
    )

    validate_hitl_answered_blockers(
        state,
        resolved_request_ids={"hitl-1"},
        answer_fact={
            "fact_id": "fact-1",
            "source": "hitl_user_reply",
            "text": "Requested limit is 5M.",
        },
    )

    assert state.blockers[0].status == "resolved"
    assert "fact-1" in state.blockers[0].evidence_refs


def test_keeps_hitl_blocker_open_when_answer_is_insufficient():
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
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Produce quote",
        candidate_agent_ids=["broker-agent"],
        blockers=[blocker],
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "supervisor",
                "status": "resolved",
                "resolved": True,
                "blocker_keys": ["blocker-1"],
                "required_obligation_keys": ["quote:requested_limit"],
                "answer": "I do not know.",
            }
        ],
    )

    validate_hitl_answered_blockers(
        state,
        resolved_request_ids={"hitl-1"},
        answer_fact={
            "fact_id": "fact-1",
            "source": "hitl_user_reply",
            "text": "I do not know.",
        },
    )

    assert state.blockers[0].status == "open"


@pytest.mark.parametrize(
    "answer",
    [
        "I don't know the requested limit.",
        "The requested limit is unknown.",
    ],
)
def test_keeps_hitl_blocker_open_when_answer_names_field_without_value(answer):
    blocker = _validated_blocker()
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Produce quote",
        candidate_agent_ids=["broker-agent"],
        blockers=[blocker],
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "supervisor",
                "status": "resolved",
                "resolved": True,
                "blocker_keys": ["blocker-1"],
                "required_obligation_keys": ["quote:requested_limit"],
                "answer": answer,
            }
        ],
    )

    validate_hitl_answered_blockers(
        state,
        resolved_request_ids={"hitl-1"},
        answer_fact={"fact_id": "fact-1", "text": answer},
    )

    assert state.blockers[0].status == "open"


def test_keeps_hitl_blocker_open_without_obligation_metadata():
    blocker = _validated_blocker()
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Produce quote",
        candidate_agent_ids=["broker-agent"],
        blockers=[blocker],
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "supervisor",
                "status": "resolved",
                "resolved": True,
                "blocker_keys": ["blocker-1"],
                "answer": "Requested limit is 5M.",
            }
        ],
    )

    validate_hitl_answered_blockers(
        state,
        resolved_request_ids={"hitl-1"},
        answer_fact={"fact_id": "fact-1", "text": "Requested limit is 5M."},
    )

    assert state.blockers[0].status == "open"


def test_keeps_hitl_blocker_open_when_mapping_omits_blocker_obligations():
    blocker = _validated_blocker()
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Produce quote",
        candidate_agent_ids=["broker-agent"],
        blockers=[blocker],
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "supervisor",
                "status": "resolved",
                "resolved": True,
                "blocker_keys": ["blocker-1"],
                "required_obligation_keys": ["quote:requested_limit"],
                "blocker_obligations": {"other-blocker": ["quote:industry"]},
                "answer": "Requested limit is 5M.",
            }
        ],
    )

    validate_hitl_answered_blockers(
        state,
        resolved_request_ids={"hitl-1"},
        answer_fact={"fact_id": "fact-1", "text": "Requested limit is 5M."},
    )

    assert state.blockers[0].status == "open"


def test_resolves_only_addressed_blocker_for_multi_blocker_answer():
    first = _validated_blocker("limit-blocker")
    second = _validated_blocker("industry-blocker")
    state = OrchestrationRunState(
        run_id="run-1",
        room_id="room-1",
        user_message_id="msg-1",
        goal="Produce quote",
        candidate_agent_ids=["broker-agent"],
        blockers=[first, second],
        open_questions=[
            {
                "request_id": "hitl-1",
                "source": "supervisor",
                "status": "resolved",
                "resolved": True,
                "blocker_keys": ["limit-blocker", "industry-blocker"],
                "blocker_obligations": {
                    "limit-blocker": ["quote:requested_limit"],
                    "industry-blocker": ["quote:industry"],
                },
                "answer": "Requested limit is 5M.",
            }
        ],
    )

    validate_hitl_answered_blockers(
        state,
        resolved_request_ids={"hitl-1"},
        answer_fact={
            "fact_id": "fact-1",
            "source": "hitl_user_reply",
            "text": "Requested limit is 5M.",
        },
    )

    assert state.blockers[0].status == "resolved"
    assert state.blockers[1].status == "open"
