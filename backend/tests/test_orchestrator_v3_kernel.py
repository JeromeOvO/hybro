from __future__ import annotations

import asyncio

import pytest

from execution.orchestrator.budget import BudgetExceeded, BudgetPolicy
from execution.orchestrator.fake_tools import RecordingFakeToolRuntime
from execution.orchestrator.in_memory import (
    InMemoryOrchestratorRunStore,
    InMemoryRunStoreResult,
)
from execution.orchestrator.kernel import KernelConflict
from execution.orchestrator.models import (
    AssistantMessage,
    ModelStreamEvent,
    SessionNotice,
    TextPart,
    ToolBatchEntry,
    ToolCall,
    ToolCallBatch,
    ToolInvocation,
    ToolObservation,
    ToolResult,
    ToolResultMessage,
    ToolSuspension,
    UsageRecord,
)
from tests._orchestrator_v3_helpers import (
    NOW,
    EventCancellationSignal,
    NeverCancelled,
    final_events,
    make_kernel,
    make_run,
    tool_events,
)


@pytest.mark.asyncio
async def test_kernel_completes_final_answer_without_tools():
    kernel, store, runtime, _ = await make_kernel([final_events("answer")])
    run_id = next(iter(store.runs))
    result = await kernel.run(run_id, signal=NeverCancelled())
    assert result.outcome == "final_answer"
    assert result.run.status == "completed"
    assert result.run.projection_state == "settled"
    assert runtime.requests[0].tools


@pytest.mark.asyncio
async def test_finalizing_run_resumes_terminal_commit_without_new_model_turn():
    run = make_run()
    assistant = AssistantMessage(
        message_id="pending-final",
        content=[TextPart(text="durable answer")],
        tool_calls=[],
        finish_reason="stop",
        usage=None,
        created_at=NOW,
    )
    run = run.model_copy(
        update={
            "status": "finalizing",
            "proposed_final_message_id": assistant.message_id,
            "transcript": [*run.transcript, assistant],
        }
    )
    kernel, _, runtime, _ = await make_kernel([], run=run)

    result = await kernel.run(run.run_id, signal=NeverCancelled())

    assert result.outcome == "final_answer"
    assert result.run.status == "completed"
    assert result.run.proposed_final_message_id == assistant.message_id
    assert runtime.requests == []
    assert [
        item.message_id
        for item in result.run.transcript
        if isinstance(item, AssistantMessage)
    ] == [assistant.message_id]


@pytest.mark.asyncio
async def test_tool_calling_assistant_and_pending_batch_checkpoint_atomically():
    kernel, store, _, _ = await make_kernel([])
    run = next(iter(store.runs.values()))
    assistant = AssistantMessage(
        message_id="assistant-tools",
        content=[],
        tool_calls=[
            ToolCall(
                call_id="call-atomic",
                tool_name="fake_agent_echo",
                arguments={"value": "ok"},
            )
        ],
        finish_reason="tool_calls",
        usage=None,
        created_at=NOW,
    )

    stored = await kernel._append_assistant(run, assistant)

    assert stored.transcript[-1] == assistant
    assert len(stored.tool_batches) == 1
    assert stored.tool_batches[0].assistant_message_id == assistant.message_id
    assert stored.tool_batches[0].entries[0].call_id == "call-atomic"
    assert stored.tool_batches[0].entries[0].state == "pending"


@pytest.mark.asyncio
async def test_running_run_reconstructs_missing_tool_batch_after_crash():
    run = make_run()
    assistant = AssistantMessage(
        message_id="assistant-torn",
        content=[],
        tool_calls=[
            ToolCall(
                call_id="call-torn",
                tool_name="fake_agent_echo",
                arguments={"value": "recovered"},
            )
        ],
        finish_reason="tool_calls",
        usage=None,
        created_at=NOW,
    )
    run = run.model_copy(update={"transcript": [*run.transcript, assistant]})
    kernel, _, runtime, tools = await make_kernel([final_events()], run=run)

    result = await kernel.run(run.run_id, signal=NeverCancelled())

    assert result.outcome == "final_answer"
    assert tools.accept_log == ["call-torn"]
    assert tools.execute_log == ["call-torn"]
    assert result.run.tool_batches[0].results_flushed is True
    assert len(runtime.requests) == 1


@pytest.mark.asyncio
async def test_reconstructed_tool_batch_can_suspend_and_accept_observation():
    run = make_run()
    assistant = AssistantMessage(
        message_id="assistant-torn-pause",
        content=[],
        tool_calls=[
            ToolCall(
                call_id="call-torn-pause",
                tool_name="fake_agent_pause",
                arguments={"status": "waiting_external"},
            )
        ],
        finish_reason="tool_calls",
        usage=None,
        created_at=NOW,
    )
    run = run.model_copy(update={"transcript": [*run.transcript, assistant]})
    kernel, _, runtime, _ = await make_kernel([final_events()], run=run)

    waiting = await kernel.run(run.run_id, signal=NeverCancelled())
    assert waiting.outcome == "waiting_external"
    assert waiting.run.status == "waiting_external"

    result = await kernel.observe_tool(
        run.run_id,
        ToolObservation(
            observation_id="torn-complete",
            invocation_id="call-torn-pause",
            outcome=ToolResult(
                call_id="call-torn-pause",
                tool_name="fake_agent_pause",
                status="completed",
                content=[TextPart(text="done")],
                artifact_refs=[],
            ),
            observed_at=NOW,
        ),
        signal=NeverCancelled(),
    )

    assert result.outcome == "final_answer"
    assert len(runtime.requests) == 1


@pytest.mark.asyncio
async def test_kernel_accepts_before_execute_and_feeds_result_to_next_turn():
    kernel, store, runtime, tools = await make_kernel(
        [
            tool_events(("call-1", "fake_agent_echo", '{"value":"hello"}')),
            final_events(),
        ]
    )
    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())
    assert result.outcome == "final_answer"
    assert tools.accept_log == ["call-1"]
    assert tools.execute_log == ["call-1"]
    assert runtime.requests[1].messages[-1].role == "tool"
    assert runtime.requests[1].messages[-1].content[0].tool_name == "fake_agent_echo"
    assert result.run.tool_batches[0].results_flushed is True


@pytest.mark.asyncio
async def test_kernel_correlates_unknown_and_schema_invalid_calls_without_execution():
    kernel, store, runtime, tools = await make_kernel(
        [
            tool_events(
                ("unknown", "not_registered", "{}"),
                ("invalid", "fake_agent_pause", "{}"),
                ("ok", "fake_agent_echo", '{"value":1,"structured":true}'),
            ),
            final_events(),
        ]
    )
    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())
    messages = [
        item for item in result.run.transcript if isinstance(item, ToolResultMessage)
    ]
    assert [item.call_id for item in messages] == ["unknown", "invalid", "ok"]
    assert [item.error_code for item in messages] == [
        "invalid_tool_call",
        "invalid_tool_call",
        None,
    ]
    assert tools.execute_log == ["ok"]
    assert runtime.requests[1].messages[-3].role == "tool"


@pytest.mark.asyncio
async def test_malformed_tool_call_adds_stable_notice_and_never_invokes_tools():
    malformed = [
        ModelStreamEvent(kind="attempt_started", attempt=1),
        ModelStreamEvent(
            kind="tool_call_start", call_id="bad", tool_name="fake_agent_echo"
        ),
        ModelStreamEvent(kind="tool_call_arguments_delta", call_id="bad", delta="{"),
        ModelStreamEvent(kind="finish", finish_reason="tool_calls"),
    ]
    kernel, store, _, tools = await make_kernel([malformed, final_events()])
    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())
    notices = [
        item for item in result.run.transcript if isinstance(item, SessionNotice)
    ]
    assert len(notices) == 1
    assert notices[0].related_call_id == "bad"
    assert tools.accept_log == tools.execute_log == []


@pytest.mark.asyncio
async def test_suspended_mixed_batch_flushes_once_in_source_order_after_observation():
    kernel, store, runtime, tools = await make_kernel(
        [
            tool_events(
                ("bad", "fake_agent_echo", "{}"),
                ("done", "fake_agent_echo", '{"value":"ok"}'),
                ("wait", "fake_agent_pause", '{"status":"waiting_external"}'),
            ),
            final_events("resumed"),
        ]
    )
    run_id = next(iter(store.runs))
    waiting = await kernel.run(run_id, signal=NeverCancelled())
    assert waiting.outcome == "waiting_external"
    assert not any(
        isinstance(item, ToolResultMessage) for item in waiting.run.transcript
    )
    assert tools.execute_log == ["done", "wait"]

    observation = ToolObservation(
        observation_id="observation-1",
        invocation_id="wait",
        outcome=ToolResult(
            call_id="wait",
            tool_name="fake_agent_pause",
            status="completed",
            content=[TextPart(text="external done")],
            artifact_refs=[],
        ),
        observed_at=NOW,
    )
    result = await kernel.observe_tool(run_id, observation, signal=NeverCancelled())
    results = [
        item for item in result.run.transcript if isinstance(item, ToolResultMessage)
    ]
    assert [item.call_id for item in results] == ["bad", "done", "wait"]
    assert result.run.tool_batches[0].results_flushed is True
    assert runtime.requests[-1].messages[-1].role == "tool"

    replay = await kernel.observe_tool(run_id, observation, signal=NeverCancelled())
    replay_results = [
        item for item in replay.run.transcript if isinstance(item, ToolResultMessage)
    ]
    assert len(replay_results) == 3


@pytest.mark.asyncio
async def test_cas_conflict_after_acceptance_reconciles_without_duplicate_execution():
    class ConflictOnceStore(InMemoryOrchestratorRunStore):
        conflicted = False

        async def cas_mutate(self, run, *, expected_state_version, command_id):
            if command_id.startswith("accepted-tool:") and not self.conflicted:
                self.conflicted = True
                current = self.runs[run.run_id]
                self.runs[run.run_id] = current.model_copy(
                    update={"state_version": current.state_version + 1}
                )
                return InMemoryRunStoreResult("conflict", self.runs[run.run_id])
            return await super().cas_mutate(
                run,
                expected_state_version=expected_state_version,
                command_id=command_id,
            )

    store = ConflictOnceStore()
    kernel, store, _, tools = await make_kernel(
        [tool_events(("call-1", "fake_agent_echo", '{"value":"ok"}')), final_events()],
        run_store=store,
    )
    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())
    assert result.outcome == "final_answer"
    assert tools.accept_log == ["call-1"]
    assert tools.execute_log == ["call-1"]


@pytest.mark.asyncio
async def test_stale_tool_entry_writer_cannot_overwrite_terminal_result():
    winner = ToolResult(
        call_id="call-1",
        tool_name="fake_agent_echo",
        status="completed",
        content=[TextPart(text="winner")],
        artifact_refs=[],
    )

    class InterveningWinnerStore(InMemoryOrchestratorRunStore):
        conflicted = False

        async def cas_mutate(self, run, *, expected_state_version, command_id):
            if command_id == "stale-writer" and not self.conflicted:
                self.conflicted = True
                current = self.runs[run.run_id]
                batch = current.tool_batches[0]
                entry = batch.entries[0].model_copy(
                    update={
                        "state": "terminal",
                        "buffered_terminal_result": winner,
                    }
                )
                self.runs[run.run_id] = current.model_copy(
                    update={
                        "tool_batches": [batch.model_copy(update={"entries": [entry]})],
                        "state_version": current.state_version + 1,
                    }
                )
                return InMemoryRunStoreResult("conflict", self.runs[run.run_id])
            return await super().cas_mutate(
                run,
                expected_state_version=expected_state_version,
                command_id=command_id,
            )

    run = make_run()
    assistant = AssistantMessage(
        message_id="assistant-1",
        content=[],
        tool_calls=[
            ToolCall(
                call_id="call-1",
                tool_name="fake_agent_echo",
                arguments={"value": "ok"},
            )
        ],
        finish_reason="tool_calls",
        usage=None,
        created_at=NOW,
    )
    batch = ToolCallBatch(
        assistant_message_id=assistant.message_id,
        entries=[
            ToolBatchEntry(
                call_id="call-1",
                assistant_message_id=assistant.message_id,
                source_index=0,
                tool_name="fake_agent_echo",
            )
        ],
    )
    run = run.model_copy(
        update={"transcript": [*run.transcript, assistant], "tool_batches": [batch]}
    )
    store = InterveningWinnerStore()
    kernel, store, _, _ = await make_kernel([], run=run, run_store=store)
    loser = ToolResult(
        call_id="call-1",
        tool_name="fake_agent_echo",
        status="rejected",
        content=[TextPart(text="loser")],
        artifact_refs=[],
        error_code="invalid_tool_call",
    )

    with pytest.raises(KernelConflict, match="entry changed"):
        await kernel._update_entry(
            run,
            0,
            0,
            state="terminal",
            result=loser,
            command="stale-writer",
        )

    stored = await store.load(run.run_id)
    assert stored.tool_batches[0].entries[0].buffered_terminal_result == winner


@pytest.mark.asyncio
async def test_acceptance_failed_sibling_is_retained_until_suspension_finishes():
    tools = RecordingFakeToolRuntime(fail_accept_for={"fake_agent_fail"})
    kernel, store, _, _ = await make_kernel(
        [
            tool_events(
                ("accept-fail", "fake_agent_fail", "{}"),
                ("wait", "fake_agent_pause", '{"status":"input_required"}'),
            ),
            final_events(),
        ],
        tool_runtime=tools,
    )
    run_id = next(iter(store.runs))
    waiting = await kernel.run(run_id, signal=NeverCancelled())
    assert waiting.outcome == "awaiting_user"
    assert [entry.state for entry in waiting.run.tool_batches[0].entries] == [
        "terminal",
        "input_required",
    ]

    result = await kernel.observe_tool(
        run_id,
        ToolObservation(
            observation_id="input-1",
            invocation_id="wait",
            outcome=ToolResult(
                call_id="wait",
                tool_name="fake_agent_pause",
                status="completed",
                content=[TextPart(text="provided")],
                artifact_refs=[],
            ),
            observed_at=NOW,
        ),
        signal=NeverCancelled(),
    )
    messages = [
        item for item in result.run.transcript if isinstance(item, ToolResultMessage)
    ]
    assert [item.call_id for item in messages] == ["accept-fail", "wait"]
    assert messages[0].error_code == "acceptance_failed"


@pytest.mark.asyncio
async def test_running_run_recovers_persisted_accepted_tool_entry():
    kernel, store, _, tools = await make_kernel([final_events()])
    run_id = next(iter(store.runs))
    run = store.runs[run_id]
    assistant = AssistantMessage(
        message_id="assistant-crash",
        content=[],
        tool_calls=[
            ToolCall(
                call_id="recover-call",
                tool_name="fake_agent_echo",
                arguments={"value": "recovered"},
            )
        ],
        finish_reason="tool_calls",
        usage=None,
        created_at=NOW,
    )
    resolved = kernel.tool_catalog.resolve(run, "fake_agent_echo")
    invocation = ToolInvocation(
        invocation_id="recover-call",
        run_id=run_id,
        expected_run_version=run.state_version,
        assistant_message_id=assistant.message_id,
        source_index=0,
        causation_id=assistant.message_id,
        idempotency_key="recovery-key",
        tool=resolved,
        arguments={"value": "recovered"},
        deadline_at=run.budget.deadline_at,
    )
    acceptance = await tools.accept(invocation)
    tools.accept_log.clear()
    batch = ToolCallBatch(
        assistant_message_id=assistant.message_id,
        entries=[
            ToolBatchEntry(
                call_id=invocation.invocation_id,
                assistant_message_id=assistant.message_id,
                source_index=0,
                tool_name="fake_agent_echo",
                state="accepted",
                invocation=invocation,
                acceptance=acceptance,
            )
        ],
    )
    store.runs[run_id] = run.model_copy(
        update={"transcript": [*run.transcript, assistant], "tool_batches": [batch]}
    )

    result = await kernel.run(run_id, signal=NeverCancelled())

    assert result.outcome == "final_answer"
    assert tools.accept_log == []
    assert tools.execute_log == ["recover-call"]
    assert result.run.tool_batches[0].results_flushed is True


@pytest.mark.asyncio
async def test_consecutive_tool_turns_have_distinct_turn_and_idempotency_keys():
    kernel, store, runtime, tools = await make_kernel(
        [
            tool_events(("call-1", "fake_agent_echo", '{"value":"one"}')),
            tool_events(("call-2", "fake_agent_echo", '{"value":"two"}')),
            final_events(),
        ]
    )

    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())

    assert result.outcome == "final_answer"
    assert tools.execute_log == ["call-1", "call-2"]
    invocations = [batch.entries[0].invocation for batch in result.run.tool_batches]
    assert invocations[0].idempotency_key != invocations[1].idempotency_key
    assert len({request.turn_id for request in runtime.requests}) == 3


@pytest.mark.asyncio
async def test_tool_budget_is_reserved_before_acceptance():
    run = make_run(max_agent_calls=1)
    kernel, store, _, tools = await make_kernel(
        [
            tool_events(
                ("first", "fake_agent_echo", '{"value":"one"}'),
                ("excess", "fake_agent_echo", '{"value":"two"}'),
            ),
            final_events(),
        ],
        run=run,
    )

    result = await kernel.run(run.run_id, signal=NeverCancelled())
    results = [
        item for item in result.run.transcript if isinstance(item, ToolResultMessage)
    ]

    assert tools.accept_log == ["first"]
    assert tools.execute_log == ["first"]
    assert result.run.budget.agent_calls_used == 1
    assert next(item for item in results if item.call_id == "excess").is_error


@pytest.mark.asyncio
async def test_wrap_up_tool_calls_are_rejected_and_flushed_before_grace_final():
    run = make_run(max_model_turns=1, grace_model_turns=2)
    kernel, _, runtime, tools = await make_kernel(
        [
            tool_events(("normal", "fake_agent_echo", '{"value":"first"}')),
            tool_events(("grace-tool", "fake_agent_echo", '{"value":"blocked"}')),
            final_events("wrapped up"),
        ],
        run=run,
    )

    result = await kernel.run(run.run_id, signal=NeverCancelled())

    assert result.outcome == "final_answer"
    assert len(runtime.requests) == 3
    assert runtime.requests[1].tools == []
    assert tools.execute_log == ["normal"]
    grace_batch = result.run.tool_batches[1]
    assert grace_batch.results_flushed is True
    assert grace_batch.entries[0].state == "terminal"
    assert grace_batch.entries[0].buffered_terminal_result.error_code == (
        "grace_tools_disabled"
    )


@pytest.mark.asyncio
async def test_deadline_during_tool_reservation_aborts_batch_without_side_effects():
    class DeadlineAtToolPolicy(BudgetPolicy):
        def before_tool_call(self, budget, profile, *, now):
            del budget, profile, now
            raise BudgetExceeded("deadline")

    kernel, store, _, tools = await make_kernel(
        [
            tool_events(
                ("first", "fake_agent_echo", '{"value":"one"}'),
                ("second", "fake_agent_echo", '{"value":"two"}'),
            )
        ]
    )
    kernel.budget_policy = DeadlineAtToolPolicy()

    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())

    assert result.outcome == "budget_exhausted"
    assert result.run.terminal_reason == "deadline"
    assert tools.accept_log == tools.execute_log == []
    assert all(
        entry.buffered_terminal_result is None
        for entry in result.run.tool_batches[0].entries
    )


@pytest.mark.asyncio
async def test_tool_budget_reservation_conflict_propagates_without_execution():
    class ReservationConflictStore(InMemoryOrchestratorRunStore):
        async def cas_mutate(self, run, *, expected_state_version, command_id):
            if command_id.startswith("tool-budget-reserve:"):
                current = self.runs[run.run_id]
                return InMemoryRunStoreResult("conflict", current)
            return await super().cas_mutate(
                run,
                expected_state_version=expected_state_version,
                command_id=command_id,
            )

    store = ReservationConflictStore()
    kernel, store, _, tools = await make_kernel(
        [tool_events(("call-1", "fake_agent_echo", '{"value":"ok"}'))],
        run_store=store,
    )

    with pytest.raises(KernelConflict, match="tool-budget-reserve"):
        await kernel.run(next(iter(store.runs)), signal=NeverCancelled())

    assert tools.accept_log == tools.execute_log == []
    stored = await store.load(next(iter(store.runs)))
    assert stored.tool_batches[0].entries[0].state == "pending"
    assert stored.tool_batches[0].entries[0].buffered_terminal_result is None


@pytest.mark.asyncio
async def test_usage_budget_exhaustion_prevents_tool_side_effects():
    run = make_run(max_output_tokens_total=1)
    events = [
        ModelStreamEvent(kind="attempt_started", attempt=1),
        ModelStreamEvent(
            kind="usage",
            attempt=1,
            usage=UsageRecord(input_tokens=1, output_tokens=2),
        ),
        *tool_events(("never", "fake_agent_echo", '{"value":"no"}'))[1:],
    ]
    kernel, store, _, tools = await make_kernel([events], run=run)

    result = await kernel.run(run.run_id, signal=NeverCancelled())

    assert result.outcome == "budget_exhausted"
    assert result.run.budget.output_tokens == 2
    assert tools.accept_log == tools.execute_log == []


@pytest.mark.asyncio
async def test_retry_without_usage_is_durably_counted_once():
    retry_then_final = [
        ModelStreamEvent(kind="attempt_started", attempt=1),
        ModelStreamEvent(
            kind="attempt_failed",
            attempt=1,
            error_class="timeout",
            retryable=True,
        ),
        ModelStreamEvent(
            kind="retry_scheduled",
            attempt=2,
            error_class="timeout",
            retryable=True,
        ),
        ModelStreamEvent(kind="attempt_started", attempt=2),
        ModelStreamEvent(kind="finish", attempt=2, finish_reason="stop"),
    ]
    kernel, store, _, _ = await make_kernel([retry_then_final])

    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())

    assert result.run.budget.provider_retries_used == 1
    assert result.run.budget.model_turns_used == 1
    assert len(result.run.budget.provider_attempt_keys) == 2


@pytest.mark.asyncio
async def test_late_or_mismatched_observations_cannot_reopen_terminal_run():
    kernel, store, _, _ = await make_kernel(
        [
            tool_events(("wait", "fake_agent_pause", '{"status":"waiting_external"}')),
            final_events(),
        ]
    )
    run_id = next(iter(store.runs))
    await kernel.run(run_id, signal=NeverCancelled())
    with pytest.raises(ValueError, match="does not correlate"):
        await kernel.observe_tool(
            run_id,
            ToolObservation(
                observation_id="bad-suspension",
                invocation_id="wait",
                outcome=ToolSuspension(
                    invocation_id="other", status="waiting_external"
                ),
                observed_at=NOW,
            ),
            signal=NeverCancelled(),
        )
    completion = ToolObservation(
        observation_id="complete",
        invocation_id="wait",
        outcome=ToolResult(
            call_id="wait",
            tool_name="fake_agent_pause",
            status="completed",
            content=[TextPart(text="done")],
            artifact_refs=[],
        ),
        observed_at=NOW,
    )
    completed = await kernel.observe_tool(run_id, completion, signal=NeverCancelled())
    with pytest.raises(KernelConflict, match="terminal Run"):
        await kernel.observe_tool(
            run_id,
            ToolObservation(
                observation_id="late",
                invocation_id="wait",
                outcome=ToolSuspension(invocation_id="wait", status="input_required"),
                observed_at=NOW,
            ),
            signal=NeverCancelled(),
        )
    with pytest.raises(KeyError):
        await kernel.observe_tool(
            run_id,
            ToolObservation(
                observation_id="fabricated",
                invocation_id="other-run-call",
                outcome=ToolSuspension(
                    invocation_id="other-run-call", status="input_required"
                ),
                observed_at=NOW,
            ),
            signal=NeverCancelled(),
        )

    assert completed.outcome == "final_answer"
    assert completed.run.status == "completed"


@pytest.mark.asyncio
async def test_fake_tools_cover_failure_delays_and_cancellation():
    kernel, store, _, tools = await make_kernel(
        [
            tool_events(
                ("fail", "fake_agent_fail", '{"message":"expected"}'),
                ("parallel", "fake_agent_delay_parallel", '{"seconds":0}'),
                ("sequential", "fake_agent_delay_sequential", '{"seconds":0}'),
            ),
            final_events(),
        ]
    )
    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())
    messages = [
        item for item in result.run.transcript if isinstance(item, ToolResultMessage)
    ]
    assert [item.status for item in messages] == ["failed", "completed", "completed"]
    assert tools.execute_log == ["fail", "parallel", "sequential"]

    invocation = result.run.tool_batches[0].entries[1].invocation
    acceptance = result.run.tool_batches[0].entries[1].acceptance
    with pytest.raises(ValueError, match="missing or mismatched"):
        await RecordingFakeToolRuntime().execute(
            invocation, acceptance, signal=NeverCancelled()
        )

    signal = EventCancellationSignal()
    # Runtime-level cancellation remains independent of cached replay outcomes.
    fresh_tools = RecordingFakeToolRuntime()
    cancel_invocation = invocation.model_copy(
        update={"invocation_id": "cancel-call", "idempotency_key": "cancel-key"}
    )
    cancel_acceptance = await fresh_tools.accept(cancel_invocation)
    signal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fresh_tools.execute(cancel_invocation, cancel_acceptance, signal=signal)


@pytest.mark.asyncio
async def test_terminal_tool_artifacts_merge_atomically_into_run_inventory():
    class ArtifactRuntime(RecordingFakeToolRuntime):
        async def execute(self, invocation, acceptance, *, signal):
            await super().execute(invocation, acceptance, signal=signal)
            return ToolResult(
                call_id=invocation.invocation_id,
                tool_name=invocation.tool.definition.name,
                status="completed",
                content=[],
                artifact_refs=["artifact-1"],
            )

    kernel, store, _, _ = await make_kernel(
        [
            tool_events(("artifact-call", "fake_agent_echo", '{"value":"ok"}')),
            final_events(),
        ],
        tool_runtime=ArtifactRuntime(),
    )
    result = await kernel.run(next(iter(store.runs)), signal=NeverCancelled())
    assert result.run.artifact_refs == ["artifact-1"]
    assert result.run.tool_batches[0].entries[
        0
    ].buffered_terminal_result.artifact_refs == ["artifact-1"]


@pytest.mark.asyncio
async def test_observed_terminal_tool_artifacts_merge_with_observation_checkpoint():
    kernel, store, _, _ = await make_kernel(
        [
            tool_events(
                (
                    "wait",
                    "fake_agent_pause",
                    '{"status":"waiting_external"}',
                )
            ),
            final_events(),
        ]
    )
    run_id = next(iter(store.runs))
    waiting = await kernel.run(run_id, signal=NeverCancelled())
    completed = await kernel.observe_tool(
        run_id,
        ToolObservation(
            observation_id="artifact-observation",
            invocation_id="wait",
            outcome=ToolResult(
                call_id="wait",
                tool_name="fake_agent_pause",
                status="completed",
                content=[],
                artifact_refs=["artifact-observed"],
            ),
            observed_at=NOW,
        ),
        signal=NeverCancelled(),
    )
    assert waiting.outcome == "waiting_external"
    assert completed.run.artifact_refs == ["artifact-observed"]
