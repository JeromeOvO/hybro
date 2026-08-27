from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from container import _project_orchestrator_agent_activity
from execution.orchestrator.a2a_runtime.catalog import FrozenToolCatalog
from execution.orchestrator.a2a_runtime.catalog_assembler import (
    AgentToolCatalogAssembler,
    agent_tool_input_schema,
    deterministic_tool_name,
)
from execution.orchestrator.a2a_runtime.in_memory import (
    InMemoryAgentToolBindingStore,
    InMemoryRoomEpochStore,
)
from execution.orchestrator.a2a_runtime.models import AgentToolCandidate
from execution.orchestrator.a2a_runtime.preparation import (
    RunPreparedInvocationSnapshotReader,
)
from execution.orchestrator.in_memory import InMemoryOrchestratorRunStore
from execution.orchestrator.models import (
    AssistantMessage,
    CandidateScopeSnapshot,
    PreparedResourceRef,
    RunResourceManifestSnapshot,
    TextPart,
    ToolAcceptance,
    ToolBatchEntry,
    ToolCall,
    ToolCallBatch,
    ToolInvocation,
    ToolResult,
)

from ._orchestrator_a2a_helpers import invocation
from ._orchestrator_helpers import NOW, make_run


class Candidates:
    def __init__(self, values):
        self.values = values
        self.calls = 0

    async def list_candidates(self, **kwargs):
        self.calls += 1
        assert kwargs["requesting_subject_id"] == "user-1"
        return list(self.values)


def candidate(agent_id="agent-1", **updates):
    values = {
        "agent_id": agent_id,
        "agent_display_name": "Broker Agent",
        "display_name": "Broker Agent - Place Policy",
        "description": "Places insurance",
        "card_digest": "card-1",
        "endpoint_scope": "https://agent.example/a2a",
        "endpoint_scope_digest": "endpoint-1",
        "transport_kind": "direct",
        "input_modes": ["text", "application/pdf"],
    }
    values.update(updates)
    return AgentToolCandidate(**values)


async def prepare(values):
    epochs = InMemoryRoomEpochStore()
    assert (await epochs.activate("room-1", "create-1", activated_at=NOW))[
        0
    ] == "accepted"
    bindings = InMemoryAgentToolBindingStore()
    source = Candidates(values)
    assembler = AgentToolCatalogAssembler(
        candidate_source=source,
        binding_store=bindings,
        room_epoch_store=epochs,
    )
    manifest = RunResourceManifestSnapshot(
        manifest_id="resources",
        refs=[
            PreparedResourceRef(
                ref_id="artifact-1",
                kind="artifact",
                source_message_id="message-1",
                mime_type="application/pdf",
                size_bytes=10,
                content_digest="digest-1",
            )
        ],
        content_digest="manifest-digest",
    )
    prepared = await assembler.prepare(
        run_id="run-1",
        room_id="room-1",
        room_epoch=1,
        requesting_subject_id="user-1",
        candidate_scope=CandidateScopeSnapshot(
            snapshot_id="scope-1",
            revision=2,
            source="test",
            room_id="room-1",
            agent_ids=[item.agent_id for item in values],
        ),
        resource_manifest=manifest,
        authorization_basis_digest="auth-basis",
        created_at=NOW,
    )
    return prepared, source, bindings


def test_tool_name_is_provider_safe_stable_and_private():
    first = deterministic_tool_name("private-owner/agent", "skill with spaces")
    second = deterministic_tool_name("private-owner/agent", "skill with spaces")
    assert first == second
    assert first.startswith("agent_")
    assert len(first) <= 64
    assert "private" not in first
    assert set(first) <= set("abcdefghijklmnopqrstuvwxyz0123456789_-")


async def test_async_assembler_filters_candidates_and_persists_private_bindings():
    prepared, source, store = await prepare(
        [
            candidate(),
            candidate("agent-2", authorized=False),
            candidate("agent-3", excluded=True),
        ]
    )
    assert source.calls == 1
    assert len(prepared.snapshot.entries) == 1
    assert len(prepared.bindings) == 1
    persisted = await store.load(prepared.bindings[0].binding_id)
    assert persisted == prepared.bindings[0]
    assert persisted is not None
    assert persisted.agent_display_name == "Broker Agent"
    assert persisted.definition.label == "Broker Agent - Place Policy"
    schema = prepared.snapshot.entries[0].definition.input_schema
    # The tool description carries the agent's I/O contract so the kernel can
    # match capabilities instead of relying on a one-line blurb.
    description = prepared.snapshot.entries[0].definition.description
    assert "Places insurance" in description
    assert "Input: text, application/pdf" in description
    assert "agent_id" not in schema["properties"]
    assert schema["properties"]["artifact_refs"]["items"]["enum"] == ["artifact-1"]


async def test_root_agent_name_survives_binding_projection_delivery_and_persistence():
    prepared, _, bindings = await prepare(
        [
            candidate(
                agent_display_name="Weather Agent",
                display_name="Weather Agent - Get Current Weather",
            )
        ]
    )
    binding = prepared.bindings[0]
    assert binding.agent_display_name == "Weather Agent"
    assert binding.definition.label == "Weather Agent - Get Current Weather"

    run = make_run().model_copy(update={"tool_catalog": prepared.snapshot})
    tool_name = prepared.snapshot.entries[0].definition.name
    resolved = FrozenToolCatalog(prepared.snapshot).resolve(run, tool_name)
    assistant = AssistantMessage(
        message_id="assistant-weather",
        content=[TextPart(text="Check the weather")],
        tool_calls=[
            ToolCall(
                call_id="call-weather",
                tool_name=tool_name,
                arguments={"task": "Get current weather"},
            )
        ],
        finish_reason="tool_calls",
        usage=None,
        created_at=NOW,
    )
    invocation = ToolInvocation(
        invocation_id="call-weather",
        run_id=run.run_id,
        expected_run_version=run.state_version,
        assistant_message_id=assistant.message_id,
        source_index=0,
        causation_id=run.request.user_message_id,
        idempotency_key="invoke-weather",
        tool=resolved,
        arguments={"task": "Get current weather"},
        deadline_at=NOW,
    )
    acceptance = ToolAcceptance(
        acceptance_id="accept-weather",
        invocation_id=invocation.invocation_id,
        idempotency_key=invocation.idempotency_key,
        accepted_at=NOW,
    )
    run = run.model_copy(
        update={
            "transcript": [*run.transcript, assistant],
            "tool_batches": [
                ToolCallBatch(
                    assistant_message_id=assistant.message_id,
                    entries=[
                        ToolBatchEntry(
                            call_id="call-weather",
                            opaque_public_call_id="inv_weather_0001",
                            assistant_message_id=assistant.message_id,
                            source_index=0,
                            tool_name=tool_name,
                            state="accepted",
                            invocation=invocation,
                            acceptance=acceptance,
                        )
                    ],
                )
            ],
        }
    )
    runtime = SimpleNamespace(
        run_store=SimpleNamespace(load=AsyncMock(return_value=run)),
        binding_store=bindings,
        public_secret_values=(),
    )
    message_store = SimpleNamespace(upsert_room_agent_message=AsyncMock())
    delivery = SimpleNamespace(
        send_task_submitted=AsyncMock(), send_task_update=AsyncMock()
    )

    await _project_orchestrator_agent_activity(
        SimpleNamespace(
            event_type="tool_execution_started",
            run_id=run.run_id,
            payload={"call_id": "call-weather"},
        ),
        runtime,
        message_store,
        delivery,
    )

    persisted = message_store.upsert_room_agent_message.await_args.args[0]
    assert persisted.extend_info["public_agent_name"] == "Weather Agent"
    assert persisted.extend_info["public_task_label"] == (
        "Requesting Weather Agent - Get Current Weather"
    )
    # Canonical Runs fold Agent Cards from tool_execution_* events and never
    # emit the legacy task_submitted/task_update card contract.
    assert delivery.send_task_submitted.await_args is None
    assert delivery.send_task_update.await_args is None

    # Legacy Runs keep the task_* card contract with the exact root Agent name.
    legacy_run = run.model_copy(update={"lifecycle_family": "legacy"})
    legacy_runtime = SimpleNamespace(
        run_store=SimpleNamespace(load=AsyncMock(return_value=legacy_run)),
        binding_store=bindings,
        public_secret_values=(),
    )
    legacy_delivery = SimpleNamespace(
        send_task_submitted=AsyncMock(), send_task_update=AsyncMock()
    )
    await _project_orchestrator_agent_activity(
        SimpleNamespace(
            event_type="tool_execution_started",
            run_id=run.run_id,
            payload={"call_id": "call-weather"},
        ),
        legacy_runtime,
        message_store,
        legacy_delivery,
    )
    submitted = legacy_delivery.send_task_submitted.await_args.kwargs
    assert submitted["agent_name"] == "Weather Agent"
    assert submitted["agent_name"] != "Weather Agent - Get Current Weather"

    terminal_entry = (
        legacy_run.tool_batches[0]
        .entries[0]
        .model_copy(
            update={
                "state": "terminal",
                "buffered_terminal_result": ToolResult(
                    call_id="call-weather",
                    tool_name=tool_name,
                    status="completed",
                    content=[TextPart(text="Sunny")],
                    artifact_refs=[],
                ),
            }
        )
    )
    terminal_run = legacy_run.model_copy(
        update={
            "tool_batches": [
                legacy_run.tool_batches[0].model_copy(
                    update={"entries": [terminal_entry]}
                )
            ]
        }
    )
    legacy_runtime.run_store.load = AsyncMock(return_value=terminal_run)
    await _project_orchestrator_agent_activity(
        SimpleNamespace(
            event_type="message_completed",
            run_id=run.run_id,
            payload={"call_id": "call-weather", "message_kind": "tool_result"},
        ),
        legacy_runtime,
        message_store,
        legacy_delivery,
    )
    terminal_update = legacy_delivery.send_task_update.await_args.kwargs
    assert terminal_update["status"] == "completed"
    assert terminal_update["content"] == "Sunny"
    assert terminal_update["delivery_id"] == (
        f"orchestrator:{run.run_id}:call-weather:terminal:completed"
    )

    # A canonical Run with an unreadable binding still never leaks the
    # skill-qualified Trace label into the card projection.
    missing_binding_store = SimpleNamespace(upsert_room_agent_message=AsyncMock())
    missing_binding_delivery = SimpleNamespace(
        send_task_submitted=AsyncMock(), send_task_update=AsyncMock()
    )
    missing_binding_runtime = SimpleNamespace(
        run_store=SimpleNamespace(load=AsyncMock(return_value=run)),
        binding_store=SimpleNamespace(load=AsyncMock(return_value=None)),
        public_secret_values=(),
    )
    await _project_orchestrator_agent_activity(
        SimpleNamespace(
            event_type="tool_execution_started",
            run_id=run.run_id,
            payload={"call_id": "call-weather"},
        ),
        missing_binding_runtime,
        missing_binding_store,
        missing_binding_delivery,
    )
    missing_doc = missing_binding_store.upsert_room_agent_message.await_args.args[0]
    assert missing_doc.extend_info["public_agent_name"] == "Unknown agent"
    assert missing_doc.extend_info["public_agent_name"] != (
        "Weather Agent - Get Current Weather"
    )


def test_input_schema_omits_ref_fields_when_no_resources_available():
    """A free-form ref field invites the model to invent reference ids, which
    authorization then rejects. With no resources the schema exposes only
    ``task`` so the model inlines facts instead."""
    schema = agent_tool_input_schema([], [], [])
    assert set(schema["properties"]) == {"task"}
    assert "context_refs" not in schema["properties"]
    assert "artifact_refs" not in schema["properties"]
    assert "attachment_refs" not in schema["properties"]


def test_input_schema_bounds_ref_fields_to_available_resources():
    schema = agent_tool_input_schema([], ["artifact-1", "artifact-2"], ["file:att-1"])
    assert schema["properties"]["artifact_refs"]["items"]["enum"] == [
        "artifact-1",
        "artifact-2",
    ]
    assert schema["properties"]["attachment_refs"]["items"]["enum"] == ["file:att-1"]
    # A family with no refs stays omitted so the model cannot invent ids.
    assert "context_refs" not in schema["properties"]


async def test_frozen_catalog_is_synchronous_and_run_bound():
    prepared, _, _ = await prepare([candidate()])
    run = make_run().model_copy(update={"tool_catalog": prepared.snapshot})
    catalog = FrozenToolCatalog(prepared.snapshot)
    assert catalog.list_tools(run) == [prepared.snapshot.entries[0].definition]
    resolved = catalog.resolve(run, prepared.snapshot.entries[0].definition.name)
    assert resolved.binding == prepared.snapshot.entries[0].binding


async def test_live_artifact_schema_is_identical_for_listing_and_resolution():
    prepared, _, _ = await prepare(
        [candidate(input_modes=["text", "application/json"])]
    )
    run = make_run().model_copy(
        update={
            "tool_catalog": prepared.snapshot,
            "resource_manifest": RunResourceManifestSnapshot(
                manifest_id="live-resources",
                refs=[
                    PreparedResourceRef(
                        ref_id="art_inline",
                        kind="artifact",
                        source_message_id="observation-1",
                        mime_type="application/json",
                        size_bytes=42,
                        content_digest="inline-digest",
                    )
                ],
                content_digest="live-manifest-digest",
            ),
        }
    )
    catalog = FrozenToolCatalog(prepared.snapshot)
    listed = catalog.list_tools(run)[0]
    resolved = catalog.resolve(run, listed.name)

    assert listed.input_schema == resolved.definition.input_schema
    assert listed.input_schema["properties"]["artifact_refs"]["items"]["enum"] == [
        "art_inline"
    ]


async def test_card_or_endpoint_change_changes_binding_digest_only_for_new_run():
    first, _, _ = await prepare([candidate()])
    second, _, _ = await prepare([candidate(card_digest="card-2")])
    assert (
        first.snapshot.entries[0].definition.name
        == second.snapshot.entries[0].definition.name
    )
    assert first.bindings[0].binding_digest != second.bindings[0].binding_digest


async def test_prepared_invocation_reconstructs_from_durable_run_and_binding():
    prepared_catalog, _, binding_store = await prepare([candidate()])
    run = make_run().model_copy(
        update={
            "run_id": "run-1",
            "tool_catalog": prepared_catalog.snapshot,
            "resource_manifest": RunResourceManifestSnapshot(
                manifest_id="empty", refs=[], content_digest="empty"
            ),
            "request": make_run().request.model_copy(
                update={"requesting_subject_id": "user-1", "room_epoch": 1}
            ),
        }
    )
    store = InMemoryOrchestratorRunStore()
    await store.create(run, command_id="create")
    reader = RunPreparedInvocationSnapshotReader(
        run_store=store, binding_store=binding_store
    )
    call = invocation().model_copy(
        update={
            "tool": invocation().tool.model_copy(
                update={
                    "definition": prepared_catalog.snapshot.entries[0].definition,
                    "binding": prepared_catalog.snapshot.entries[0].binding,
                }
            )
        }
    )
    snapshot = await reader.read_prepared(call)
    assert snapshot is not None
    assert snapshot.requesting_subject_id == "user-1"
    assert snapshot.binding == prepared_catalog.bindings[0]


async def test_preparation_fails_closed_for_inactive_epoch():
    epochs = InMemoryRoomEpochStore()
    assembler = AgentToolCatalogAssembler(
        candidate_source=Candidates([candidate()]),
        binding_store=InMemoryAgentToolBindingStore(),
        room_epoch_store=epochs,
    )
    try:
        await assembler.prepare(
            run_id="run-1",
            room_id="room-1",
            room_epoch=1,
            requesting_subject_id="user-1",
            candidate_scope=CandidateScopeSnapshot(
                snapshot_id="scope",
                source="test",
                room_id="room-1",
                agent_ids=["agent-1"],
            ),
            resource_manifest=RunResourceManifestSnapshot(
                manifest_id="empty", refs=[], content_digest="empty"
            ),
            authorization_basis_digest="auth",
            created_at=NOW,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("inactive Room epoch was accepted")
