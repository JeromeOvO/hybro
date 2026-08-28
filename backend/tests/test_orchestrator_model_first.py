"""Focused tests for the model-first HITL kernel helpers (Phase 0.5 + Phase 1)."""

from __future__ import annotations

from execution.orchestrator.kernel import (
    _batch_is_parked,
    _canonical_turn_closure,
    _find_join_target,
    _flush_batch,
)
from execution.orchestrator.models import (
    ToolBatchEntry,
    ToolCallBatch,
    ToolResult,
)
from tests._orchestrator_helpers import NOW, make_run


def _entry(
    call_id: str,
    *,
    state: str = "terminal",
    presented: bool = False,
    result_flushed: bool = False,
    suspended_call_record_id: str | None = None,
    interaction_id: str | None = None,
    interaction_fingerprint: str | None = None,
    opaque_public_call_id: str | None = None,
    assistant_message_id: str = "assistant-1",
) -> ToolBatchEntry:
    return ToolBatchEntry(
        call_id=call_id,
        assistant_message_id=assistant_message_id,
        source_index=0,
        tool_name="agent_abc",
        state=state,  # type: ignore[arg-type]
        presented=presented,
        result_flushed=result_flushed,
        suspended_call_record_id=suspended_call_record_id,
        interaction_id=interaction_id,
        interaction_fingerprint=interaction_fingerprint,
        opaque_public_call_id=opaque_public_call_id or f"inv_{call_id}",
        buffered_terminal_result=(
            ToolResult(
                call_id=call_id,
                tool_name="agent_abc",
                status="completed",
                content=[],
                artifact_refs=[],
            )
            if state == "terminal"
            else None
        ),
    )


def test_batch_is_parked_only_when_every_entry_settled_or_presented():
    parked = ToolCallBatch(
        assistant_message_id="assistant-1",
        entries=[
            _entry("call-1", state="input_required", presented=True),
            _entry("call-2", state="terminal", result_flushed=True),
        ],
    )
    assert _batch_is_parked(parked)

    open_batch = parked.model_copy(
        update={
            "entries": [
                _entry("call-1", state="input_required", presented=False),
            ]
        }
    )
    assert not _batch_is_parked(open_batch)


def test_canonical_turn_closure_requires_terminal_entries_and_uses_latest_message():
    run = make_run().model_copy(
        update={
            "tool_batches": [
                ToolCallBatch(
                    assistant_message_id="assistant-1",
                    internal_turn_id="turn-1",
                    entries=[
                        _entry(
                            "call-1",
                            state="input_required",
                            presented=True,
                            suspended_call_record_id="parent-1",
                            interaction_id="interaction-1",
                            interaction_fingerprint="fp",
                            opaque_public_call_id="inv_call-1",
                        ),
                    ],
                ),
            ]
        }
    )
    # A presented interaction keeps the turn open.
    assert _canonical_turn_closure(run, "turn-1") is None

    closed = run.model_copy(
        update={
            "tool_batches": [
                ToolCallBatch(
                    assistant_message_id="assistant-1",
                    internal_turn_id="turn-1",
                    entries=[
                        _entry("call-1", opaque_public_call_id="inv_call-1"),
                        _entry("call-2", opaque_public_call_id="inv_call-2"),
                    ],
                ),
                ToolCallBatch(
                    assistant_message_id="assistant-2",
                    internal_turn_id="turn-1",
                    entries=[
                        _entry(
                            "call-3",
                            opaque_public_call_id="inv_call-3",
                            assistant_message_id="assistant-2",
                        )
                    ],
                ),
            ]
        }
    )
    closure = _canonical_turn_closure(closed, "turn-1")
    assert closure is not None
    assert closure.message_id == "assistant-2"
    assert closure.public_tool_call_ids == (
        "inv_call-1",
        "inv_call-2",
        "inv_call-3",
    )


def test_find_join_target_prefers_most_recent_presented_interaction():
    run = make_run().model_copy(
        update={
            "tool_batches": [
                ToolCallBatch(
                    assistant_message_id="assistant-1",
                    entries=[
                        _entry(
                            "call-1",
                            state="input_required",
                            presented=True,
                            suspended_call_record_id="parent-1",
                            interaction_id="interaction-1",
                            interaction_fingerprint="fp-1",
                        ),
                        _entry(
                            "call-2",
                            state="input_required",
                            presented=True,
                            suspended_call_record_id="parent-2",
                            interaction_id="interaction-2",
                            interaction_fingerprint="fp-2",
                        ),
                    ],
                ),
            ]
        }
    )
    assert _find_join_target(run, "agent_abc") == "parent-2"
    assert _find_join_target(run, "other_agent") is None


def test_flush_batch_materializes_terminal_entries_without_closing_mixed_batch():
    batch = ToolCallBatch(
        assistant_message_id="assistant-1",
        entries=[
            _entry("call-1", state="input_required", presented=True),
            _entry("call-2", state="terminal"),
        ],
    )
    transcript, flushed = _flush_batch([], batch, NOW)
    assert not flushed.results_flushed
    assert [message.message_id for message in transcript] == ["tool-result:call-2"]
    assert flushed.entries[1].result_flushed is True
    assert flushed.entries[0].result_flushed is False

    # Final flush closes the batch after the suspended entry terminalizes.
    final_batch = flushed.model_copy(
        update={
            "entries": [
                _entry("call-1", state="terminal", result_flushed=False),
                flushed.entries[1],
            ]
        }
    )
    _, closed = _flush_batch(transcript, final_batch, NOW)
    assert closed.results_flushed is True
