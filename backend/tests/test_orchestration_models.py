import pytest

from execution.orchestration.run_reducer import (
    OrchestrationTransitionError,
    mark_running,
    mark_terminal,
)
from models.orchestration import (
    AuthorizationBasis,
    BlockerResolutionAttempt,
    CandidateAgentSnapshot,
    CandidateScopeSnapshot,
    CompletionEvidence,
    DelegationOutcomeRecord,
    DispatchContentRef,
    DispatchExpectedOutput,
    DispatchRefKind,
    OpenFailureRecord,
    OrchestrationEventType,
    OrchestrationRunState,
    OrchestrationStatus,
    ParticipantSnapshot,
    PendingAgentContinuation,
    PlannedDelegateTarget,
    PlannerAction,
    PlannerActionType,
)


def test_legacy_expected_output_loads_with_outcome_contract_defaults():
    output = DispatchExpectedOutput.model_validate(
        {"kind": "artifact", "required": True, "description": None}
    )

    assert output.output_key is not None
    assert output.output_key.startswith("legacy:")
    assert output.artifact_name is None
    assert output.required_fields == []
    assert output.allow_partial is True


def test_legacy_expected_output_key_is_stable_across_whitespace():
    first = DispatchExpectedOutput(
        kind="artifact",
        description="Produce a quote summary.",
        required_fields=["pricing.premium", "pricing.currency"],
    )
    second = DispatchExpectedOutput(
        kind="  artifact ",
        description=" Produce  a quote summary. ",
        required_fields=["pricing.currency", "pricing.premium"],
    )

    assert first.output_key == second.output_key


@pytest.mark.parametrize(
    ("persisted_value", "validation_status", "expected"),
    [(True, "candidate", False), (False, "validated", True)],
)
def test_blocker_derives_compatibility_validation_field(
    persisted_value: bool,
    validation_status: str,
    expected: bool,
):
    outcome = DelegationOutcomeRecord.model_validate(
        {
            "outcome_id": "outcome-1",
            "dispatch_intent_id": "intent-1",
            "agent_id": "agent-1",
            "goal_family_fingerprint": "family-1",
            "goal_revision_fingerprint": "revision-1",
            "attempt_fingerprint": "attempt-1",
            "status": "blocked",
            "blockers": [
                {
                    "key": "missing-limit",
                    "description": "The requested limit is unavailable.",
                    "source": "agent",
                    "validated_user_only": persisted_value,
                    "validation_status": validation_status,
                }
            ],
        }
    )

    blocker = outcome.blockers[0]
    assert blocker.validated_user_only is expected
    assert blocker.model_dump()["validated_user_only"] is expected


def test_legacy_run_loads_with_outcome_collections_defaulted():
    state = OrchestrationRunState.model_validate(
        {
            "run_id": "run-1",
            "room_id": "room-1",
            "user_message_id": "msg-1",
            "goal": "coordinate",
            "candidate_agent_ids": ["agent-1"],
        }
    )

    assert state.delegation_outcomes == []
    assert state.pending_agent_continuations == []
    assert state.goal_family_dispositions == []
    assert state.assumptions == []
    assert state.unknowns == []
    assert state.blockers == []


def test_legacy_blocker_resolution_attempt_defaults_output_key_linkage():
    attempt = BlockerResolutionAttempt.model_validate(
        {
            "kind": "resource",
            "reference_id": "resource-1",
            "outcome": "unavailable",
        }
    )

    assert attempt.applies_to_output_keys == []


def test_outcome_does_not_persist_derived_policy_counters():
    fields = DelegationOutcomeRecord.model_fields

    assert "same_agent_attempt_number" not in fields
    assert "required_progress_epoch" not in fields
    assert "no_progress_repair_used_in_epoch" not in fields


def test_pending_continuation_defaults_open():
    continuation = PendingAgentContinuation(
        continuation_id="cont-1",
        source_intent_id="intent-1",
        source_agent_message_id="agent-msg-1",
        agent_id="agent-1",
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
        a2a_task_id="task-1",
        a2a_context_id="context-1",
    )

    assert continuation.status == "open"
    assert continuation.attempted_resource_fingerprints == []


def test_outcome_state_round_trips_nested_evidence():
    outcome = DelegationOutcomeRecord(
        outcome_id="outcome-1",
        dispatch_intent_id="intent-1",
        agent_id="agent-1",
        goal_family_fingerprint="family-1",
        goal_revision_fingerprint="revision-1",
        attempt_fingerprint="attempt-1",
        status="blocked",
        assumptions=[
            {
                "key": "currency",
                "description": "Amounts are denominated in GBP.",
                "applies_to_output_keys": ["quote"],
            }
        ],
        blockers=[
            {
                "key": "missing-limit",
                "description": "The requested limit is unavailable.",
                "blocked_output_keys": ["quote"],
                "source": "agent",
            }
        ],
    )
    state = _run_state(delegation_outcomes=[outcome], blockers=outcome.blockers)

    restored = OrchestrationRunState.model_validate_json(state.model_dump_json())

    assert restored.delegation_outcomes[0].status == "blocked"
    assert restored.delegation_outcomes[0].assumptions[0].key == "currency"
    assert restored.blockers[0].key == "missing-limit"


def _run_state(**overrides):
    values = {
        "run_id": "run-1",
        "room_id": "room-1",
        "user_message_id": "msg-1",
        "goal": "Get quotes",
        "candidate_agent_ids": ["broker", "insurer"],
        "client_request_id": "cr-1",
    }
    values.update(overrides)
    return OrchestrationRunState(**values)


def test_run_state_defaults_are_v2_and_non_terminal():
    state = _run_state()

    assert state.schema_version == 2
    assert state.status == OrchestrationStatus.CREATED
    assert state.state_version == 0
    assert state.steps_used == 0
    assert state.candidate_agent_ids == ["broker", "insurer"]


def test_event_types_cover_recovery_and_terminal_projection():
    assert OrchestrationEventType.RUN_RECOVERED.value == "run_recovered"
    assert OrchestrationEventType.PUBLIC_LIFECYCLE_PROJECTED.value == (
        "public_lifecycle_projected"
    )
    assert OrchestrationEventType.OUTCOME_EVALUATED.value == "outcome_evaluated"
    assert (
        OrchestrationEventType.REQUIRED_EVIDENCE_INVALIDATED.value
        == "required_evidence_invalidated"
    )


def test_planner_action_schema_rejects_unknown_actions():
    with pytest.raises(ValueError, match="action"):
        PlannerAction(
            action="done",
            reasoning="legacy terminal",
        )


def test_delegate_target_preserves_explicit_resource_refs():
    target = PlannedDelegateTarget(
        agent_id="agent-1",
        task="Review the selected submission.",
        depends_on=["prior-intent"],
        parallel_group="fanout-1",
        required_resource_refs=["ctx:file-file-1:text"],
        context_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.CONTEXT,
                ref_id="ctx:file-file-1:text",
            )
        ],
        attachment_refs=[
            DispatchContentRef(
                kind=DispatchRefKind.ATTACHMENT,
                ref_id="file:file-1",
                mime_type="application/pdf",
                required=False,
            )
        ],
        expected_outputs=[
            DispatchExpectedOutput(kind="summary", description="Review summary")
        ],
    )

    assert target.depends_on == ["prior-intent"]
    assert target.parallel_group == "fanout-1"
    assert target.required_resource_refs == ["ctx:file-file-1:text"]
    assert target.context_refs[0].ref_id == "ctx:file-file-1:text"
    assert target.attachment_refs[0].required is False
    assert target.expected_outputs[0].kind == "summary"
    assert target.attachment_policy == "explicit_refs_only"


def test_candidate_snapshot_defaults_to_text_input_mode():
    candidate = CandidateAgentSnapshot(agent_id="agent-1")

    assert candidate.input_modes == ["text"]
    assert candidate.output_modes == []
    assert candidate.supports_file_upload is False


def test_mark_running_returns_updated_copy_without_mutating_input():
    state = _run_state(state_version=3)
    original_updated_at = state.updated_at

    updated = mark_running(state)

    assert updated is not state
    assert state.status == OrchestrationStatus.CREATED
    assert state.state_version == 3
    assert state.updated_at == original_updated_at
    assert updated.status == OrchestrationStatus.RUNNING
    assert updated.state_version == 4
    assert updated.updated_at > original_updated_at


def test_mark_running_rejects_already_terminal_state():
    state = _run_state(status=OrchestrationStatus.COMPLETED)

    with pytest.raises(OrchestrationTransitionError):
        mark_running(state)


def test_mark_terminal_sets_terminal_status_and_reason_without_mutating_input():
    state = _run_state(state_version=2)
    original_updated_at = state.updated_at

    updated = mark_terminal(
        state,
        OrchestrationStatus.FAILED,
        reason="planner failed",
    )

    assert updated is not state
    assert state.status == OrchestrationStatus.CREATED
    assert state.terminal_reason is None
    assert state.state_version == 2
    assert state.updated_at == original_updated_at
    assert updated.status == OrchestrationStatus.FAILED
    assert updated.terminal_reason == "planner failed"
    assert updated.state_version == 3
    assert updated.updated_at > original_updated_at


def test_mark_terminal_rejects_non_terminal_target_status():
    state = _run_state()

    with pytest.raises(OrchestrationTransitionError):
        mark_terminal(state, OrchestrationStatus.RUNNING, reason="not terminal")


def test_mark_terminal_rejects_rewriting_already_terminal_state():
    state = _run_state(
        status=OrchestrationStatus.COMPLETED,
        terminal_reason="done",
        state_version=4,
    )

    with pytest.raises(OrchestrationTransitionError):
        mark_terminal(state, OrchestrationStatus.FAILED, reason="rewrite")


def test_mark_terminal_coerces_raw_string_status_to_enum():
    state = _run_state()

    updated = mark_terminal(state, "completed", reason="done")

    assert updated.status == OrchestrationStatus.COMPLETED
    assert isinstance(updated.status, OrchestrationStatus)
    assert updated.terminal_reason == "done"


def _candidate_scope():
    return CandidateScopeSnapshot(
        snapshot_id="scope-1",
        revision=1,
        source="saved_group",
        room_id="room-1",
        group_id="group-1",
        agent_ids=["agent-1", "agent-2"],
        agents=[
            CandidateAgentSnapshot(
                agent_id="agent-1",
                name="Broker",
                role="broker",
                capability_summary="Collects broker requirements.",
                status="active",
                source="saved_group",
            ),
            CandidateAgentSnapshot(
                agent_id="agent-2",
                name="Insurer",
                role="insurer",
                capability_summary="Produces quote options.",
                status="active",
                source="saved_group",
            ),
        ],
        authorization_basis=AuthorizationBasis(
            kind="saved_group_member",
            room_id="room-1",
            group_id="group-1",
            selected_by_user_id="user-1",
        ),
    )


def test_candidate_scope_snapshot_is_first_class_run_state():
    scope = _candidate_scope()
    state = _run_state(candidate_scope=scope)

    assert state.candidate_scope is not None
    assert state.candidate_scope.source == "saved_group"
    assert state.candidate_scope.agent_ids == ["agent-1", "agent-2"]
    assert state.candidate_scope.authorization_basis.kind == "saved_group_member"


def test_participant_snapshot_preserves_debate_ordering_and_round():
    participant = ParticipantSnapshot(
        mode="debate",
        ordered_agent_ids=["agent-1", "agent-2"],
        current_round=1,
        max_rounds=3,
        turn_policy="debate_rounds",
        completed_agent_ids=["agent-1"],
    )
    state = _run_state(participant_snapshot=participant)

    assert state.participant_snapshot.mode == "debate"
    assert state.participant_snapshot.ordered_agent_ids == ["agent-1", "agent-2"]
    assert state.participant_snapshot.current_round == 1


def test_completion_evidence_confidence_must_be_normalized():
    valid = CompletionEvidence(
        satisfied_criteria=["criterion-1"],
        referenced_fact_ids=["fact-1"],
        referenced_artifact_keys=["artifact-1"],
        unresolved_questions=[],
        final_answer_intent="answer_user",
        confidence=0.75,
    )

    assert valid.confidence == 0.75

    with pytest.raises(Exception, match="confidence"):
        CompletionEvidence(
            satisfied_criteria=["criterion-1"],
            referenced_fact_ids=["fact-1"],
            referenced_artifact_keys=[],
            unresolved_questions=[],
            final_answer_intent="answer_user",
            confidence=1.5,
        )

    with pytest.raises(Exception, match="confidence"):
        CompletionEvidence(
            satisfied_criteria=["criterion-1"],
            referenced_fact_ids=["fact-1"],
            referenced_artifact_keys=[],
            unresolved_questions=[],
            final_answer_intent="answer_user",
            confidence=float("nan"),
        )


def test_delegate_target_carries_explicit_refs_and_expected_outputs():
    target = PlannedDelegateTarget(
        agent_id="insurer-1",
        agent_name="Insurer",
        task="Underwrite the broker-extracted submission.",
        context_refs=[
            DispatchContentRef(kind=DispatchRefKind.CONTEXT, ref_id="room-background")
        ],
        artifact_refs=[
            DispatchContentRef(kind=DispatchRefKind.ARTIFACT, ref_id="broker-msg:artifact_id:submission")
        ],
        attachment_refs=[
            DispatchContentRef(kind=DispatchRefKind.ATTACHMENT, ref_id="file-1")
        ],
        expected_outputs=[
            DispatchExpectedOutput(kind="underwriting_decision", required=True),
            DispatchExpectedOutput(kind="pricing_guidance", required=True),
        ],
        attachment_policy="explicit_refs_only",
    )

    action = PlannerAction(
        action=PlannerActionType.DELEGATE,
        reasoning="Broker artifact is ready; insurer needs structured facts only.",
        targets=[target],
    )

    assert action.targets[0].artifact_refs[0].ref_id == "broker-msg:artifact_id:submission"
    assert action.targets[0].attachment_policy == "explicit_refs_only"
    assert action.targets[0].expected_outputs[1].kind == "pricing_guidance"


def test_candidate_agent_snapshot_exposes_a2a_input_modes():
    snapshot = CandidateAgentSnapshot(
        agent_id="agent-1",
        name="Text Agent",
        capability_summary="Summarizes structured text.",
        input_modes=["text"],
        output_modes=["text"],
        supports_file_upload=False,
    )

    assert snapshot.input_modes == ["text"]
    assert snapshot.output_modes == ["text"]
    assert snapshot.supports_file_upload is False


def test_open_failure_record_has_stable_fingerprint_and_retry_budget():
    failure = OpenFailureRecord(
        failure_id="failure-1",
        fingerprint="agent-msg-1:agent_does_not_accept_file_type:report.pdf",
        source="a2a_adapter",
        agent_id="agent-1",
        agent_message_id="agent-msg-1",
        dispatch_intent_id="intent-1",
        error_code="agent_does_not_accept_file_type",
        error_message="Agent does not accept PDF",
        recoverable=True,
        retry_count=1,
        max_retries=2,
        status="open",
        recovery_hints=["retry_without_unsupported_attachments"],
    )

    assert failure.recoverable is True
    assert failure.status == "open"
    assert failure.retry_count == 1
    assert failure.max_retries == 2


def test_open_failure_retry_count_cannot_exceed_budget():
    with pytest.raises(Exception, match="retry_count"):
        OpenFailureRecord(
            failure_id="failure-1",
            fingerprint="fp",
            source="a2a_adapter",
            agent_id="agent-1",
            error_code="timeout",
            error_message="Timed out",
            recoverable=True,
            retry_count=3,
            max_retries=2,
            status="open",
        )


def test_open_failure_retry_count_uses_configured_budget():
    allowed = OpenFailureRecord(
        failure_id="f-2",
        fingerprint="agent-1:temporary_timeout",
        source="a2a_adapter",
        agent_id="agent-1",
        error_code="temporary_timeout",
        error_message="Temporary timeout.",
        recoverable=True,
        retry_count=4,
        max_retries=5,
        status="open",
    )

    assert allowed.retry_count == 4

    with pytest.raises(Exception, match="retry_count"):
        OpenFailureRecord(
            failure_id="f-3",
            fingerprint="agent-1:temporary_timeout",
            source="a2a_adapter",
            agent_id="agent-1",
            error_code="temporary_timeout",
            error_message="Temporary timeout.",
            recoverable=True,
            retry_count=2,
            max_retries=1,
            status="open",
        )
