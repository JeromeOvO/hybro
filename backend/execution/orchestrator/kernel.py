"""Provider- and transport-neutral bounded agent loop."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal

from jsonschema import Draft202012Validator

from .budget import BudgetExceeded, BudgetPolicy
from .context import ContextCompiler, UnresolvedToolBatchError
from .models import (
    ArtifactRefPart,
    AssistantMessage,
    DataPart,
    OrchestratorRunState,
    SessionNotice,
    TextPart,
    ToolAcceptance,
    ToolBatchEntry,
    ToolCall,
    ToolCallBatch,
    ToolInvocation,
    ToolObservation,
    ToolResult,
    ToolResultMessage,
    ToolSuspension,
)
from .ports import (
    CancellationSignal,
    ContextCompactor,
    IDFactory,
    ModelRuntime,
    OrchestratorRunStore,
    ProjectionDriver,
    ToolCatalog,
    ToolRuntime,
)
from .settlement import (
    TerminalCommitRequest,
    TerminalDecisionFacts,
    TerminalStatusCommitRequest,
    commit_terminal_decision,
    commit_terminal_status,
)
from .streaming import ModelStreamAssembler, ModelStreamAssemblyError
from .tools import validate_tool_result_correlation
from .transcript import unresolved_call_ids

KernelLifecycle = Callable[
    [str, OrchestratorRunState, dict[str, object]], Awaitable[None]
]

KernelOutcome = Literal[
    "final_answer",
    "waiting_external",
    "awaiting_user",
    "budget_exhausted",
    "aborted",
    "failed",
]


class KernelConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KernelRunResult:
    outcome: KernelOutcome
    run: OrchestratorRunState


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UUIDFactory:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


def _task_text(arguments: object) -> str:
    if isinstance(arguments, dict):
        task = arguments.get("task")
        if isinstance(task, str) and task.strip():
            return task.strip()[:4000]
    return ""


def _result_text(result: ToolResult) -> str:
    parts: list[str] = []
    for part in result.content:
        if isinstance(part, TextPart):
            if part.text:
                parts.append(part.text)
        elif isinstance(part, DataPart):
            parts.append(
                json.dumps(part.data, ensure_ascii=False, separators=(",", ":"))
            )
        elif isinstance(part, ArtifactRefPart):
            parts.append(f"[artifact reference: {part.artifact_ref}]")
    return "\n".join(parts)[:8000]


class OrchestratorKernel:
    def __init__(
        self,
        *,
        run_store: OrchestratorRunStore,
        model_runtime: ModelRuntime,
        tool_runtime: ToolRuntime,
        tool_catalog: ToolCatalog,
        context_compiler: ContextCompiler,
        budget_policy: BudgetPolicy,
        projection_driver: ProjectionDriver,
        clock: SystemClock | None = None,
        id_factory: IDFactory | None = None,
        context_compactor: ContextCompactor | None = None,
    ) -> None:
        self.run_store = run_store
        self.model_runtime = model_runtime
        self.tool_runtime = tool_runtime
        self.tool_catalog = tool_catalog
        self.context_compiler = context_compiler
        self.budget_policy = budget_policy
        self.projection_driver = projection_driver
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or UUIDFactory()
        self.context_compactor = context_compactor

    async def run(
        self,
        run_id: str,
        *,
        signal: CancellationSignal,
        lifecycle: KernelLifecycle | None = None,
    ) -> KernelRunResult:
        invalid_observations = 0
        while True:
            run = await self._load(run_id)
            if run.status in {"completed", "failed", "canceled", "budget_exhausted"}:
                return KernelRunResult(_outcome_for_status(run.status), run)
            if run.status == "finalizing":
                assistant = _finalization_candidate(run)
                if assistant is None:
                    return await self._terminate(
                        run, status="failed", reason="finalization candidate missing"
                    )
                return await self._complete(run, assistant)
            if signal.cancelled:
                return await self._terminate(
                    run, status="canceled", reason="cancellation requested"
                )
            if run.status == "waiting_external":
                return KernelRunResult("waiting_external", run)
            if run.status == "awaiting_user":
                return KernelRunResult("awaiting_user", run)
            unflushed = next(
                (batch for batch in run.tool_batches if not batch.results_flushed),
                None,
            )
            if unflushed is not None:
                assistant = next(
                    (
                        item
                        for item in run.transcript
                        if isinstance(item, AssistantMessage)
                        and item.message_id == unflushed.assistant_message_id
                    ),
                    None,
                )
                if assistant is None:
                    return await self._terminate(
                        run, status="failed", reason="tool batch assistant missing"
                    )
                recovered = await self._execute_tool_batch(
                    run, assistant, signal, lifecycle=lifecycle
                )
                if recovered is not None:
                    return recovered
                continue
            torn_assistant = _assistant_missing_tool_batch(run)
            if torn_assistant is not None:
                try:
                    await self._ensure_tool_batch(run, torn_assistant)
                except KernelConflict:
                    return await self._terminate(
                        await self._load(run_id),
                        status="failed",
                        reason="unresolved tool batch could not be recovered",
                    )
                continue

            await self._emit(lifecycle, "turn_started", run, {})
            grace = run.budget.model_turns_used >= run.profile.max_model_turns
            if grace and not run.budget.wrap_up_requested:
                budget = self.budget_policy.request_wrap_up(run.budget)
                notice = SessionNotice(
                    notice_id=self._stable_id(run, "wrap_up", 0),
                    code="wrap_up",
                    content="Tools are disabled. Produce the best final answer now.",
                    created_at=self.clock.now(),
                )
                run = await self._checkpoint(
                    run,
                    updates={
                        "budget": budget,
                        "transcript": [*run.transcript, notice],
                    },
                    command_id=f"wrap-up:{run.run_id}",
                )
            try:
                self.budget_policy.before_model_turn(
                    run.budget,
                    run.profile,
                    now=self.clock.now(),
                )
            except BudgetExceeded as exc:
                return await self._terminate(
                    run, status="budget_exhausted", reason=exc.reason
                )

            tools = (
                []
                if run.budget.wrap_up_requested
                else self.tool_catalog.list_tools(run)
            )
            try:
                compiled = self.context_compiler.compile(
                    run, tools=tools, summary=run.compaction_summary
                )
            except UnresolvedToolBatchError:
                return await self._terminate(
                    run, status="failed", reason="unresolved tool batch"
                )
            if compiled.kind == "context_unfit":
                return await self._terminate(
                    run, status="failed", reason="context_unfit"
                )
            if compiled.kind == "needs_compaction":
                compacted = await self._compact(
                    run,
                    compiled.messages,
                    baseline=compiled.estimated_input_tokens,
                    signal=signal,
                )
                if isinstance(compacted, KernelRunResult):
                    return compacted
                continue
            if (
                run.compaction_baseline_tokens is not None
                and compiled.estimated_input_tokens >= run.compaction_baseline_tokens
            ):
                return await self._terminate(
                    run,
                    status="budget_exhausted",
                    reason="compaction did not reduce context",
                )
            if run.compaction_baseline_tokens is not None:
                run = await self._checkpoint(
                    run,
                    updates={"compaction_baseline_tokens": None},
                    command_id=(
                        f"compaction-validated:{run.run_id}:"
                        f"{run.budget.compactions_used}"
                    ),
                )

            request = self._model_request(run, compiled.messages, tools)
            assembler = ModelStreamAssembler()
            try:
                async for event in self.model_runtime.stream_turn(
                    request, signal=signal
                ):
                    assembler.accept(event)
                    run = await self._record_model_event(run, request.turn_id, event)
                    await self._emit_model_event(lifecycle, run, event)
                model_outcome = assembler.build_outcome(
                    message_id=self.id_factory.new_id("assistant"),
                    created_at=self.clock.now(),
                )
            except BudgetExceeded as exc:
                return await self._terminate(
                    run, status="budget_exhausted", reason=exc.reason
                )
            except ModelStreamAssemblyError as exc:
                notice = self._assembly_notice(run, exc)
                run = await self._append_notice(run, notice)
                invalid_observations += 1
                if invalid_observations > run.profile.grace_model_turns + 1:
                    return await self._terminate(
                        run, status="failed", reason="invalid model output loop"
                    )
                continue

            if model_outcome.kind == "aborted":
                return await self._terminate(
                    run, status="canceled", reason="model request aborted"
                )
            if model_outcome.kind == "context_overflow":
                if (
                    self.context_compactor is None
                    or run.budget.compactions_used >= run.profile.max_compactions
                ):
                    return await self._terminate(
                        run, status="budget_exhausted", reason="context overflow"
                    )
                compacted = await self._compact(
                    run,
                    list(compiled.messages),
                    baseline=compiled.estimated_input_tokens,
                    signal=signal,
                )
                if isinstance(compacted, KernelRunResult):
                    return compacted
                continue
            if model_outcome.kind == "provider_error":
                notice = SessionNotice(
                    notice_id=self._stable_id(
                        run,
                        model_outcome.error_class or "provider_error",
                        run.budget.model_turns_used,
                    ),
                    code=model_outcome.error_class or "provider_error",
                    content="The previous model attempt failed; retry within bounds.",
                    created_at=self.clock.now(),
                )
                run = await self._append_notice(run, notice)
                invalid_observations += 1
                if invalid_observations > run.profile.grace_model_turns + 1:
                    return await self._terminate(
                        run, status="failed", reason="provider error loop"
                    )
                continue
            assistant = model_outcome.assistant
            if assistant is None:
                return await self._terminate(
                    run, status="failed", reason="missing assistant outcome"
                )
            run = await self._checkpoint(
                run,
                updates={
                    "budget": self.budget_policy.record_assistant_turn(
                        run.budget, grace=grace
                    )
                },
                command_id=f"assistant-turn:{request.turn_id}",
            )
            run = await self._append_assistant(run, assistant)
            await self._emit(
                lifecycle,
                "message_completed",
                run,
                {"message_id": assistant.message_id},
            )
            if not assistant.tool_calls:
                await self._emit(
                    lifecycle,
                    "turn_completed",
                    run,
                    {"message_id": assistant.message_id},
                )
                return await self._complete(run, assistant)
            if run.budget.wrap_up_requested:
                run = await self._reject_grace_tools(run, assistant)
                continue
            try:
                result = await self._execute_tool_batch(
                    run, assistant, signal, lifecycle=lifecycle
                )
            except asyncio.CancelledError:
                if signal.cancelled:
                    current = await self._load(run.run_id)
                    return await self._terminate(
                        current, status="canceled", reason="tool execution canceled"
                    )
                raise
            if result is not None:
                return result
            await self._emit(
                lifecycle,
                "turn_completed",
                await self._load(run.run_id),
                {"message_id": assistant.message_id},
            )

    async def observe_tool(
        self,
        run_id: str,
        observation: ToolObservation,
        *,
        signal: CancellationSignal,
        lifecycle: KernelLifecycle | None = None,
    ) -> KernelRunResult:
        run = await self._load(run_id)
        batch_index, entry_index = _find_invocation(run, observation.invocation_id)
        if batch_index is None or entry_index is None:
            raise KeyError(observation.invocation_id)
        entry = run.tool_batches[batch_index].entries[entry_index]
        if entry.invocation is None:
            raise KernelConflict("tool observation target has no invocation")
        if observation.invocation_id != entry.invocation.invocation_id:
            raise ValueError("observation invocation does not correlate")
        if isinstance(observation.outcome, ToolResult):
            if (
                observation.outcome.call_id != entry.call_id
                or observation.outcome.tool_name != entry.tool_name
            ):
                raise ValueError("observation result does not correlate")
        elif observation.outcome.invocation_id != entry.invocation.invocation_id:
            raise ValueError("observation suspension does not correlate")
        if run.status in {"completed", "failed", "canceled", "budget_exhausted"}:
            if observation.observation_id in entry.processed_observation_ids:
                return KernelRunResult(_outcome_for_status(run.status), run)
            raise KernelConflict("terminal Run cannot accept a new tool observation")
        if run.status not in {"waiting_external", "awaiting_user"}:
            raise KernelConflict("tool observations require a suspended Run")
        if entry.state not in {"waiting_external", "input_required", "auth_required"}:
            raise KernelConflict("tool observation target is not suspended")
        if observation.observation_id in entry.processed_observation_ids:
            return KernelRunResult(
                "waiting_external"
                if run.status == "waiting_external"
                else "awaiting_user",
                run,
            )
        batches = list(run.tool_batches)
        batch = batches[batch_index]
        entries = list(batch.entries)
        if isinstance(observation.outcome, ToolResult):
            state = "terminal"
            result = observation.outcome
        else:
            state = observation.outcome.status
            result = entry.buffered_terminal_result
        entries[entry_index] = entry.model_copy(
            update={
                "state": state,
                "buffered_terminal_result": result,
                "processed_observation_ids": [
                    *entry.processed_observation_ids,
                    observation.observation_id,
                ],
            }
        )
        batch = batch.model_copy(update={"entries": entries})
        batches[batch_index] = batch
        all_terminal = all(item.state == "terminal" for item in batch.entries)
        updates: dict[str, object] = {"tool_batches": batches}
        if isinstance(observation.outcome, ToolResult):
            updates["artifact_refs"] = _merge_artifact_refs(
                run.artifact_refs, [observation.outcome]
            )
        if all_terminal:
            transcript, batch = _flush_batch(run.transcript, batch, self.clock.now())
            batches[batch_index] = batch
            updates.update(
                tool_batches=batches,
                transcript=transcript,
                status="running",
            )
        else:
            updates["status"] = _wait_status(batch)
        run = await self._checkpoint(
            run,
            updates=updates,
            command_id=f"tool-observation:{observation.observation_id}",
        )
        if not all_terminal:
            return KernelRunResult(
                "awaiting_user"
                if run.status == "awaiting_user"
                else "waiting_external",
                run,
            )
        for item in batch.entries:
            await self._emit(
                lifecycle,
                "message_completed",
                run,
                {
                    "call_id": item.call_id,
                    "message_kind": "tool_result",
                    "agent_label": self._tool_label(run, result.tool_name)
                    if (result := item.buffered_terminal_result) is not None
                    else None,
                    "binding_id": self._tool_binding_id(run, result.tool_name)
                    if (result := item.buffered_terminal_result) is not None
                    else None,
                    "result_text": _result_text(result)
                    if (result := item.buffered_terminal_result) is not None
                    else None,
                    "result_status": result.status
                    if (result := item.buffered_terminal_result) is not None
                    else None,
                },
            )
        return await self.run(run_id, signal=signal, lifecycle=lifecycle)

    @staticmethod
    def _tool_binding_id(run: OrchestratorRunState, tool_name: str) -> str | None:
        """Resolve the frozen binding id for a tool name."""
        if run.tool_catalog is None:
            return None
        for entry in run.tool_catalog.entries:
            if entry.definition.name == tool_name:
                return entry.binding.binding_id
        return None

    @staticmethod
    def _tool_label(run: OrchestratorRunState, tool_name: str) -> str | None:
        """Resolve the user-facing agent label for a tool name."""
        if run.tool_catalog is None:
            return None
        for entry in run.tool_catalog.entries:
            if entry.definition.name == tool_name:
                label = entry.definition.label.strip()
                return label or None
        return None

    async def _ensure_tool_batch(
        self, run: OrchestratorRunState, assistant: AssistantMessage
    ) -> OrchestratorRunState:
        existing = next(
            (
                batch
                for batch in run.tool_batches
                if batch.assistant_message_id == assistant.message_id
            ),
            None,
        )
        if existing is not None:
            return run
        call_ids = {call.call_id for call in assistant.tool_calls}
        if not call_ids or not call_ids.issubset(unresolved_call_ids(run.transcript)):
            raise KernelConflict("tool batch reconstruction is inconsistent")
        return await self._checkpoint(
            run,
            updates={"tool_batches": [*run.tool_batches, _new_tool_batch(assistant)]},
            command_id=f"reconstruct-tool-batch:{assistant.message_id}",
        )

    async def _execute_tool_batch(
        self,
        run: OrchestratorRunState,
        assistant: AssistantMessage,
        signal: CancellationSignal,
        *,
        lifecycle: KernelLifecycle | None = None,
    ) -> KernelRunResult | None:
        batch_index = next(
            (
                index
                for index, item in enumerate(run.tool_batches)
                if item.assistant_message_id == assistant.message_id
                and not item.results_flushed
            ),
            None,
        )
        if batch_index is None:
            run = await self._ensure_tool_batch(run, assistant)
            batch_index = next(
                index
                for index, item in enumerate(run.tool_batches)
                if item.assistant_message_id == assistant.message_id
                and not item.results_flushed
            )
        executable: list[tuple[ToolCall, ToolInvocation, ToolAcceptance]] = []
        for call in assistant.tool_calls:
            run = await self._load(run.run_id)
            batch = run.tool_batches[batch_index]
            entry_index = next(
                index
                for index, entry in enumerate(batch.entries)
                if entry.call_id == call.call_id
            )
            entry = batch.entries[entry_index]
            if entry.state in {
                "terminal",
                "waiting_external",
                "input_required",
                "auth_required",
            }:
                continue
            if entry.state in {"accepted", "executing"}:
                if entry.invocation is None or entry.acceptance is None:
                    raise KernelConflict("accepted tool entry is incomplete")
                executable.append((call, entry.invocation, entry.acceptance))
                continue
            if entry.state != "pending":
                raise KernelConflict(
                    f"unsupported recoverable tool state {entry.state}"
                )
            try:
                resolved = self.tool_catalog.resolve(run, call.tool_name)
                errors = list(
                    Draft202012Validator(resolved.definition.input_schema).iter_errors(
                        call.arguments
                    )
                )
                if errors:
                    raise ValueError("tool arguments failed schema validation")
            except Exception as exc:
                run = await self._update_entry(
                    run,
                    batch_index,
                    entry_index,
                    state="terminal",
                    result=_tool_error(call, "invalid_tool_call", str(exc)),
                    command=f"invalid-tool:{call.call_id}",
                )
                continue
            try:
                self.budget_policy.before_tool_call(
                    run.budget, run.profile, now=self.clock.now()
                )
            except BudgetExceeded as exc:
                if exc.reason != "tool_calls":
                    return await self._terminate(
                        run, status="budget_exhausted", reason=exc.reason
                    )
                run = await self._update_entry(
                    run,
                    batch_index,
                    entry_index,
                    state="terminal",
                    result=_tool_error(call, "tool_call_budget_exhausted", exc.reason),
                    command=f"tool-budget-exhausted:{call.call_id}",
                )
                continue
            run = await self._checkpoint(
                run,
                updates={"budget": self.budget_policy.record_tool_calls(run.budget, 1)},
                command_id=(
                    f"tool-budget-reserve:{assistant.message_id}:{call.call_id}"
                ),
            )
            invocation = ToolInvocation(
                invocation_id=call.call_id,
                run_id=run.run_id,
                expected_run_version=run.state_version,
                assistant_message_id=assistant.message_id,
                source_index=entry_index,
                causation_id=assistant.message_id,
                idempotency_key=self._stable_id(
                    run,
                    "tool",
                    assistant.message_id,
                    call.call_id,
                    entry_index,
                    resolved.binding.binding_digest,
                ),
                tool=resolved,
                arguments=call.arguments,
                deadline_at=run.budget.deadline_at,
            )
            try:
                acceptance = await self.tool_runtime.accept(invocation)
                if (
                    acceptance.invocation_id != invocation.invocation_id
                    or acceptance.idempotency_key != invocation.idempotency_key
                ):
                    raise ValueError("tool acceptance does not correlate")
            except Exception as exc:
                run = await self._update_entry(
                    run,
                    batch_index,
                    entry_index,
                    state="terminal",
                    invocation=invocation,
                    result=_tool_error(call, "acceptance_failed", str(exc)),
                    command=f"acceptance-failed:{call.call_id}",
                )
                continue
            run = await self._update_entry(
                run,
                batch_index,
                entry_index,
                state="accepted",
                invocation=invocation,
                acceptance=acceptance,
                command=f"accepted-tool:{call.call_id}",
            )
            executable.append((call, invocation, acceptance))

        sequential = run.profile.tool_execution == "sequential" or any(
            item[1].tool.definition.execution_mode == "sequential"
            for item in executable
        )
        outcomes: list[tuple[str, ToolResult | ToolSuspension]] = []
        if sequential:
            for call, invocation, acceptance in executable:
                await self._emit(
                    lifecycle,
                    "tool_execution_started",
                    run,
                    {
                        "call_id": call.call_id,
                        "tool_name": call.tool_name,
                        "agent_label": self._tool_label(run, call.tool_name),
                    },
                )
                outcome = await self._execute_one(invocation, acceptance, signal=signal)
                await self._emit(
                    lifecycle,
                    "tool_execution_completed",
                    run,
                    {"call_id": call.call_id, "status": outcome.status},
                )
                outcomes.append((call.call_id, outcome))
        else:
            semaphore = asyncio.Semaphore(run.profile.max_parallel_calls)

            async def execute_bounded(
                call: ToolCall,
                invocation: ToolInvocation,
                acceptance: ToolAcceptance,
            ) -> ToolResult | ToolSuspension:
                async with semaphore:
                    await self._emit(
                        lifecycle,
                        "tool_execution_started",
                        run,
                        {"call_id": call.call_id, "tool_name": call.tool_name},
                    )
                    outcome = await self._execute_one(
                        invocation, acceptance, signal=signal
                    )
                    await self._emit(
                        lifecycle,
                        "tool_execution_completed",
                        run,
                        {"call_id": call.call_id, "status": outcome.status},
                    )
                    return outcome

            values = await asyncio.gather(
                *(
                    execute_bounded(call, invocation, acceptance)
                    for call, invocation, acceptance in executable
                )
            )
            outcomes = [
                (call.call_id, outcome)
                for (call, _, _), outcome in zip(executable, values, strict=True)
            ]

        run = await self._load(run.run_id)
        batch = run.tool_batches[batch_index]
        entries = list(batch.entries)
        for call_id, outcome in outcomes:
            index = next(i for i, item in enumerate(entries) if item.call_id == call_id)
            entry = entries[index]
            if isinstance(outcome, ToolResult):
                call = next(
                    item for item in assistant.tool_calls if item.call_id == call_id
                )
                validate_tool_result_correlation(call, outcome)
                entries[index] = entry.model_copy(
                    update={"state": "terminal", "buffered_terminal_result": outcome}
                )
            else:
                entries[index] = entry.model_copy(update={"state": outcome.status})
        batch = batch.model_copy(update={"entries": entries})
        batches = list(run.tool_batches)
        batches[batch_index] = batch
        artifact_refs = _merge_artifact_refs(
            run.artifact_refs,
            [outcome for _, outcome in outcomes if isinstance(outcome, ToolResult)],
        )
        if all(entry.state == "terminal" for entry in entries):
            transcript, batch = _flush_batch(run.transcript, batch, self.clock.now())
            batches[batch_index] = batch
            run = await self._checkpoint(
                run,
                updates={
                    "tool_batches": batches,
                    "transcript": transcript,
                    "artifact_refs": artifact_refs,
                },
                command_id=f"complete-tool-batch:{assistant.message_id}",
            )
            for entry in batch.entries:
                await self._emit(
                    lifecycle,
                    "message_completed",
                    run,
                    {
                        "call_id": entry.call_id,
                        "message_kind": "tool_result",
                        "agent_label": self._tool_label(
                            run, entry.buffered_terminal_result.tool_name
                        )
                        if entry.buffered_terminal_result is not None
                        else None,
                    },
                )
            return None
        status = _wait_status(batch)
        run = await self._checkpoint(
            run,
            updates={
                "tool_batches": batches,
                "status": status,
                "artifact_refs": artifact_refs,
            },
            command_id=f"suspend-tool-batch:{assistant.message_id}",
        )
        await self._emit(
            lifecycle,
            "turn_completed",
            run,
            {"message_id": assistant.message_id, "status": status},
        )
        return KernelRunResult(
            "awaiting_user" if status == "awaiting_user" else "waiting_external",
            run,
        )

    async def _execute_one(
        self,
        invocation: ToolInvocation,
        acceptance: ToolAcceptance,
        *,
        signal: CancellationSignal,
    ) -> ToolResult | ToolSuspension:
        if (
            acceptance.invocation_id != invocation.invocation_id
            or acceptance.idempotency_key != invocation.idempotency_key
        ):
            return ToolResult(
                call_id=invocation.invocation_id,
                tool_name=invocation.tool.definition.name,
                status="rejected",
                content=[],
                artifact_refs=[],
                error_code="acceptance_mismatch",
                error_message="tool acceptance does not correlate",
            )
        try:
            return await self.tool_runtime.execute(
                invocation, acceptance, signal=signal
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ToolResult(
                call_id=invocation.invocation_id,
                tool_name=invocation.tool.definition.name,
                status="failed",
                content=[],
                artifact_refs=[],
                error_code="tool_execution_failed",
                error_message=str(exc)[:500],
            )

    async def _reject_grace_tools(
        self, run: OrchestratorRunState, assistant: AssistantMessage
    ) -> OrchestratorRunState:
        batch_index = next(
            (
                index
                for index, batch in enumerate(run.tool_batches)
                if batch.assistant_message_id == assistant.message_id
                and not batch.results_flushed
            ),
            None,
        )
        if batch_index is None:
            raise KernelConflict("wrap-up tool batch is missing")
        batches = list(run.tool_batches)
        batch = batches[batch_index]
        calls = {call.call_id: call for call in assistant.tool_calls}
        entries = []
        for entry in batch.entries:
            call = calls.get(entry.call_id)
            if call is None:
                raise KernelConflict("wrap-up tool batch does not correlate")
            entries.append(
                entry.model_copy(
                    update={
                        "state": "terminal",
                        "buffered_terminal_result": _tool_error(
                            call,
                            "grace_tools_disabled",
                            "Tools are disabled during wrap-up.",
                        ),
                    }
                )
            )
        batch = batch.model_copy(update={"entries": entries})
        transcript, batch = _flush_batch(run.transcript, batch, self.clock.now())
        batches[batch_index] = batch
        return await self._checkpoint(
            run,
            updates={"transcript": transcript, "tool_batches": batches},
            command_id=f"reject-grace-tools:{assistant.message_id}",
        )

    async def _append_assistant(
        self, run: OrchestratorRunState, assistant: AssistantMessage
    ) -> OrchestratorRunState:
        updates: dict[str, object] = {"transcript": [*run.transcript, assistant]}
        if assistant.tool_calls:
            updates["tool_batches"] = [
                *run.tool_batches,
                _new_tool_batch(assistant),
            ]
        else:
            updates.update(
                proposed_final_message_id=assistant.message_id,
                status="finalizing",
            )
        return await self._checkpoint(
            run,
            updates=updates,
            command_id=f"assistant:{assistant.message_id}",
        )

    async def _append_notice(
        self, run: OrchestratorRunState, notice: SessionNotice
    ) -> OrchestratorRunState:
        if any(
            isinstance(message, SessionNotice) and message.notice_id == notice.notice_id
            for message in run.transcript
        ):
            return run
        return await self._checkpoint(
            run,
            updates={"transcript": [*run.transcript, notice]},
            command_id=f"notice:{notice.notice_id}",
        )

    async def _record_model_event(
        self,
        run: OrchestratorRunState,
        turn_id: str,
        event,
    ) -> OrchestratorRunState:
        attempt = event.attempt or 1
        attempt_key = f"{turn_id}:{attempt}"
        if event.kind == "attempt_started":
            budget = self.budget_policy.record_provider_attempt(
                run.budget,
                run.profile,
                attempt_key=attempt_key,
                retry=attempt > 1,
            )
            return await self._checkpoint(
                run,
                updates={"budget": budget},
                command_id=f"provider-attempt:{attempt_key}",
            )
        if event.kind == "usage" and event.usage is not None:
            budget = self.budget_policy.record_usage_snapshot(
                run.budget,
                attempt_key=attempt_key,
                usage=event.usage,
            )
            usage_key = (
                f"{event.usage.input_tokens}:{event.usage.output_tokens}:"
                f"{event.usage.cache_read_tokens}:{event.usage.cache_write_tokens}"
            )
            run = await self._checkpoint(
                run,
                updates={"budget": budget},
                command_id=f"provider-usage:{attempt_key}:{usage_key}",
            )
            self.budget_policy.before_token_side_effect(run.budget, run.profile)
        return run

    async def _compact(
        self,
        run: OrchestratorRunState,
        messages: list[object],
        *,
        baseline: int,
        signal: CancellationSignal,
    ) -> OrchestratorRunState | KernelRunResult:
        if (
            self.context_compactor is None
            or run.budget.compactions_used >= run.profile.max_compactions
        ):
            return await self._terminate(
                run, status="budget_exhausted", reason="context overflow"
            )
        try:
            self.budget_policy.before_model_turn(
                run.budget,
                run.profile,
                now=self.clock.now(),
                purpose="compaction",
            )
            turn_id = self._stable_id(
                run, "compaction", run.budget.compactions_used + 1
            )

            async def record_event(event) -> None:
                nonlocal run
                run = await self._record_model_event(run, turn_id, event)

            compaction = await self.context_compactor.compact(
                messages,
                turn_id=turn_id,
                remaining_provider_retries=(
                    self.budget_policy.remaining_provider_retries(
                        run.budget, run.profile
                    )
                ),
                deadline_at=run.budget.deadline_at,
                on_event=record_event,
                signal=signal,
            )
            summary = compaction.summary
            budget = self.budget_policy.record_compaction(run.budget, run.profile)
        except BudgetExceeded as exc:
            return await self._terminate(
                run, status="budget_exhausted", reason=exc.reason
            )
        except Exception:
            summary = "Older completed turns omitted; preserve recent context."
            budget = self.budget_policy.record_compaction(run.budget, run.profile)
        run = await self._checkpoint(
            run,
            updates={
                "budget": budget,
                "compaction_summary": summary,
                "compaction_baseline_tokens": baseline,
            },
            command_id=(f"compaction:{run.run_id}:{run.budget.compactions_used + 1}"),
        )
        return run

    async def _emit(
        self,
        lifecycle: KernelLifecycle | None,
        event_type: str,
        run: OrchestratorRunState,
        payload: dict[str, object],
    ) -> None:
        if lifecycle is not None:
            await lifecycle(event_type, run, payload)

    async def _emit_model_event(
        self,
        lifecycle: KernelLifecycle | None,
        run: OrchestratorRunState,
        event,
    ) -> None:
        event_type = {
            "attempt_started": "model_attempt_started",
            "retry_scheduled": "model_retry_scheduled",
            "attempt_failed": "model_attempt_failed",
        }.get(event.kind)
        if event_type is not None:
            await self._emit(
                lifecycle,
                event_type,
                run,
                {
                    "attempt": event.attempt or 0,
                    "error_class": event.error_class or "",
                },
            )

    async def _complete(
        self, run: OrchestratorRunState, assistant: AssistantMessage
    ) -> KernelRunResult:
        sequence = (
            max((item.event_sequence for item in run.projection_outbox), default=0) + 1
        )
        request = TerminalCommitRequest(
            expected_state_version=run.state_version,
            command_id=f"complete:{run.run_id}:{assistant.message_id}",
            event_id=self.id_factory.new_id("event"),
            event_sequence=sequence,
            event_intent_id=self.id_factory.new_id("intent-event"),
            final_message_intent_id=self.id_factory.new_id("intent-message"),
            public_run_intent_id=self.id_factory.new_id("intent-run"),
            final_message_target=run.room_id,
            public_run_target=run.run_id,
            created_at=self.clock.now(),
        )
        committed = commit_terminal_decision(
            run,
            facts=TerminalDecisionFacts(final_message_id=assistant.message_id),
            request=request,
        )
        if committed.outcome != "accepted":
            return await self._terminate(
                run, status="failed", reason=committed.evaluation.reason
            )
        stored = await self.run_store.cas_mutate(
            committed.run,
            expected_state_version=run.state_version,
            command_id=request.command_id,
        )
        if stored.run is None:
            raise KernelConflict("terminal completion CAS failed")
        settled = await self.projection_driver.settle(run.run_id)
        return KernelRunResult("final_answer", settled)

    async def _terminate(
        self,
        run: OrchestratorRunState,
        *,
        status: Literal["failed", "canceled", "budget_exhausted"],
        reason: str,
    ) -> KernelRunResult:
        sequence = (
            max((item.event_sequence for item in run.projection_outbox), default=0) + 1
        )
        request = TerminalStatusCommitRequest(
            expected_state_version=run.state_version,
            command_id=f"terminate:{status}:{run.run_id}:{run.state_version}",
            event_id=self.id_factory.new_id("event"),
            event_sequence=sequence,
            event_intent_id=self.id_factory.new_id("intent-event"),
            public_run_intent_id=self.id_factory.new_id("intent-run"),
            public_run_target=run.run_id,
            status=status,
            terminal_reason=reason,
            created_at=self.clock.now(),
        )
        committed = commit_terminal_status(run, request=request)
        if committed.outcome != "accepted":
            raise KernelConflict("terminal status CAS rejected")
        stored = await self.run_store.cas_mutate(
            committed.run,
            expected_state_version=run.state_version,
            command_id=request.command_id,
        )
        if stored.run is None:
            raise KernelConflict("terminal status store CAS failed")
        settled = await self.projection_driver.settle(run.run_id)
        return KernelRunResult(_outcome_for_status(status), settled)

    async def _update_entry(
        self,
        run: OrchestratorRunState,
        batch_index: int,
        entry_index: int,
        *,
        state: str,
        command: str,
        invocation: ToolInvocation | None = None,
        acceptance: ToolAcceptance | None = None,
        result: ToolResult | None = None,
    ) -> OrchestratorRunState:
        batches = list(run.tool_batches)
        batch = batches[batch_index]
        entries = list(batch.entries)
        original_entry = entries[entry_index]
        update: dict[str, object] = {
            "state": state,
            "buffered_terminal_result": result,
        }
        if invocation is not None:
            update["invocation"] = invocation
        if acceptance is not None:
            update["acceptance"] = acceptance
        desired_entry = original_entry.model_copy(update=update)
        entries[entry_index] = desired_entry
        batches[batch_index] = batch.model_copy(update={"entries": entries})
        try:
            return await self._checkpoint(
                run, updates={"tool_batches": batches}, command_id=command
            )
        except KernelConflict:
            current = await self._load(run.run_id)
            current_batch_index, current_entry_index = _find_entry(
                current,
                assistant_message_id=original_entry.assistant_message_id,
                call_id=original_entry.call_id,
            )
            if current_batch_index is None or current_entry_index is None:
                raise KernelConflict(
                    "tool entry disappeared during CAS retry"
                ) from None
            current_entry = current.tool_batches[current_batch_index].entries[
                current_entry_index
            ]
            if current_entry == desired_entry:
                return current
            if current_entry != original_entry:
                raise KernelConflict("tool entry changed during CAS retry") from None
            current_batches = list(current.tool_batches)
            current_batch = current_batches[current_batch_index]
            current_entries = list(current_batch.entries)
            current_entries[current_entry_index] = desired_entry
            current_batches[current_batch_index] = current_batch.model_copy(
                update={"entries": current_entries}
            )
            return await self._checkpoint(
                current,
                updates={"tool_batches": current_batches},
                command_id=command,
            )

    async def _checkpoint(
        self,
        run: OrchestratorRunState,
        *,
        updates: dict[str, object],
        command_id: str,
    ) -> OrchestratorRunState:
        now = self.clock.now()
        candidate = run.model_copy(
            update={
                **updates,
                "state_version": run.state_version + 1,
                "updated_at": now,
            }
        )
        result = await self.run_store.cas_mutate(
            candidate,
            expected_state_version=run.state_version,
            command_id=command_id,
        )
        if result.outcome in {"accepted", "replayed"} and result.run is not None:
            return result.run
        if result.outcome == "conflict":
            current = await self._load(run.run_id)
            if command_id in current.processed_command_ids:
                return current
        raise KernelConflict(f"checkpoint failed: {command_id}:{result.outcome}")

    async def _load(self, run_id: str) -> OrchestratorRunState:
        run = await self.run_store.load(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _model_request(
        self,
        run: OrchestratorRunState,
        messages: list[object],
        tools: list[object],
    ):
        from .models import ModelMessage, ModelTurnRequest, ToolDefinition

        return ModelTurnRequest(
            turn_id=self._stable_id(run, "model-turn", run.state_version),
            model=run.profile.model,
            system_prompt=run.profile.prompt.rendered_system_prompt,
            messages=[item for item in messages if isinstance(item, ModelMessage)],
            tools=[item for item in tools if isinstance(item, ToolDefinition)],
            tool_choice="none" if run.budget.wrap_up_requested else "auto",
            purpose="agent_turn",
            thinking_level=run.profile.thinking_level,
            remaining_provider_retries=self.budget_policy.remaining_provider_retries(
                run.budget, run.profile
            ),
            absolute_deadline_at=run.budget.deadline_at,
        )

    def _assembly_notice(
        self, run: OrchestratorRunState, exc: ModelStreamAssemblyError
    ) -> SessionNotice:
        notice_id = self._stable_id(
            run,
            exc.code,
            len(run.budget.provider_attempt_keys),
            exc.provider_call_id or "",
            exc.tool_index if exc.tool_index is not None else "",
            exc.raw_arguments_digest or "",
        )
        return SessionNotice(
            notice_id=notice_id,
            code=exc.code,
            content="The prior tool call was malformed or incomplete; retry safely.",
            related_call_id=exc.provider_call_id,
            created_at=self.clock.now(),
        )

    def _stable_id(self, run: OrchestratorRunState, *parts: object) -> str:
        raw = ":".join([run.run_id, *(str(part) for part in parts)])
        return sha256(raw.encode()).hexdigest()


def _merge_artifact_refs(existing: list[str], results: list[ToolResult]) -> list[str]:
    return list(
        dict.fromkeys(
            [*existing, *(ref for result in results for ref in result.artifact_refs)]
        )
    )


def _new_tool_batch(assistant: AssistantMessage) -> ToolCallBatch:
    return ToolCallBatch(
        assistant_message_id=assistant.message_id,
        entries=[
            ToolBatchEntry(
                call_id=call.call_id,
                assistant_message_id=assistant.message_id,
                source_index=index,
                tool_name=call.tool_name,
            )
            for index, call in enumerate(assistant.tool_calls)
        ],
    )


def _finalization_candidate(run: OrchestratorRunState) -> AssistantMessage | None:
    if run.proposed_final_message_id is None:
        return None
    candidates = [
        item
        for item in run.transcript
        if isinstance(item, AssistantMessage)
        and item.message_id == run.proposed_final_message_id
        and not item.tool_calls
    ]
    return candidates[0] if len(candidates) == 1 else None


def _assistant_missing_tool_batch(
    run: OrchestratorRunState,
) -> AssistantMessage | None:
    unresolved = unresolved_call_ids(run.transcript)
    batch_message_ids = {batch.assistant_message_id for batch in run.tool_batches}
    for item in run.transcript:
        if not isinstance(item, AssistantMessage) or not item.tool_calls:
            continue
        if item.message_id in batch_message_ids:
            continue
        if {call.call_id for call in item.tool_calls} & unresolved:
            return item
    return None


def _find_entry(
    run: OrchestratorRunState,
    *,
    assistant_message_id: str,
    call_id: str,
) -> tuple[int | None, int | None]:
    for batch_index, batch in enumerate(run.tool_batches):
        if batch.assistant_message_id != assistant_message_id:
            continue
        for entry_index, entry in enumerate(batch.entries):
            if entry.call_id == call_id:
                return batch_index, entry_index
    return None, None


def _tool_error(call: ToolCall, code: str, message: str) -> ToolResult:
    return ToolResult(
        call_id=call.call_id,
        tool_name=call.tool_name,
        status="rejected",
        content=[TextPart(text=message[:500])],
        artifact_refs=[],
        error_code=code,
        error_message=message[:500],
    )


def _flush_batch(
    transcript: list[object], batch: ToolCallBatch, created_at: datetime
) -> tuple[list[object], ToolCallBatch]:
    if batch.results_flushed:
        return transcript, batch
    results: list[ToolResultMessage] = []
    for entry in sorted(batch.entries, key=lambda item: item.source_index):
        result = entry.buffered_terminal_result
        if result is None:
            raise ValueError("cannot flush non-terminal tool batch")
        results.append(
            ToolResultMessage(
                message_id=f"tool-result:{entry.call_id}",
                call_id=result.call_id,
                tool_name=result.tool_name,
                status=result.status,
                content=result.content,
                artifact_refs=result.artifact_refs,
                is_error=result.status != "completed",
                error_code=result.error_code,
                error_message=result.error_message,
                created_at=created_at,
            )
        )
    return [*transcript, *results], batch.model_copy(update={"results_flushed": True})


def _wait_status(batch: ToolCallBatch) -> str:
    states = {entry.state for entry in batch.entries}
    if states & {"input_required", "auth_required"}:
        return "awaiting_user"
    return "waiting_external"


def _find_invocation(
    run: OrchestratorRunState, invocation_id: str
) -> tuple[int | None, int | None]:
    for batch_index, batch in enumerate(run.tool_batches):
        for entry_index, entry in enumerate(batch.entries):
            if entry.call_id == invocation_id:
                return batch_index, entry_index
    return None, None


def _outcome_for_status(status: str) -> KernelOutcome:
    return {
        "completed": "final_answer",
        "failed": "failed",
        "canceled": "aborted",
        "budget_exhausted": "budget_exhausted",
    }.get(status, "failed")  # type: ignore[return-value]


__all__ = [
    "KernelConflict",
    "KernelLifecycle",
    "KernelRunResult",
    "OrchestratorKernel",
    "SystemClock",
    "UUIDFactory",
]
