from __future__ import annotations

from hashlib import sha256

from execution.orchestrator.a2a_runtime.models import (
    AgentCallLedgerRecord,
    AgentToolBindingRecord,
    FrozenCallResourceManifest,
    ImmutableA2ADispatchSnapshot,
    PreparedInvocationSnapshot,
)
from execution.orchestrator.models import (
    ResolvedTool,
    ToolBindingRef,
    ToolDefinition,
    ToolInvocation,
)

from ._orchestrator_helpers import NOW


def definition(name: str = "agent_abc") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        label="Agent",
        description="test Agent",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["task"],
            "properties": {"task": {"type": "string"}},
        },
        execution_mode="parallel",
        side_effect_level="external",
    )


def binding(
    *,
    run_id: str = "run-1",
    transport_kind: str = "direct",
    agent_id: str = "agent-1",
    authorization_kind: str | None = None,
) -> AgentToolBindingRecord:
    tool = definition()
    return AgentToolBindingRecord(
        binding_id=f"binding-{run_id}",
        binding_digest=f"binding-digest-{run_id}",
        run_id=run_id,
        room_id="room-1",
        room_epoch=1,
        tool_name=tool.name,
        definition=tool,
        agent_id=agent_id,
        card_digest="card",
        endpoint_scope="https://agent.example/a2a",
        endpoint_scope_digest="endpoint",
        transport_kind=transport_kind,
        candidate_scope_id="scope",
        candidate_scope_revision=1,
        authorization_basis_digest="basis",
        authorization_kind=authorization_kind,
        requesting_subject_digest=sha256(b"user-1").hexdigest(),
        input_modes=["text"],
        output_modes=["text"],
        compatible_resource_refs=[],
        created_at=NOW,
    )


def manifest() -> FrozenCallResourceManifest:
    return FrozenCallResourceManifest(
        manifest_id="manifest", refs=[], content_digest="manifest-digest"
    )


def prepared(*, run_id: str = "run-1") -> PreparedInvocationSnapshot:
    return PreparedInvocationSnapshot(
        run_id=run_id,
        invocation_id="call-1",
        room_id="room-1",
        room_epoch=1,
        requesting_subject_id="user-1",
        binding=binding(run_id=run_id),
        resource_manifest=manifest(),
    )


def invocation(*, run_id: str = "run-1", call_id: str = "call-1") -> ToolInvocation:
    bound = binding(run_id=run_id)
    return ToolInvocation(
        invocation_id=call_id,
        run_id=run_id,
        expected_run_version=0,
        assistant_message_id="assistant-1",
        source_index=0,
        causation_id="assistant-1",
        idempotency_key=f"key-{run_id}-{call_id}",
        tool=ResolvedTool(
            definition=bound.definition,
            binding=ToolBindingRef(
                binding_id=bound.binding_id,
                binding_digest=bound.binding_digest,
            ),
        ),
        arguments={"task": "do work"},
        deadline_at=NOW,
    )


def ledger_record(
    *, run_id: str = "run-1", call_id: str = "call-1", state: str = "accepted"
) -> AgentCallLedgerRecord:
    bound = binding(run_id=run_id)
    resources = manifest()
    record_id = f"call-{sha256(f'{run_id}:{call_id}'.encode()).hexdigest()}"
    return AgentCallLedgerRecord(
        call_record_id=record_id,
        invocation_id=call_id,
        acceptance_id=f"acceptance-{run_id}-{call_id}",
        idempotency_key=f"key-{run_id}-{call_id}",
        run_id=run_id,
        room_id="room-1",
        room_epoch=1,
        assistant_message_id="assistant-1",
        source_index=0,
        tool_name=bound.tool_name,
        binding_id=bound.binding_id,
        binding_digest=bound.binding_digest,
        agent_id=bound.agent_id,
        card_digest=bound.card_digest,
        endpoint_scope_digest=bound.endpoint_scope_digest,
        arguments_digest="arguments",
        requesting_subject_digest=sha256(b"user-1").hexdigest(),
        dispatch_snapshot=ImmutableA2ADispatchSnapshot(
            command_id=f"dispatch-{record_id}",
            message_id=f"message-{record_id}",
            task="do work",
            agent_id=bound.agent_id,
            endpoint_scope=bound.endpoint_scope,
            transport_kind=bound.transport_kind,
            requesting_subject_digest=sha256(b"user-1").hexdigest(),
            room_id="room-1",
            room_epoch=1,
            deadline_at=NOW,
            resource_manifest=resources,
        ),
        resource_manifest=resources,
        state=state,
        transport_kind=bound.transport_kind,
        dispatch_command_id=f"dispatch-{record_id}",
        accepted_at=NOW,
        updated_at=NOW,
    )
