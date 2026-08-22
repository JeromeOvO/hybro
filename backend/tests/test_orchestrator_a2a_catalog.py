from __future__ import annotations

from execution.orchestrator.a2a_runtime.catalog import FrozenToolCatalog
from execution.orchestrator.a2a_runtime.catalog_assembler import (
    AgentToolCatalogAssembler,
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
    CandidateScopeSnapshot,
    PreparedResourceRef,
    RunResourceManifestSnapshot,
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
        "display_name": "Broker",
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
    assert (await store.load(prepared.bindings[0].binding_id)) == prepared.bindings[0]
    schema = prepared.snapshot.entries[0].definition.input_schema
    # The tool description carries the agent's I/O contract so the kernel can
    # match capabilities instead of relying on a one-line blurb.
    description = prepared.snapshot.entries[0].definition.description
    assert "Places insurance" in description
    assert "Input: text, application/pdf" in description
    assert "agent_id" not in schema["properties"]
    assert schema["properties"]["artifact_refs"]["items"]["enum"] == ["artifact-1"]


async def test_frozen_catalog_is_synchronous_and_run_bound():
    prepared, _, _ = await prepare([candidate()])
    run = make_run().model_copy(update={"tool_catalog": prepared.snapshot})
    catalog = FrozenToolCatalog(prepared.snapshot)
    assert catalog.list_tools(run) == [prepared.snapshot.entries[0].definition]
    resolved = catalog.resolve(run, prepared.snapshot.entries[0].definition.name)
    assert resolved.binding == prepared.snapshot.entries[0].binding


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
