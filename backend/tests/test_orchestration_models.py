import pytest

from execution.orchestration.run_reducer import (
    OrchestrationTransitionError,
    mark_running,
    mark_terminal,
)
from models.orchestration import (
    AuthorizationBasis,
    CandidateAgentSnapshot,
    CandidateScopeSnapshot,
    CompletionEvidence,
    OrchestrationEventType,
    OrchestrationRunState,
    OrchestrationStatus,
    ParticipantSnapshot,
    PlannerAction,
)


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


def test_planner_action_schema_rejects_unknown_actions():
    with pytest.raises(ValueError, match="action"):
        PlannerAction(
            action="done",
            reasoning="legacy terminal",
        )


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
