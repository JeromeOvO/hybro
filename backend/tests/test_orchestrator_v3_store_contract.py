from __future__ import annotations

from datetime import UTC, datetime, timedelta

from execution.orchestrator import (
    AcceptedAgentCall,
    AssistantMessage,
    BudgetState,
    CandidateScopeSnapshot,
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
)
from execution.orchestrator.contract_harness import (
    InMemoryOrchestratorContractHarness,
)

NOW = datetime(2026, 3, 12, tzinfo=UTC)


def _profile() -> OrchestratorProfile:
    return OrchestratorProfile(
        profile_id="fast",
        model=ResolvedModelSnapshot(
            route="offline",
            provider="openai",
            model_id="offline",
            api="chat_completions",
            supports_native_tools=True,
            supports_provider_strict_schema=True,
            supports_local_structured_action=False,
            structured_action_validation="unsupported",
            tool_strategy="native",
            context_window=8_000,
            max_output_tokens=1_000,
            temperature=0,
            provider_timeout_seconds=10,
            max_provider_retries=1,
        ),
        prompt=PromptSnapshot(
            prompt_id="test",
            version="1",
            content_digest="digest",
            rendered_system_prompt="test",
        ),
        max_model_turns=2,
        grace_model_turns=1,
        max_agent_calls=2,
        max_parallel_calls=1,
        max_transport_retries_per_call=1,
        max_compactions=1,
        deadline_seconds=60,
        initial_routing="explicit_agent_first",
        tool_execution="sequential",
        finalization="light",
    )


def _run(run_id: str = "run-1", *, room_id: str = "room-1") -> OrchestratorRunState:
    final = AssistantMessage(
        message_id=f"final-{run_id}",
        content=[TextPart(text="done")],
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        created_at=NOW,
    )
    return OrchestratorRunState(
        run_id=run_id,
        session_id=room_id,
        room_id=room_id,
        client_request_id=f"request-{run_id}",
        request=RunRequestSnapshot(
            request_fingerprint=f"fingerprint-{run_id}",
            room_generation=1,
            user_message_id=f"user-{run_id}",
        ),
        profile=_profile(),
        candidate_scope=CandidateScopeSnapshot(
            snapshot_id=f"scope-{run_id}",
            source="room_default",
            room_id=room_id,
            agent_ids=["agent-1"],
            resolved_at=NOW,
        ),
        status="running",
        transcript=[final],
        calls=[],
        pending_interaction_ids=[],
        artifact_refs=[],
        budget=BudgetState(deadline_at=NOW + timedelta(minutes=1)),
        proposed_final_message_id=final.message_id,
        terminal_reason=None,
        projection_state="pending",
        recovery_claim=RecoveryClaim(),
        projection_outbox=[],
        processed_command_ids=[],
        state_version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _call(state: str = "accepted") -> AcceptedAgentCall:
    return AcceptedAgentCall(
        state_version=1,
        call_id="call-1",
        run_id="run-1",
        agent_id="agent-1",
        tool_name="call_agent",
        arguments={"task": "work"},
        state=state,
        idempotency_key="call-key-1",
        accepted_at=NOW,
        updated_at=NOW,
    )


def _dispatch_intent() -> ProjectionIntent:
    return ProjectionIntent(
        intent_id="dispatch-call-1",
        kind="dispatch_agent_call",
        target="agent-1",
        dedupe_key="dispatch-call-1",
        required=True,
        event_id="event-call-1",
        event_sequence=1,
        causation_id="accept-call-1",
        payload={"call_id": "call-1"},
        status="pending",
    )


def test_recovery_claims_only_due_or_stale_runs_at_current_version():
    store = InMemoryOrchestratorContractHarness()
    due = _run("run-due", room_id="room-due").model_copy(
        update={"recovery_claim": RecoveryClaim(next_attempt_at=NOW)}
    )
    future = _run("run-future", room_id="room-future").model_copy(
        update={
            "recovery_claim": RecoveryClaim(next_attempt_at=NOW + timedelta(minutes=5))
        }
    )
    live = _run("run-live", room_id="room-live").model_copy(
        update={
            "recovery_claim": RecoveryClaim(
                owner_id="live-worker",
                lease_expires_at=NOW + timedelta(minutes=5),
            )
        }
    )
    stale = _run("run-stale", room_id="room-stale").model_copy(
        update={
            "recovery_claim": RecoveryClaim(
                owner_id="dead-worker",
                lease_expires_at=NOW - timedelta(seconds=1),
            )
        }
    )
    terminal = _run("run-terminal", room_id="room-terminal").model_copy(
        update={"status": "completed"}
    )
    for run in (due, future, live, stale, terminal):
        assert store.create(run) == "accepted"

    assert [run.run_id for run in store.list_due_runs(due_at=NOW, limit=10)] == [
        "run-due",
        "run-stale",
    ]
    assert (
        store.claim_recovery(
            "run-due",
            expected_state_version=0,
            owner_id="worker-1",
            lease_expires_at=NOW + timedelta(minutes=1),
            claimed_at=NOW,
        )
        == "conflict"
    )
    assert (
        store.claim_recovery(
            "run-due",
            expected_state_version=1,
            owner_id="worker-1",
            lease_expires_at=NOW,
            claimed_at=NOW,
        )
        == "conflict"
    )
    assert (
        store.claim_recovery(
            "run-future",
            expected_state_version=1,
            owner_id="worker-1",
            lease_expires_at=NOW + timedelta(minutes=1),
            claimed_at=NOW,
        )
        == "conflict"
    )
    assert (
        store.claim_recovery(
            "run-due",
            expected_state_version=1,
            owner_id="worker-1",
            lease_expires_at=NOW + timedelta(minutes=1),
            claimed_at=NOW,
        )
        == "accepted"
    )
    claimed = store.runs["run-due"]
    assert claimed.state_version == 2
    assert claimed.recovery_claim.owner_id == "worker-1"
    assert claimed.recovery_claim.lease_expires_at == NOW + timedelta(minutes=1)
    assert (
        store.claim_recovery(
            "run-due",
            expected_state_version=2,
            owner_id="worker-2",
            lease_expires_at=NOW + timedelta(minutes=2),
            claimed_at=NOW + timedelta(seconds=1),
        )
        == "conflict"
    )
    assert (
        store.claim_recovery(
            "run-stale",
            expected_state_version=1,
            owner_id="worker-2",
            lease_expires_at=NOW + timedelta(minutes=1),
            claimed_at=NOW,
        )
        == "accepted"
    )
    assert store.runs["run-stale"].recovery_claim.owner_id == "worker-2"


def test_recovery_lease_renewal_and_release_are_owner_and_version_fenced():
    store = InMemoryOrchestratorContractHarness()
    assert store.create(_run()) == "accepted"
    assert (
        store.claim_recovery(
            "run-1",
            expected_state_version=1,
            owner_id="worker-1",
            lease_expires_at=NOW + timedelta(minutes=1),
            claimed_at=NOW,
        )
        == "accepted"
    )
    assert store.delete_room("room-1", owner_id="deleter") == "conflict"
    assert (
        store.renew_recovery(
            "run-1",
            expected_state_version=1,
            owner_id="worker-1",
            lease_expires_at=NOW + timedelta(minutes=2),
            renewed_at=NOW + timedelta(seconds=10),
        )
        == "conflict"
    )
    assert (
        store.renew_recovery(
            "run-1",
            expected_state_version=2,
            owner_id="worker-2",
            lease_expires_at=NOW + timedelta(minutes=2),
            renewed_at=NOW + timedelta(seconds=10),
        )
        == "conflict"
    )
    assert (
        store.renew_recovery(
            "run-1",
            expected_state_version=2,
            owner_id="worker-1",
            lease_expires_at=NOW + timedelta(minutes=2),
            renewed_at=NOW + timedelta(seconds=10),
        )
        == "accepted"
    )
    assert store.runs["run-1"].state_version == 3
    assert (
        store.release_recovery(
            "run-1",
            expected_state_version=2,
            owner_id="worker-1",
            next_attempt_at=NOW + timedelta(minutes=3),
            released_at=NOW + timedelta(seconds=20),
        )
        == "conflict"
    )
    assert (
        store.release_recovery(
            "run-1",
            expected_state_version=3,
            owner_id="worker-2",
            next_attempt_at=NOW + timedelta(minutes=3),
            released_at=NOW + timedelta(seconds=20),
        )
        == "conflict"
    )
    assert (
        store.release_recovery(
            "run-1",
            expected_state_version=3,
            owner_id="worker-1",
            next_attempt_at=NOW + timedelta(minutes=3),
            released_at=NOW + timedelta(seconds=20),
        )
        == "accepted"
    )
    released = store.runs["run-1"]
    assert released.state_version == 4
    assert released.recovery_claim == RecoveryClaim(
        next_attempt_at=NOW + timedelta(minutes=3)
    )
    assert store.list_due_runs(due_at=NOW + timedelta(minutes=2), limit=10) == []
    assert [
        run.run_id
        for run in store.list_due_runs(due_at=NOW + timedelta(minutes=3), limit=10)
    ] == ["run-1"]


def test_call_is_durable_before_dispatch_side_effect():
    store = InMemoryOrchestratorContractHarness()
    assert store.create(_run()) == "accepted"
    assert store.dispatch("run-1", "call-1") == "conflict"

    assert (
        store.persist_call_before_dispatch(
            "run-1",
            call=_call(),
            dispatch_intent=_dispatch_intent(),
            expected_state_version=1,
        )
        == "accepted"
    )
    persisted = store.runs["run-1"]
    assert persisted.calls[0].call_id == "call-1"
    assert persisted.projection_outbox[0].payload == {"call_id": "call-1"}
    assert store.dispatch("run-1", "call-1") == "accepted"
    assert store.dispatch_log == ["call-1"]


def test_duplicate_callback_replays_without_duplicate_transcript_or_artifact_intent():
    store = InMemoryOrchestratorContractHarness()
    run = _run().model_copy(update={"calls": [_call("working")]})
    assert store.create(run) == "accepted"

    first = store.ingest_callback(
        "run-1",
        call_id="call-1",
        observation_id="observation-1",
        room_generation=1,
        message_id="tool-result-1",
        artifact_refs=["artifact-1"],
        observed_at=NOW + timedelta(seconds=1),
    )
    replay = store.ingest_callback(
        "run-1",
        call_id="call-1",
        observation_id="observation-1",
        room_generation=1,
        message_id="tool-result-duplicate",
        artifact_refs=["artifact-1"],
        observed_at=NOW + timedelta(seconds=2),
    )

    persisted = store.runs["run-1"]
    assert first == "accepted"
    assert replay == "replayed"
    assert [message.message_id for message in persisted.transcript].count(
        "tool-result-1"
    ) == 1
    assert persisted.artifact_refs == ["artifact-1"]
    assert [item.kind for item in persisted.projection_outbox] == [
        "project_call_observation"
    ]


def test_terminal_callback_is_a_deduplicated_audit_only_observation():
    store = InMemoryOrchestratorContractHarness()
    terminal_at = NOW + timedelta(seconds=1)
    terminal_call = _call("completed").model_copy(
        update={
            "artifact_refs": ["original-artifact"],
            "terminal_at": terminal_at,
            "updated_at": terminal_at,
        }
    )
    run = _run().model_copy(
        update={
            "status": "completed",
            "calls": [terminal_call],
            "artifact_refs": ["original-artifact"],
        }
    )
    assert store.create(run) == "accepted"

    first = store.ingest_callback(
        "run-1",
        call_id="call-1",
        observation_id="late-observation",
        room_generation=1,
        message_id="late-result",
        artifact_refs=["late-artifact"],
        observed_at=NOW + timedelta(seconds=2),
    )
    replay = store.ingest_callback(
        "run-1",
        call_id="call-1",
        observation_id="late-observation",
        room_generation=1,
        message_id="later-result",
        artifact_refs=["later-artifact"],
        observed_at=NOW + timedelta(seconds=3),
    )

    persisted = store.runs["run-1"]
    persisted_call = persisted.calls[0]
    assert first == "accepted"
    assert replay == "replayed"
    assert persisted.status == "completed"
    assert persisted_call.state == "completed"
    assert persisted_call.terminal_at == terminal_at
    assert persisted_call.artifact_refs == ["original-artifact"]
    assert persisted_call.processed_observation_ids == ["late-observation"]
    assert persisted.transcript == run.transcript
    assert persisted.artifact_refs == ["original-artifact"]
    assert persisted.projection_outbox == []


def test_crash_after_terminal_cas_is_repaired_idempotently_from_outbox():
    store = InMemoryOrchestratorContractHarness()
    original = _run()
    assert store.create(original) == "accepted"
    committed = commit_terminal_decision(
        original,
        facts=TerminalDecisionFacts(final_message_id="final-run-1"),
        request=TerminalCommitRequest(
            expected_state_version=1,
            command_id="complete-1",
            event_id="terminal-event-1",
            event_sequence=1,
            event_intent_id="append-terminal-event",
            final_message_intent_id="deliver-final-message",
            public_run_intent_id="project-public-run",
            final_message_target="room-1",
            public_run_target="run-1",
            created_at=NOW + timedelta(seconds=1),
        ),
    )
    assert committed.outcome == "accepted"
    store.save_authoritative(committed.run)
    assert store.event_store == {}
    assert store.delivered_dedupe_keys == set()

    assert store.repair_outbox("run-1", repaired_at=NOW + timedelta(seconds=2)) == 3
    repaired = store.runs["run-1"]
    assert repaired.status == "completed"
    assert repaired.projection_state == "settled"
    assert set(store.event_store) == {"terminal-event-1"}
    assert store.delivered_dedupe_keys == {
        "final-message:final-run-1",
        "run-completed:run-1",
    }
    assert store.repair_outbox("run-1", repaired_at=NOW + timedelta(seconds=3)) == 0
    assert len(store.event_store) == 1


def test_room_lock_and_generation_fence_deletion_against_claimed_side_effects():
    store = InMemoryOrchestratorContractHarness()
    run = _run().model_copy(update={"projection_outbox": [_dispatch_intent()]})
    assert store.create(run) == "accepted"
    assert (
        store.claim_projection(
            "run-1",
            "dispatch-call-1",
            owner_id="projector",
            room_generation=1,
        )
        == "accepted"
    )

    assert store.delete_room("room-1", owner_id="deleter") == "conflict"
    assert "run-1" in store.runs
    store.release_projection("run-1", "dispatch-call-1", owner_id="projector")
    assert store.delete_room("room-1", owner_id="deleter") == "accepted"
    assert store.room_generations["room-1"] == 2
    assert "run-1" not in store.runs
    assert (
        store.confirm_projection(
            "run-1",
            "dispatch-call-1",
            owner_id="projector",
            room_generation=1,
        )
        == "gone"
    )
    assert (
        store.ingest_callback(
            "run-1",
            call_id="call-1",
            observation_id="late",
            room_generation=1,
            message_id="late-result",
            artifact_refs=[],
            observed_at=NOW + timedelta(seconds=4),
        )
        == "gone"
    )
    assert "run-1" not in store.runs


def test_only_one_nonterminal_run_per_room_is_created():
    store = InMemoryOrchestratorContractHarness()
    first = _run("run-1")
    concurrent = _run("run-2")
    assert store.create(first) == "accepted"
    assert store.create(concurrent) == "conflict"

    store.runs["run-1"] = first.model_copy(update={"status": "completed"})
    assert store.create(concurrent) == "accepted"
