from __future__ import annotations

from datetime import timedelta

import pytest

from execution.orchestrator import (
    AssistantMessage,
    ProjectionIntent,
    TerminalCommitRequest,
    TerminalDecisionFacts,
    TextPart,
    commit_terminal_decision,
    evaluate_projection_settlement,
    transition_projection_intent,
    transition_projection_settlement,
)

from ._orchestrator_helpers import NOW, make_run


def finalizing(*, intents=None):
    base = make_run()
    final = AssistantMessage(
        message_id="final-1",
        content=[TextPart(text="done")],
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        created_at=NOW,
    )
    return base.model_copy(
        update={
            "status": "finalizing",
            "transcript": [*base.transcript, final],
            "proposed_final_message_id": final.message_id,
            "projection_outbox": intents or [],
        }
    )


def request(**updates):
    values = {
        "expected_state_version": 0,
        "command_id": "complete-command",
        "event_id": "complete-event",
        "event_sequence": 1,
        "event_intent_id": "event-intent",
        "final_message_intent_id": "message-intent",
        "public_run_intent_id": "run-intent",
        "final_message_target": "room-1",
        "public_run_target": "run-1",
        "created_at": NOW,
    }
    values.update(updates)
    return TerminalCommitRequest(**values)


def facts():
    return TerminalDecisionFacts(final_message_id="final-1")


def intent(intent_id: str, status: str, *, required: bool = True):
    return ProjectionIntent(
        intent_id=intent_id,
        kind="projection",
        target="target",
        dedupe_key=intent_id,
        required=required,
        event_id="event-1",
        event_sequence=1,
        causation_id="cause",
        payload={},
        status=status,
    )


def committed(status="pending"):
    run = commit_terminal_decision(finalizing(), facts=facts(), request=request()).run
    return run.model_copy(
        update={
            "projection_outbox": [
                item.model_copy(update={"status": status})
                for item in run.projection_outbox
            ]
        }
    )


def test_terminal_cas_persists_complete_outbox_and_replays_exactly_once():
    result = commit_terminal_decision(finalizing(), facts=facts(), request=request())
    assert result.outcome == "accepted"
    assert result.run.status == "completed"
    assert {item.kind for item in result.run.projection_outbox} == {
        "append_orchestrator_event",
        "deliver_final_message",
        "project_terminal_run_status",
    }
    replay = commit_terminal_decision(
        result.run,
        facts=facts(),
        request=request(expected_state_version=1),
    )
    assert replay.outcome == "replayed"


def test_terminal_cas_rejects_stale_version_and_non_next_sequence():
    stale = commit_terminal_decision(
        finalizing(), facts=facts(), request=request(expected_state_version=1)
    )
    assert stale.outcome == "conflict"
    history = intent("history", "completed").model_copy(
        update={
            "kind": "append_orchestrator_event",
            "event_id": "history-event",
            "event_sequence": 1,
        }
    )
    wrong = commit_terminal_decision(
        finalizing(intents=[history]),
        facts=facts(),
        request=request(event_sequence=3),
    )
    assert wrong.outcome == "conflict"
    assert wrong.evaluation.reason == "expected terminal event sequence 2"


@pytest.mark.parametrize("status", ["pending", "claimed"])
def test_required_unfinished_projection_stays_pending(status):
    assert evaluate_projection_settlement([intent("required", status)]) == "pending"


def test_optional_blocked_projection_does_not_block_required_settlement():
    assert (
        evaluate_projection_settlement(
            [
                intent("required", "completed"),
                intent("optional", "blocked", required=False),
            ]
        )
        == "settled"
    )


def test_empty_projection_inventory_never_settles():
    assert evaluate_projection_settlement([]) == "pending"


@pytest.mark.parametrize(
    "missing_kind",
    [
        "append_orchestrator_event",
        "deliver_final_message",
        "project_terminal_run_status",
    ],
)
def test_terminal_projection_requires_one_complete_mandatory_group(missing_kind):
    terminal = committed("completed")
    terminal = terminal.model_copy(
        update={
            "projection_outbox": [
                item for item in terminal.projection_outbox if item.kind != missing_kind
            ]
        }
    )
    result = transition_projection_settlement(
        terminal,
        expected_state_version=1,
        updated_at=NOW + timedelta(seconds=1),
    )
    assert result.outcome == "replayed"
    assert result.run.projection_state == "pending"


def test_required_blocked_outbox_blocks_without_rewriting_terminal_winner():
    terminal = committed("blocked")
    result = transition_projection_settlement(
        terminal,
        expected_state_version=1,
        updated_at=NOW + timedelta(seconds=1),
    )
    assert result.outcome == "accepted"
    assert result.run.status == "completed"
    assert result.run.projection_state == "blocked"


def test_terminal_batch_settles_with_later_optional_projection_intent():
    terminal = committed("completed")
    optional = terminal.projection_outbox[0].model_copy(
        update={
            "intent_id": "optional-later",
            "kind": "analytics_optional",
            "required": False,
            "event_id": "event-later",
            "event_sequence": 99,
            "causation_id": "later",
            "payload": {},
        }
    )
    terminal = terminal.model_copy(
        update={"projection_outbox": [*terminal.projection_outbox, optional]}
    )
    result = transition_projection_settlement(
        terminal,
        expected_state_version=1,
        updated_at=NOW + timedelta(seconds=1),
    )
    assert result.outcome == "accepted"
    assert result.run.projection_state == "settled"


def test_terminal_batch_rejects_ambiguous_and_payload_mismatched_groups():
    terminal = committed("completed")
    duplicate = [
        item.model_copy(
            update={
                "intent_id": f"duplicate-{index}",
                "event_id": "event-duplicate",
                "event_sequence": 2,
                "causation_id": "duplicate-cause",
                "payload": {
                    **item.payload,
                    "event_id": "event-duplicate",
                    "event_sequence": 2,
                    "causation_id": "duplicate-cause",
                },
            }
        )
        for index, item in enumerate(terminal.projection_outbox)
    ]
    ambiguous = terminal.model_copy(
        update={"projection_outbox": [*terminal.projection_outbox, *duplicate]}
    )
    result = transition_projection_settlement(
        ambiguous,
        expected_state_version=1,
        updated_at=NOW + timedelta(seconds=1),
    )
    assert result.outcome == "replayed"
    assert result.run.projection_state == "pending"

    intents = list(terminal.projection_outbox)
    event_index = next(
        index
        for index, item in enumerate(intents)
        if item.kind == "append_orchestrator_event"
    )
    intents[event_index] = intents[event_index].model_copy(
        update={"payload": {**intents[event_index].payload, "event_type": "wrong"}}
    )
    mismatch = terminal.model_copy(update={"projection_outbox": intents})
    result = transition_projection_settlement(
        mismatch,
        expected_state_version=1,
        updated_at=NOW + timedelta(seconds=1),
    )
    assert result.outcome == "replayed"
    assert result.run.projection_state == "pending"


def test_all_required_completed_settles_exactly_once():
    terminal = committed("completed")
    first = transition_projection_settlement(
        terminal,
        expected_state_version=1,
        updated_at=NOW + timedelta(seconds=1),
    )
    second = transition_projection_settlement(
        first.run,
        expected_state_version=first.run.state_version,
        updated_at=NOW + timedelta(seconds=2),
    )
    assert first.outcome == "accepted"
    assert first.run.projection_state == "settled"
    assert second.outcome == "replayed"


def test_projection_intent_claim_complete_and_terminal_behavior():
    pending = intent("required", "pending")
    claimed = transition_projection_intent(
        pending,
        to_status="claimed",
        claim_owner="worker",
        claim_expires_at=NOW + timedelta(seconds=30),
    )
    completed = transition_projection_intent(claimed, to_status="completed")
    assert claimed.attempt_count == 1
    assert completed.claim_owner is None
    with pytest.raises(ValueError):
        transition_projection_intent(completed, to_status="claimed")
