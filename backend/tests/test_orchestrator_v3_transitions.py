from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from execution.orchestrator import (
    AGENT_CALL_STATES,
    AGENT_CALL_TRANSITIONS,
    AcceptedAgentCall,
    ArtifactDeliveryCheck,
    ArtifactRefPart,
    AssistantMessage,
    BudgetState,
    CandidateScopeSnapshot,
    IllegalAgentCallTransition,
    OrchestratorProfile,
    OrchestratorRunState,
    ProjectionIntent,
    PromptSnapshot,
    RecoveryClaim,
    ResolvedModelSnapshot,
    RunRequestSnapshot,
    TerminalCommitRequest,
    TerminalDecisionFacts,
    TextPart,
    commit_terminal_decision,
    evaluate_projection_settlement,
    evaluate_terminal_decision,
    is_legal_agent_call_transition,
    transition_after_terminal_evaluation,
    transition_projection_intent,
    transition_projection_settlement,
    validate_agent_call_transition,
)

NOW = datetime(2026, 3, 12, tzinfo=UTC)


def profile() -> OrchestratorProfile:
    return OrchestratorProfile(
        profile_id="ultimate",
        model=ResolvedModelSnapshot(
            route="test",
            provider="test",
            model_id="model",
            api="responses",
            supports_native_tools=True,
            supports_strict_tools=True,
            tool_strategy="native",
            context_window=32_000,
            max_output_tokens=2_000,
            temperature=0.1,
            provider_timeout_seconds=30,
            max_provider_retries=2,
        ),
        prompt=PromptSnapshot(
            prompt_id="system",
            version="1",
            content_digest="digest",
            rendered_system_prompt="prompt",
        ),
        max_model_turns=8,
        grace_model_turns=1,
        max_agent_calls=4,
        max_parallel_calls=2,
        max_transport_retries_per_call=2,
        max_compactions=2,
        deadline_seconds=120,
        initial_routing="model_select",
        tool_execution="parallel",
        finalization="synthesize",
    )


def agent_call(state: str, call_id: str = "call-1") -> AcceptedAgentCall:
    return AcceptedAgentCall(
        state_version=1,
        call_id=call_id,
        run_id="run-1",
        agent_id="agent-1",
        tool_name="call_agent",
        arguments={"task": "work"},
        state=state,
        idempotency_key=f"key-{call_id}",
        accepted_at=NOW,
        updated_at=NOW,
    )


def final_message(*, artifact_ref: str | None = None) -> AssistantMessage:
    content = [TextPart(text="done")]
    if artifact_ref is not None:
        content.append(ArtifactRefPart(artifact_ref=artifact_ref))
    return AssistantMessage(
        message_id="final-1",
        content=content,
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        created_at=NOW,
    )


def run(
    *,
    calls: list[AcceptedAgentCall] | None = None,
    pending_interactions: list[str] | None = None,
    artifact_ref: str | None = None,
    status: str = "finalizing",
    intents: list[ProjectionIntent] | None = None,
) -> OrchestratorRunState:
    return OrchestratorRunState(
        run_id="run-1",
        session_id="room-1",
        room_id="room-1",
        client_request_id="request-1",
        request=RunRequestSnapshot(
            request_fingerprint="fingerprint",
            room_generation=1,
            user_message_id="user-1",
        ),
        profile=profile(),
        candidate_scope=CandidateScopeSnapshot(
            snapshot_id="scope-1",
            source="room_default",
            room_id="room-1",
            agent_ids=["agent-1"],
            resolved_at=NOW,
        ),
        status=status,
        transcript=[final_message(artifact_ref=artifact_ref)],
        calls=calls or [],
        pending_interaction_ids=pending_interactions or [],
        artifact_refs=[artifact_ref] if artifact_ref else [],
        budget=BudgetState(deadline_at=NOW + timedelta(minutes=2)),
        proposed_final_message_id="final-1",
        terminal_reason=None,
        projection_state="pending",
        recovery_claim=RecoveryClaim(),
        projection_outbox=intents or [],
        processed_command_ids=[],
        state_version=3,
        created_at=NOW,
        updated_at=NOW,
    )


def facts(**updates) -> TerminalDecisionFacts:
    values = {"final_message_id": "final-1"}
    values.update(updates)
    return TerminalDecisionFacts(**values)


def commit_request(**updates) -> TerminalCommitRequest:
    values = {
        "expected_state_version": 3,
        "command_id": "command-complete",
        "event_id": "event-complete",
        "event_sequence": 1,
        "event_intent_id": "intent-event",
        "final_message_intent_id": "intent-message",
        "public_run_intent_id": "intent-run",
        "final_message_target": "room-1",
        "public_run_target": "run-1",
        "created_at": NOW + timedelta(seconds=1),
    }
    values.update(updates)
    return TerminalCommitRequest(**values)


def intent(
    intent_id: str,
    status: str,
    *,
    required: bool = True,
) -> ProjectionIntent:
    return ProjectionIntent(
        intent_id=intent_id,
        kind="projection",
        target="target",
        dedupe_key=intent_id,
        required=required,
        event_id="event-1",
        event_sequence=1,
        causation_id="command-1",
        payload={},
        status=status,
    )


def test_every_legal_and_illegal_agent_call_transition():
    for from_state in AGENT_CALL_STATES:
        for to_state in AGENT_CALL_STATES:
            expected = to_state in AGENT_CALL_TRANSITIONS[from_state]
            assert is_legal_agent_call_transition(from_state, to_state) is expected
            if expected:
                validate_agent_call_transition(from_state, to_state)
            else:
                with pytest.raises(IllegalAgentCallTransition):
                    validate_agent_call_transition(from_state, to_state)


def test_terminal_calls_have_no_outbound_transitions():
    for state in {"completed", "failed", "canceled", "rejected", "expired"}:
        assert AGENT_CALL_TRANSITIONS[state] == frozenset()


def test_processed_observation_inventory_rejects_duplicate_callbacks():
    payload = agent_call("working").model_dump()
    payload["processed_observation_ids"] = ["observation-1", "observation-1"]
    with pytest.raises(ValueError, match="observation IDs"):
        AcceptedAgentCall.model_validate(payload)


def test_final_answer_without_calls_is_ready_for_durable_decision():
    assert evaluate_terminal_decision(run(), facts()).decision == "ready"


@pytest.mark.parametrize(
    "state", ["accepted", "dispatching", "working", "continuation_pending", "resuming"]
)
def test_active_or_continuation_pending_call_waits(state):
    evaluation = evaluate_terminal_decision(run(calls=[agent_call(state)]), facts())
    assert evaluation.decision == "waiting_external"


@pytest.mark.parametrize("state", ["input_required", "auth_required"])
def test_user_facing_input_or_auth_awaits_user(state):
    evaluation = evaluate_terminal_decision(run(calls=[agent_call(state)]), facts())
    assert evaluation.decision == "awaiting_user"


@pytest.mark.parametrize(
    ("state", "expected_status"),
    [("working", "waiting_external"), ("input_required", "awaiting_user")],
)
def test_nonterminal_evaluation_persists_wait_without_publishing_draft(
    state, expected_status
):
    original = run(calls=[agent_call(state)])
    evaluation = evaluate_terminal_decision(original, facts())
    transitioned = transition_after_terminal_evaluation(
        original,
        evaluation=evaluation,
        expected_state_version=3,
        updated_at=NOW + timedelta(seconds=1),
    )

    assert transitioned.outcome == "accepted"
    assert transitioned.run.status == expected_status
    assert transitioned.run.proposed_final_message_id == "final-1"
    assert transitioned.run.projection_outbox == []


@pytest.mark.parametrize(
    "state", ["completed", "failed", "rejected", "expired", "canceled"]
)
def test_terminal_child_outcomes_may_terminate(state):
    evaluation = evaluate_terminal_decision(run(calls=[agent_call(state)]), facts())
    assert evaluation.decision == "ready"


def test_pending_hitl_inventory_awaits_user():
    evaluation = evaluate_terminal_decision(
        run(pending_interactions=["interaction-1"]), facts()
    )
    assert evaluation.decision == "awaiting_user"


@pytest.mark.parametrize(
    "check",
    [
        ArtifactDeliveryCheck(
            artifact_ref="artifact-1",
            exists=False,
            belongs_to_run=True,
            belongs_to_room=True,
            deliverable=True,
        ),
        ArtifactDeliveryCheck(
            artifact_ref="artifact-1",
            exists=True,
            belongs_to_run=False,
            belongs_to_room=True,
            deliverable=True,
        ),
        ArtifactDeliveryCheck(
            artifact_ref="artifact-1",
            exists=True,
            belongs_to_run=True,
            belongs_to_room=False,
            deliverable=True,
        ),
        ArtifactDeliveryCheck(
            artifact_ref="artifact-1",
            exists=True,
            belongs_to_run=True,
            belongs_to_room=True,
            deliverable=False,
        ),
    ],
)
def test_missing_foreign_or_undeliverable_artifact_is_rejected(check):
    evaluation = evaluate_terminal_decision(
        run(artifact_ref="artifact-1"), facts(artifact_checks=[check])
    )
    assert evaluation.decision == "operational_rejection"


def test_owned_deliverable_artifact_passes():
    check = ArtifactDeliveryCheck(
        artifact_ref="artifact-1",
        exists=True,
        belongs_to_run=True,
        belongs_to_room=True,
        deliverable=True,
    )
    assert (
        evaluate_terminal_decision(
            run(artifact_ref="artifact-1"), facts(artifact_checks=[check])
        ).decision
        == "ready"
    )


def test_cancellation_winner_prevents_completion():
    evaluation = evaluate_terminal_decision(run(), facts(cancellation_won=True))
    assert evaluation.decision == "terminal_conflict"


def test_terminal_cas_persists_event_outbox_and_wins_exactly_once():
    original = run()
    committed = commit_terminal_decision(
        original, facts=facts(), request=commit_request()
    )

    assert committed.outcome == "accepted"
    assert committed.run.status == "completed"
    assert committed.run.state_version == 4
    assert committed.run.projection_state == "pending"
    assert committed.event is not None
    assert committed.event.event_type == "run_completed"
    assert {item.kind for item in committed.run.projection_outbox} == {
        "append_orchestrator_event",
        "deliver_final_message",
        "project_terminal_run_status",
    }
    assert all(item.required for item in committed.run.projection_outbox)

    replay = commit_terminal_decision(
        committed.run, facts=facts(), request=commit_request(expected_state_version=4)
    )
    loser = commit_terminal_decision(
        committed.run,
        facts=facts(),
        request=commit_request(command_id="other-command", expected_state_version=4),
    )
    assert replay.outcome == "replayed"
    assert loser.outcome == "conflict"


def test_terminal_run_cannot_be_reopened_by_late_waiting_observation():
    terminal = run(status="completed")
    evaluation = evaluate_terminal_decision(run(calls=[agent_call("working")]), facts())
    result = transition_after_terminal_evaluation(
        terminal,
        evaluation=evaluation,
        expected_state_version=3,
        updated_at=NOW + timedelta(seconds=1),
    )
    assert result.outcome == "conflict"
    assert result.run.status == "completed"


def test_terminal_cas_rejects_stale_state_version():
    result = commit_terminal_decision(
        run(), facts=facts(), request=commit_request(expected_state_version=2)
    )
    assert result.outcome == "conflict"
    assert result.run.status == "finalizing"


@pytest.mark.parametrize("sequence", [1, 3])
def test_terminal_cas_rejects_duplicate_or_non_next_event_sequence(sequence):
    existing = intent("history", "completed").model_copy(
        update={
            "kind": "append_orchestrator_event",
            "event_id": "event-history",
            "event_sequence": 1,
        }
    )
    original = run(intents=[existing])

    result = commit_terminal_decision(
        original,
        facts=facts(),
        request=commit_request(event_sequence=sequence),
    )

    assert result.outcome == "conflict"
    assert result.evaluation.reason == "expected terminal event sequence 2"
    assert result.run == original


def test_terminal_cas_accepts_only_the_next_event_sequence():
    existing = intent("history", "completed").model_copy(
        update={
            "kind": "append_orchestrator_event",
            "event_id": "event-history",
            "event_sequence": 1,
        }
    )
    result = commit_terminal_decision(
        run(intents=[existing]),
        facts=facts(),
        request=commit_request(event_sequence=2),
    )
    assert result.outcome == "accepted"
    assert result.event is not None
    assert result.event.sequence == 2


@pytest.mark.parametrize("status", ["pending", "claimed"])
def test_required_pending_or_claimed_outbox_keeps_projection_pending(status):
    assert evaluate_projection_settlement([intent("required", status)]) == "pending"


def _committed_terminal_with_status(status: str) -> OrchestratorRunState:
    committed = commit_terminal_decision(
        run(), facts=facts(), request=commit_request()
    ).run
    return committed.model_copy(
        update={
            "projection_outbox": [
                item.model_copy(update={"status": status})
                for item in committed.projection_outbox
            ]
        }
    )


def test_required_blocked_outbox_blocks_without_rewriting_terminal_run():
    terminal = _committed_terminal_with_status("blocked")
    result = transition_projection_settlement(
        terminal, expected_state_version=4, updated_at=NOW + timedelta(seconds=1)
    )

    assert result.outcome == "accepted"
    assert result.run.status == "completed"
    assert result.run.projection_state == "blocked"


def test_optional_blocked_outbox_does_not_prevent_settlement():
    intents = [
        intent("required", "completed"),
        intent("optional", "blocked", required=False),
    ]
    assert evaluate_projection_settlement(intents) == "settled"


def test_empty_projection_inventory_never_settles():
    terminal = run(status="completed", intents=[]).model_copy(
        update={"projection_state": "settled"}
    )
    result = transition_projection_settlement(
        terminal, expected_state_version=3, updated_at=NOW + timedelta(seconds=1)
    )
    assert evaluate_projection_settlement([]) == "pending"
    assert result.outcome == "accepted"
    assert result.run.projection_state == "pending"


@pytest.mark.parametrize(
    "missing_kind",
    [
        "append_orchestrator_event",
        "deliver_final_message",
        "project_terminal_run_status",
    ],
)
def test_terminal_projection_cannot_settle_without_mandatory_intent(missing_kind):
    terminal = _committed_terminal_with_status("completed")
    terminal = terminal.model_copy(
        update={
            "projection_outbox": [
                item for item in terminal.projection_outbox if item.kind != missing_kind
            ]
        }
    )
    result = transition_projection_settlement(
        terminal, expected_state_version=4, updated_at=NOW + timedelta(seconds=1)
    )
    assert result.outcome == "replayed"
    assert result.run.projection_state == "pending"


def test_all_required_completed_settles_exactly_once():
    terminal = _committed_terminal_with_status("completed")
    first = transition_projection_settlement(
        terminal, expected_state_version=4, updated_at=NOW + timedelta(seconds=1)
    )
    second = transition_projection_settlement(
        first.run,
        expected_state_version=first.run.state_version,
        updated_at=NOW + timedelta(seconds=2),
    )

    assert first.outcome == "accepted"
    assert first.run.projection_state == "settled"
    assert second.outcome == "replayed"
    assert second.run.state_version == first.run.state_version


def test_projection_intent_claim_complete_and_terminal_behavior():
    pending = intent("required", "pending")
    claimed = transition_projection_intent(
        pending,
        to_status="claimed",
        claim_owner="worker-1",
        claim_expires_at=NOW + timedelta(seconds=30),
    )
    completed = transition_projection_intent(claimed, to_status="completed")

    assert claimed.attempt_count == 1
    assert completed.claim_owner is None
    with pytest.raises(ValueError):
        transition_projection_intent(completed, to_status="claimed")
