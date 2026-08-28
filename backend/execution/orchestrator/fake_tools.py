"""Deterministic agent-shaped fake tools for kernel and session tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256

from .models import (
    DataPart,
    ResolvedTool,
    TextPart,
    ToolAcceptance,
    ToolBindingRef,
    ToolDefinition,
    ToolExecutionOutcome,
    ToolInvocation,
    ToolResult,
    ToolSuspension,
)
from .ports import CancellationSignal


def fake_agent_definitions() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="fake_agent_echo",
            label="Fake Agent Echo",
            description="Return a deterministic text or structured result.",
            input_schema={
                "type": "object",
                "properties": {
                    "value": {},
                    "structured": {"type": "boolean"},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
            execution_mode="parallel",
            side_effect_level="read",
        ),
        ToolDefinition(
            name="fake_agent_fail",
            label="Fake Agent Failure",
            description="Return a deterministic delegated-agent failure.",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "additionalProperties": False,
            },
            execution_mode="parallel",
            side_effect_level="read",
        ),
        ToolDefinition(
            name="fake_agent_delay_parallel",
            label="Fake Parallel Agent",
            description="Wait cancellably and complete; parallel safe.",
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number", "minimum": 0}},
                "additionalProperties": False,
            },
            execution_mode="parallel",
            side_effect_level="read",
        ),
        ToolDefinition(
            name="fake_agent_delay_sequential",
            label="Fake Sequential Agent",
            description="Wait cancellably and complete; sequential only.",
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number", "minimum": 0}},
                "additionalProperties": False,
            },
            execution_mode="sequential",
            side_effect_level="read",
        ),
        ToolDefinition(
            name="fake_agent_pause",
            label="Fake Pausing Agent",
            description="Suspend for an external observation or user input.",
            input_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "enum": [
                            "waiting_external",
                            "input_required",
                            "auth_required",
                        ]
                    }
                },
                "required": ["status"],
                "additionalProperties": False,
            },
            execution_mode="parallel",
            side_effect_level="external",
        ),
    ]


class StaticFakeToolCatalog:
    def __init__(self, definitions: list[ToolDefinition] | None = None) -> None:
        definitions = definitions or fake_agent_definitions()
        self._tools = {definition.name: definition for definition in definitions}

    def list_tools(self, run: object) -> list[ToolDefinition]:
        del run
        return list(self._tools.values())

    def resolve(self, run: object, tool_name: str) -> ResolvedTool:
        del run
        try:
            definition = self._tools[tool_name]
        except KeyError as exc:
            raise KeyError(f"unknown tool {tool_name!r}") from exc
        digest = sha256(definition.model_dump_json().encode()).hexdigest()
        return ResolvedTool(
            definition=definition,
            binding=ToolBindingRef(
                binding_id=f"fake:{tool_name}", binding_digest=digest
            ),
        )


class RecordingFakeToolRuntime:
    def __init__(self, *, fail_accept_for: set[str] | None = None) -> None:
        self.acceptances: dict[str, ToolAcceptance] = {}
        self.accept_log: list[str] = []
        self.execute_log: list[str] = []
        self.outcomes: dict[str, ToolExecutionOutcome] = {}
        self.fail_accept_for = fail_accept_for or set()

    async def accept(self, invocation: ToolInvocation) -> ToolAcceptance:
        if invocation.tool.definition.name in self.fail_accept_for:
            raise RuntimeError("fake acceptance failed")
        existing = self.acceptances.get(invocation.idempotency_key)
        if existing is not None:
            return existing
        acceptance = ToolAcceptance(
            acceptance_id=f"accept:{invocation.invocation_id}",
            invocation_id=invocation.invocation_id,
            idempotency_key=invocation.idempotency_key,
            accepted_at=datetime.now(UTC),
        )
        self.acceptances[invocation.idempotency_key] = acceptance
        self.accept_log.append(invocation.invocation_id)
        return acceptance

    async def execute(
        self,
        invocation: ToolInvocation,
        acceptance: ToolAcceptance,
        *,
        signal: CancellationSignal,
    ) -> ToolExecutionOutcome:
        stored = self.acceptances.get(invocation.idempotency_key)
        if stored != acceptance or acceptance.invocation_id != invocation.invocation_id:
            raise ValueError("missing or mismatched tool acceptance")
        prior = self.outcomes.get(acceptance.acceptance_id)
        if prior is not None:
            return prior
        self.execute_log.append(invocation.invocation_id)
        name = invocation.tool.definition.name
        if name == "fake_agent_echo":
            value = invocation.arguments["value"]
            content = (
                [DataPart(data={"value": value})]
                if invocation.arguments.get("structured")
                else [TextPart(text=str(value))]
            )
            outcome: ToolExecutionOutcome = _result(invocation, content=content)
        elif name == "fake_agent_fail":
            message = str(invocation.arguments.get("message", "fake failure"))
            outcome = _result(
                invocation,
                status="failed",
                content=[TextPart(text=message)],
                error_code="fake_failure",
                error_message=message,
            )
        elif name in {"fake_agent_delay_parallel", "fake_agent_delay_sequential"}:
            seconds = float(invocation.arguments.get("seconds", 0))
            await _cancellable_sleep(seconds, signal)
            outcome = _result(invocation, content=[TextPart(text="delayed result")])
        elif name == "fake_agent_pause":
            outcome = ToolSuspension(
                invocation_id=invocation.invocation_id,
                status=invocation.arguments["status"],  # type: ignore[arg-type]
                observation_cursor=f"cursor:{invocation.invocation_id}",
            )
        else:
            raise ValueError(f"unsupported fake tool {name!r}")
        self.outcomes[acceptance.acceptance_id] = outcome
        return outcome

    async def dispatch_model_reply(
        self,
        invocation: ToolInvocation,
        *,
        parent_call_record_id: str,
        interaction_fingerprint: str | None,
        signal: CancellationSignal,
    ) -> ToolExecutionOutcome:
        del parent_call_record_id, interaction_fingerprint
        outcome = self.outcomes.get(f"model-reply:{invocation.invocation_id}")
        if outcome is not None:
            return outcome
        return ToolSuspension(
            invocation_id=invocation.invocation_id,
            status="waiting_external",
        )

    async def publish_parked_interaction(
        self,
        *,
        call_record_id: str,
        interaction_id: str,
    ) -> None:
        del call_record_id, interaction_id
        return None

    async def abandon_parked_interaction(
        self,
        *,
        call_record_id: str,
        interaction_id: str,
        terminal_state: str,
    ) -> None:
        del call_record_id, interaction_id, terminal_state
        return None


async def _cancellable_sleep(seconds: float, signal: CancellationSignal) -> None:
    if signal.cancelled:
        raise asyncio.CancelledError
    sleep_task = asyncio.create_task(asyncio.sleep(seconds))
    cancel_task = asyncio.create_task(signal.wait())
    done, pending = await asyncio.wait(
        {sleep_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    if cancel_task in done:
        sleep_task.cancel()
        await asyncio.gather(sleep_task, return_exceptions=True)
        raise asyncio.CancelledError
    cancel_task.cancel()


def _result(
    invocation: ToolInvocation,
    *,
    status: str = "completed",
    content: list[object],
    error_code: str | None = None,
    error_message: str | None = None,
) -> ToolResult:
    return ToolResult(
        call_id=invocation.invocation_id,
        tool_name=invocation.tool.definition.name,
        status=status,  # type: ignore[arg-type]
        content=content,  # type: ignore[arg-type]
        artifact_refs=[],
        error_code=error_code,
        error_message=error_message,
    )


__all__ = [
    "RecordingFakeToolRuntime",
    "StaticFakeToolCatalog",
    "fake_agent_definitions",
]
