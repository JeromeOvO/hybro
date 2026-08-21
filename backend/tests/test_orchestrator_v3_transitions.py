from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from execution.orchestrator import (
    ArtifactDeliveryCheck,
    AssistantMessage,
    TerminalDecisionFacts,
    TextPart,
    ToolAcceptance,
    ToolBatchEntry,
    ToolCallBatch,
    ToolInvocation,
    evaluate_terminal_decision,
)
from execution.orchestrator.models import ResolvedTool, ToolBindingRef, ToolDefinition

from ._orchestrator_v3_helpers import NOW, make_run


def _finalizing_run():
    final = AssistantMessage(
        message_id="final",
        content=[TextPart(text="done")],
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        created_at=NOW,
    )
    return make_run().model_copy(
        update={
            "transcript": [*make_run().transcript, final],
            "proposed_final_message_id": "final",
            "status": "finalizing",
        }
    )


def _entry(state: str) -> ToolBatchEntry:
    tool = ResolvedTool(
        definition=ToolDefinition(
            name="agent_test",
            label="Agent",
            description="test",
            input_schema={"type": "object"},
            execution_mode="parallel",
            side_effect_level="external",
        ),
        binding=ToolBindingRef(binding_id="binding", binding_digest="digest"),
    )
    invocation = ToolInvocation(
        invocation_id="call-1",
        run_id="run-1",
        expected_run_version=0,
        assistant_message_id="assistant",
        source_index=0,
        causation_id="assistant",
        idempotency_key="key",
        tool=tool,
        arguments={},
        deadline_at=NOW + timedelta(minutes=1),
    )
    acceptance = ToolAcceptance(
        acceptance_id="acceptance",
        invocation_id="call-1",
        idempotency_key="key",
        accepted_at=NOW,
    )
    return ToolBatchEntry(
        call_id="call-1",
        assistant_message_id="assistant",
        source_index=0,
        tool_name="agent_test",
        state=state,
        invocation=invocation,
        acceptance=acceptance,
    )


def test_terminal_evaluation_derives_waiting_only_from_generic_tool_batch():
    run = _finalizing_run().model_copy(
        update={
            "tool_batches": [
                ToolCallBatch(
                    assistant_message_id="assistant",
                    entries=[_entry("waiting_external")],
                )
            ]
        }
    )

    decision = evaluate_terminal_decision(
        run, TerminalDecisionFacts(final_message_id="final")
    )

    assert decision.decision == "waiting_external"
    assert "ToolBatch" in decision.reason


def test_terminal_evaluation_derives_user_wait_from_generic_tool_batch():
    run = _finalizing_run().model_copy(
        update={
            "tool_batches": [
                ToolCallBatch(
                    assistant_message_id="assistant", entries=[_entry("input_required")]
                )
            ]
        }
    )

    decision = evaluate_terminal_decision(
        run, TerminalDecisionFacts(final_message_id="final")
    )

    assert decision.decision == "awaiting_user"


def test_terminal_evaluation_has_no_embedded_a2a_authority():
    run = make_run()
    assert run.schema_version == 5
    assert "calls" not in type(run).model_fields
    assert "pending_interaction_ids" not in type(run).model_fields
    payload = run.model_dump(mode="json")
    payload.update(schema_version=3, calls=[], pending_interaction_ids=[])
    with pytest.raises(ValidationError):
        type(run).model_validate(payload)


def test_cancellation_winner_prevents_completion():
    run = _finalizing_run()
    decision = evaluate_terminal_decision(
        run,
        TerminalDecisionFacts(
            final_message_id="final",
            cancellation_won=True,
        ),
    )
    assert decision.decision == "terminal_conflict"
    assert decision.reason == "cancellation already won"


def test_terminal_artifact_delivery_remains_machine_verified():
    final = AssistantMessage(
        message_id="final",
        content=[{"kind": "artifact_ref", "artifact_ref": "artifact-1"}],
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        created_at=NOW,
    )
    run = make_run().model_copy(
        update={
            "transcript": [*make_run().transcript, final],
            "proposed_final_message_id": "final",
            "status": "finalizing",
            "artifact_refs": ["artifact-1"],
        }
    )
    decision = evaluate_terminal_decision(
        run,
        TerminalDecisionFacts(
            final_message_id="final",
            artifact_checks=[
                ArtifactDeliveryCheck(
                    artifact_ref="artifact-1",
                    exists=True,
                    belongs_to_run=True,
                    belongs_to_room=True,
                    deliverable=True,
                )
            ],
        ),
    )

    assert decision.decision == "ready"
