"""Production composition root for the orchestrator runtime (dark launch).

This module assembles the full ``execution.orchestrator`` runtime from Mongo
stores, typed settings, and the existing product services. It is imported only
by ``container.py`` (the composition root) and tests; no route, facade, or job
may reach it until step 7 wires dual-routing ingress.

Construction is failure-isolated at the adapter boundary: missing model routes,
missing prompt assets, or invalid profile parameters raise
``OrchestratorCompositionError`` so ``container.py`` can log and continue
serving the legacy product. Programming errors (broken wiring, wrong types,
import failures) are intentionally *not* swallowed here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from a2a_adapter.client_facade import (
    cancel_remote_task as sdk_cancel_remote_task,
)
from a2a_adapter.client_facade import (
    fetch_agent_card_with_fallback as sdk_fetch_agent_card,
)
from a2a_adapter.client_facade import send_message as sdk_send_message
from a2a_adapter.client_facade import stream_message as sdk_stream_message
from a2a_adapter.orchestrator_direct_client import OrchestratorDirectA2AClient
from a2a_adapter.remote_task import fetch_remote_task as sdk_fetch_remote_task
from dal.orchestrator.artifacts import GuardedRoomFileArtifactWriter
from dal.orchestrator.event_store import MongoOrchestratorEventStore
from dal.orchestrator.hitl import MongoHITLApplicationStore
from dal.orchestrator.run_store import MongoOrchestratorRunStore
from dal.orchestrator.stores import (
    MongoAgentCallLedgerStore,
    MongoAgentToolBindingStore,
    MongoObservationConflictStore,
    MongoObservationInboxStore,
)
from execution.adapters.agent_candidates import AgentServiceCandidateSource
from execution.adapters.authorization import MembershipAuthorizationRefresh
from execution.adapters.hitl import DurableHITLApplicationPort
from execution.adapters.profiles import (
    OrchestratorProfileResolutionError,
    OrchestratorProfileResolver,
    PromptAssetRegistry,
)
from execution.adapters.resources import RoomFilesResourceMaterializer
from execution.adapters.session_host import RoomSessionHost
from execution.orchestrator.a2a_runtime.catalog import FrozenToolCatalog
from execution.orchestrator.a2a_runtime.catalog_assembler import (
    AgentToolCatalogAssembler,
)
from execution.orchestrator.a2a_runtime.dispatch import (
    DirectA2ADispatchAdapter,
    RelayA2ADispatchAdapter,
    RoutedA2ADispatchPort,
)
from execution.orchestrator.a2a_runtime.in_memory import RunCheckpointReader
from execution.orchestrator.a2a_runtime.ingress import (
    A2AObservationIngress,
    RejectExternalIngressAuthenticator,
)
from execution.orchestrator.a2a_runtime.models import (
    A2ADispatchReceipt,
    NormalizedA2AObservation,
)
from execution.orchestrator.a2a_runtime.observations import (
    RunAddressedToolObservationSink,
)
from execution.orchestrator.a2a_runtime.preparation import (
    RunPreparedInvocationSnapshotReader,
)
from execution.orchestrator.a2a_runtime.runtime import A2AAgentToolRuntime
from execution.orchestrator.a2a_runtime.terminal_interactions import (
    TerminalInteractionFinalizer,
)
from execution.orchestrator.budget import BudgetPolicy
from execution.orchestrator.context import ContextCompiler
from execution.orchestrator.in_memory import InMemoryProjectionDriver
from execution.orchestrator.kernel import OrchestratorKernel
from execution.orchestrator.model_runtime import GatewayModelRuntime
from execution.orchestrator.models import (
    FrozenToolCatalogSnapshot,
    OrchestratorProfile,
)
from execution.orchestrator.profiles import UnsupportedProviderCapabilities
from execution.orchestrator.session import EventCancellationSignal
from hub_runtime_bridge.orchestrator_relay import (
    MongoRelayCommandJournalStore,
    RelayCommandJournal,
    RelayCommandSender,
)


class OrchestratorCompositionError(RuntimeError):
    """Adapter-level composition failure; safe to degrade the dark launch."""


@dataclass(frozen=True, slots=True)
class OrchestratorRuntime:
    run_store: Any
    event_store: Any
    epoch_store: Any
    binding_store: Any
    call_ledger: Any
    observation_inbox: Any
    observation_conflicts: Any
    hitl_port: Any
    catalog_assembler: Any
    tool_runtime: Any
    observation_ingress: Any
    dispatch: Any
    profile_resolver: Any
    profiles: dict[str, OrchestratorProfile]
    session_host: Any
    observation_sink: Any
    kernel_factory: Callable[[FrozenToolCatalogSnapshot], OrchestratorKernel]


_RUNTIME_BINDINGS = (
    "run_store",
    "event_store",
    "epoch_store",
    "binding_store",
    "call_ledger",
    "observation_inbox",
    "observation_conflicts",
    "hitl_port",
    "catalog_assembler",
    "tool_runtime",
    "observation_ingress",
    "dispatch",
    "profile_resolver",
    "profiles",
    "session_host",
    "observation_sink",
    "kernel_factory",
)


def validate_orchestrator_runtime(runtime: Any) -> list[str]:
    """List missing bindings for a (possibly incomplete) composition."""
    if runtime is None:
        return ["runtime"]
    return [name for name in _RUNTIME_BINDINGS if getattr(runtime, name, None) is None]


def create_orchestrator_runtime(  # noqa: C901
    *,
    mongo: Any,
    settings_obj: Any,
    llm_gateway: Any,
    model_registry: Any,
    agent_registry: Any,
    exclusion_reader: Any,
    room_ownership_reader: Any,
    epoch_store: Any,
    room_files: Any,
    relay_service: Any,
    observation_authenticator: Any | None = None,
    session_listener: Any | None = None,
) -> OrchestratorRuntime:
    """Compose the full orchestrator runtime over the registered Mongo stores.

    No Mongo or LLM calls are made during construction; this is wiring only.
    """
    run_store = MongoOrchestratorRunStore(mongo.collection("orchestrator_runs"))
    event_store = MongoOrchestratorEventStore(
        mongo.collection("orchestrator_run_events")
    )
    binding_store = MongoAgentToolBindingStore(
        mongo.collection("orchestrator_agent_tool_bindings")
    )
    call_ledger = MongoAgentCallLedgerStore(
        mongo.collection("orchestrator_agent_calls")
    )
    observation_inbox = MongoObservationInboxStore(
        mongo.collection("orchestrator_a2a_observations")
    )
    observation_conflicts = MongoObservationConflictStore(
        mongo.collection("orchestrator_a2a_observation_conflicts")
    )
    hitl_store = MongoHITLApplicationStore(
        mongo.collection("orchestrator_hitl_interactions")
    )

    profile_resolver = OrchestratorProfileResolver(
        model_registry=model_registry,
        prompt_registry=PromptAssetRegistry(),
        settings_obj=settings_obj,
    )
    try:
        profiles = {
            "fast": profile_resolver.resolve("fast"),
            "ultimate": profile_resolver.resolve("ultimate"),
        }
    except (
        OrchestratorProfileResolutionError,
        UnsupportedProviderCapabilities,
        ValueError,
    ) as exc:
        # Narrow resolution boundary only (model route, prompt asset, digest,
        # capability). Unexpected errors remain programming errors.
        raise OrchestratorCompositionError(
            f"orchestrator profile resolution failed: {exc}"
        ) from exc

    candidate_source = AgentServiceCandidateSource(
        agents=agent_registry,
        exclusion_reader=exclusion_reader,
    )
    authorization = MembershipAuthorizationRefresh(
        agents=agent_registry,
        room_ownership=room_ownership_reader,
    )
    catalog_assembler = AgentToolCatalogAssembler(
        candidate_source=candidate_source,
        binding_store=binding_store,
        room_epoch_store=epoch_store,
    )

    hitl_port = DurableHITLApplicationPort(hitl_store=hitl_store)
    terminal_finalizer = TerminalInteractionFinalizer(hitl_port)

    authenticator = observation_authenticator or RejectExternalIngressAuthenticator()
    observation_ingress = A2AObservationIngress(
        inbox=observation_inbox,
        conflicts=observation_conflicts,
        ledger=call_ledger,
        authenticator=authenticator,
    )

    artifact_writer = GuardedRoomFileArtifactWriter(
        room_files=room_files,
        room_epochs=epoch_store,
    )
    resources = RoomFilesResourceMaterializer(
        room_files=room_files,
        artifact_writer=artifact_writer,
    )

    async def resolve_call_address(
        call_record_id: str,
    ) -> dict[str, Any] | None:
        record = await call_ledger.load_by_record_id(call_record_id)
        if record is None:
            return None
        return {
            "task_id": record.a2a_task_id,
            "context_id": record.a2a_context_id,
            "endpoint_scope": record.dispatch_snapshot.endpoint_scope,
            "agent_id": record.agent_id,
        }

    direct_client = OrchestratorDirectA2AClient(
        send_message=sdk_send_message,
        stream_message=sdk_stream_message,
        cancel_remote_task=sdk_cancel_remote_task,
        fetch_remote_task=sdk_fetch_remote_task,
        fetch_agent_card=sdk_fetch_agent_card,
        receipt_factory=A2ADispatchReceipt,
        observation_factory=NormalizedA2AObservation,
        call_resolver=resolve_call_address,
    )
    direct = DirectA2ADispatchAdapter(direct_client, observations=observation_ingress)

    relay_store = MongoRelayCommandJournalStore(mongo)
    relay_journal = RelayCommandJournal(
        store=relay_store, receipt_factory=A2ADispatchReceipt
    )
    relay_sender = RelayCommandSender(
        relay_service=relay_service,
        store=relay_store,
        receipt_factory=A2ADispatchReceipt,
        call_resolver=resolve_call_address,
    )
    relay = RelayA2ADispatchAdapter(journal=relay_journal, sender=relay_sender)
    dispatch = RoutedA2ADispatchPort(direct=direct, relay=relay)

    prepared_reader = RunPreparedInvocationSnapshotReader(
        run_store=run_store,
        binding_store=binding_store,
    )
    checkpoint_reader = RunCheckpointReader(run_store)
    tool_runtime = A2AAgentToolRuntime(
        ledger=call_ledger,
        prepared_reader=prepared_reader,
        checkpoint_reader=checkpoint_reader,
        authorization=authorization,
        room_epochs=epoch_store,
        resources=resources,
        dispatch=dispatch,
        observations=observation_ingress,
        terminal_finalizer=terminal_finalizer,
    )

    model_runtime = GatewayModelRuntime(llm_gateway)

    def kernel_for_catalog(
        snapshot: FrozenToolCatalogSnapshot,
    ) -> OrchestratorKernel:
        # InMemoryProjectionDriver is a deliberate placeholder until step 6
        # introduces the real projection driver/worker. It satisfies the
        # kernel's terminal-settlement contract without external side effects.
        return OrchestratorKernel(
            run_store=run_store,
            model_runtime=model_runtime,
            tool_runtime=tool_runtime,
            tool_catalog=FrozenToolCatalog(snapshot),
            context_compiler=ContextCompiler(),
            budget_policy=BudgetPolicy(),
            projection_driver=InMemoryProjectionDriver(run_store),
        )

    session_host = RoomSessionHost(
        kernel_factory=kernel_for_catalog,
        run_store=run_store,
        epoch_store=epoch_store,
        listener=session_listener,
    )

    def kernel_for_run(run: Any) -> OrchestratorKernel:
        if run.tool_catalog is None:
            raise OrchestratorCompositionError("Run has no frozen tool catalog")
        return kernel_for_catalog(run.tool_catalog)

    observation_sink = RunAddressedToolObservationSink(
        run_store=run_store,
        kernel_factory=kernel_for_run,
        signal_factory=EventCancellationSignal,
    )

    return OrchestratorRuntime(
        run_store=run_store,
        event_store=event_store,
        epoch_store=epoch_store,
        binding_store=binding_store,
        call_ledger=call_ledger,
        observation_inbox=observation_inbox,
        observation_conflicts=observation_conflicts,
        hitl_port=hitl_port,
        catalog_assembler=catalog_assembler,
        tool_runtime=tool_runtime,
        observation_ingress=observation_ingress,
        dispatch=dispatch,
        profile_resolver=profile_resolver,
        profiles=profiles,
        session_host=session_host,
        observation_sink=observation_sink,
        kernel_factory=kernel_for_catalog,
    )


__all__ = [
    "OrchestratorCompositionError",
    "OrchestratorRuntime",
    "create_orchestrator_runtime",
    "validate_orchestrator_runtime",
]
