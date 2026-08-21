from __future__ import annotations

from datetime import timedelta

from execution.orchestrator import (
    AssistantMessage,
    RecoveryClaim,
    TerminalCommitRequest,
    TerminalDecisionFacts,
    TextPart,
    commit_terminal_decision,
)
from execution.orchestrator.contract_harness import InMemoryOrchestratorContractHarness

from ._orchestrator_v3_helpers import NOW, make_run


def _run(run_id: str, room_id: str):
    run = make_run()
    return run.model_copy(
        update={
            "run_id": run_id,
            "session_id": room_id,
            "room_id": room_id,
            "client_request_id": f"request-{run_id}",
            "request": run.request.model_copy(
                update={
                    "request_fingerprint": f"fingerprint-{run_id}",
                    "room_epoch": 1,
                    "user_message_id": f"user-{run_id}",
                }
            ),
        }
    )


def test_recovery_claim_is_owner_version_and_epoch_fenced():
    store = InMemoryOrchestratorContractHarness()
    run = _run("run-1", "room-1").model_copy(
        update={"recovery_claim": RecoveryClaim(next_attempt_at=NOW)}
    )
    assert store.create(run) == "accepted"
    assert (
        store.claim_recovery(
            "run-1",
            expected_state_version=0,
            owner_id="worker-1",
            lease_expires_at=NOW + timedelta(minutes=1),
            claimed_at=NOW,
        )
        == "accepted"
    )
    assert (
        store.renew_recovery(
            "run-1",
            expected_state_version=1,
            owner_id="worker-2",
            lease_expires_at=NOW + timedelta(minutes=2),
            renewed_at=NOW + timedelta(seconds=1),
        )
        == "conflict"
    )
    assert (
        store.renew_recovery(
            "run-1",
            expected_state_version=1,
            owner_id="worker-1",
            lease_expires_at=NOW + timedelta(minutes=2),
            renewed_at=NOW + timedelta(seconds=1),
        )
        == "accepted"
    )


def test_recovery_claim_renew_release_and_due_schedule_are_fully_fenced():
    store = InMemoryOrchestratorContractHarness()
    run = _run("run-recovery", "room-recovery").model_copy(
        update={"recovery_claim": RecoveryClaim(next_attempt_at=NOW)}
    )
    assert store.create(run) == "accepted"
    assert (
        store.claim_recovery(
            run.run_id,
            expected_state_version=0,
            owner_id="worker",
            lease_expires_at=NOW,
            claimed_at=NOW,
        )
        == "conflict"
    )
    assert (
        store.claim_recovery(
            run.run_id,
            expected_state_version=0,
            owner_id="worker",
            lease_expires_at=NOW + timedelta(minutes=1),
            claimed_at=NOW,
        )
        == "accepted"
    )
    assert (
        store.renew_recovery(
            run.run_id,
            expected_state_version=0,
            owner_id="worker",
            lease_expires_at=NOW + timedelta(minutes=2),
            renewed_at=NOW + timedelta(seconds=1),
        )
        == "conflict"
    )
    assert (
        store.renew_recovery(
            run.run_id,
            expected_state_version=1,
            owner_id="worker",
            lease_expires_at=NOW + timedelta(seconds=30),
            renewed_at=NOW + timedelta(seconds=1),
        )
        == "conflict"
    )
    assert (
        store.renew_recovery(
            run.run_id,
            expected_state_version=1,
            owner_id="worker",
            lease_expires_at=NOW + timedelta(minutes=2),
            renewed_at=NOW + timedelta(minutes=1),
        )
        == "conflict"
    )
    assert (
        store.renew_recovery(
            run.run_id,
            expected_state_version=1,
            owner_id="worker",
            lease_expires_at=NOW + timedelta(minutes=2),
            renewed_at=NOW + timedelta(seconds=1),
        )
        == "accepted"
    )
    assert (
        store.release_recovery(
            run.run_id,
            expected_state_version=2,
            owner_id="other",
            next_attempt_at=NOW + timedelta(minutes=5),
            released_at=NOW + timedelta(seconds=2),
        )
        == "conflict"
    )
    assert (
        store.release_recovery(
            run.run_id,
            expected_state_version=2,
            owner_id="worker",
            next_attempt_at=NOW + timedelta(minutes=5),
            released_at=NOW + timedelta(seconds=2),
        )
        == "accepted"
    )
    assert store.list_due_runs(due_at=NOW + timedelta(minutes=4), limit=10) == []
    assert [
        item.run_id
        for item in store.list_due_runs(due_at=NOW + timedelta(minutes=5), limit=10)
    ] == [run.run_id]


def test_deletion_waits_for_live_projection_claim_and_fences_stale_owner():
    store = InMemoryOrchestratorContractHarness()
    run = _run("run-claim", "room-claim")
    assert store.create(run) == "accepted"
    assert (
        store.claim_projection(
            run.run_id,
            "projection-1",
            owner_id="projector",
            room_epoch=1,
        )
        == "accepted"
    )
    assert store.delete_room(run.room_id, owner_id="deleter") == "conflict"
    assert run.run_id in store.runs
    store.release_projection(run.run_id, "projection-1", owner_id="projector")
    assert store.delete_room(run.room_id, owner_id="deleter") == "accepted"
    assert run.run_id not in store.runs
    assert (
        store.confirm_projection(
            run.run_id,
            "projection-1",
            owner_id="projector",
            room_epoch=1,
        )
        == "gone"
    )


def test_only_one_nonterminal_run_per_room():
    store = InMemoryOrchestratorContractHarness()
    first = _run("run-1", "room-1")
    second = _run("run-2", "room-1")
    assert store.create(first) == "accepted"
    assert store.create(second) == "conflict"
    store.runs[first.run_id] = first.model_copy(update={"status": "completed"})
    assert store.create(second) == "accepted"


def test_room_epoch_fences_old_recovery_after_delete():
    store = InMemoryOrchestratorContractHarness()
    assert store.create(_run("run-1", "room-1")) == "accepted"
    assert store.delete_room("room-1", owner_id="deleter") == "accepted"
    assert store.room_epochs["room-1"] == 2
    recreated = _run("run-2", "room-1").model_copy(
        update={
            "request": _run("run-2", "room-1").request.model_copy(
                update={"room_epoch": 2}
            )
        }
    )
    assert store.create(recreated) == "accepted"


def test_due_run_inventory_excludes_live_leases_and_terminal_runs():
    store = InMemoryOrchestratorContractHarness()
    due = _run("due", "room-due")
    live = _run("live", "room-live").model_copy(
        update={
            "recovery_claim": RecoveryClaim(
                owner_id="worker", lease_expires_at=NOW + timedelta(minutes=1)
            )
        }
    )
    terminal = _run("terminal", "room-terminal").model_copy(
        update={"status": "completed"}
    )
    for run in (due, live, terminal):
        assert store.create(run) == "accepted"
    assert [run.run_id for run in store.list_due_runs(due_at=NOW, limit=10)] == ["due"]


def test_crash_after_terminal_cas_repairs_outbox_idempotently():
    store = InMemoryOrchestratorContractHarness()
    original = _run("run-1", "room-1")
    final = AssistantMessage(
        message_id="final-1",
        content=[TextPart(text="done")],
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        created_at=NOW,
    )
    original = original.model_copy(
        update={
            "status": "finalizing",
            "transcript": [*original.transcript, final],
            "proposed_final_message_id": final.message_id,
        }
    )
    assert store.create(original) == "accepted"
    committed = commit_terminal_decision(
        original,
        facts=TerminalDecisionFacts(final_message_id="final-1"),
        request=TerminalCommitRequest(
            expected_state_version=0,
            command_id="complete",
            event_id="event-terminal",
            event_sequence=1,
            event_intent_id="event-intent",
            final_message_intent_id="message-intent",
            public_run_intent_id="run-intent",
            final_message_target="room-1",
            public_run_target="run-1",
            created_at=NOW,
        ),
    )
    store.save_authoritative(committed.run)
    assert store.repair_outbox("run-1", repaired_at=NOW + timedelta(seconds=1)) == 3
    assert store.runs["run-1"].projection_state == "settled"
    assert store.repair_outbox("run-1", repaired_at=NOW + timedelta(seconds=2)) == 0
