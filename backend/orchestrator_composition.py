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
from datetime import UTC, datetime
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
from common.utils.logger import get_logger
from dal.orchestrator.artifacts import GuardedRoomFileArtifactWriter
from dal.orchestrator.event_store import MongoOrchestratorEventStore
from dal.orchestrator.hitl import MongoHITLApplicationStore
from dal.orchestrator.projection import (
    MongoAppendEventProjector,
    MongoFinalMessageProjector,
    MongoTerminalRunStatusProjector,
)
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
from execution.orchestrator.a2a_runtime.cancellation import A2ACancellationCoordinator
from execution.orchestrator.a2a_runtime.catalog import FrozenToolCatalog
from execution.orchestrator.a2a_runtime.catalog_assembler import (
    AgentToolCatalogAssembler,
)
from execution.orchestrator.a2a_runtime.dispatch import DirectA2ADispatchAdapter
from execution.orchestrator.a2a_runtime.errors import (
    RecoverableAdapterError,
)
from execution.orchestrator.a2a_runtime.in_memory import RunCheckpointReader
from execution.orchestrator.a2a_runtime.ingress import (
    A2AObservationIngress,
    A2AObservationProcessor,
    RejectExternalIngressAuthenticator,
)
from execution.orchestrator.a2a_runtime.models import (
    A2ADispatchReceipt,
    NormalizedA2AObservation,
)
from execution.orchestrator.a2a_runtime.preparation import (
    RunPreparedInvocationSnapshotReader,
)
from execution.orchestrator.a2a_runtime.recovery import (
    A2AArtifactRecoveryService,
    A2ACallRecoveryService,
    A2ACancellationRecoveryService,
    A2AInboxRecoveryService,
    A2ARecoveryCycle,
    dispatch_command,
)
from execution.orchestrator.a2a_runtime.runtime import A2AAgentToolRuntime
from execution.orchestrator.a2a_runtime.terminal_interactions import (
    TerminalInteractionFinalizer,
)
from execution.orchestrator.budget import BudgetPolicy
from execution.orchestrator.context import ContextCompiler
from execution.orchestrator.kernel import OrchestratorKernel
from execution.orchestrator.model_runtime import GatewayModelRuntime
from execution.orchestrator.models import (
    FrozenToolCatalogSnapshot,
    OrchestratorProfile,
)
from execution.orchestrator.profiles import UnsupportedProviderCapabilities
from execution.orchestrator.projection import (
    ProjectionListener,
    ProjectionOutboxWorker,
    SettlingProjectionDriver,
)

logger = get_logger(__name__)


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
    hitl_store: Any
    hitl_port: Any
    catalog_assembler: Any
    tool_runtime: Any
    observation_ingress: Any
    observation_processor: Any
    dispatch: Any
    profile_resolver: Any
    profiles: dict[str, OrchestratorProfile]
    session_host: Any
    observation_sink: Any
    cancellation_coordinator: Any
    kernel_factory: Callable[[FrozenToolCatalogSnapshot], OrchestratorKernel]
    projection_worker: Any
    recovery_cycle: Any


_RUNTIME_BINDINGS = (
    "run_store",
    "event_store",
    "epoch_store",
    "binding_store",
    "call_ledger",
    "observation_inbox",
    "observation_conflicts",
    "hitl_store",
    "hitl_port",
    "catalog_assembler",
    "tool_runtime",
    "observation_ingress",
    "observation_processor",
    "dispatch",
    "profile_resolver",
    "profiles",
    "session_host",
    "observation_sink",
    "cancellation_coordinator",
    "kernel_factory",
    "projection_worker",
    "recovery_cycle",
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
    observation_authenticator: Any | None = None,
    session_listener: Any | None = None,
    projection_listener: ProjectionListener | None = None,
    user_message_text_reader: Callable[[str], Any] | None = None,
    final_message_delivery: Callable[..., Any] | None = None,
) -> OrchestratorRuntime:
    """Compose the full orchestrator runtime over the registered Mongo stores.

    No Mongo or LLM calls are made during construction; this is wiring only.
    """
    run_store = MongoOrchestratorRunStore(
        mongo.collection("orchestrator_runs").raw_collection
    )
    # The event store is bound for the projection worker (step 6), which
    # appends durable Run events through the outbox projector.
    event_store = MongoOrchestratorEventStore(
        mongo.collection("orchestrator_run_events").raw_collection
    )
    binding_store = MongoAgentToolBindingStore(
        mongo.collection("orchestrator_agent_tool_bindings").raw_collection
    )
    call_ledger = MongoAgentCallLedgerStore(
        mongo.collection("orchestrator_agent_calls").raw_collection
    )
    observation_inbox = MongoObservationInboxStore(
        mongo.collection("orchestrator_a2a_observations").raw_collection
    )
    observation_conflicts = MongoObservationConflictStore(
        mongo.collection("orchestrator_a2a_observation_conflicts").raw_collection
    )
    hitl_store = MongoHITLApplicationStore(
        mongo.collection("orchestrator_hitl_interactions").raw_collection
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

    # External ingress stays fully rejected until step 7 wires the per-source
    # authenticators (webhook HMAC via a2a_adapter, relay identity).
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
        context_text_reader=user_message_text_reader,
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
    dispatch = direct

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
        # The production projection driver never claims or completes intents
        # in-process. Terminal CAS already minted the required outbox intents;
        # the leader-elected ProjectionOutboxWorker delivers them. This driver
        # only attempts the idempotent settlement transition so the kernel
        # remains non-blocking and replay-safe.
        return OrchestratorKernel(
            run_store=run_store,
            model_runtime=model_runtime,
            tool_runtime=tool_runtime,
            tool_catalog=FrozenToolCatalog(snapshot),
            context_compiler=ContextCompiler(),
            budget_policy=BudgetPolicy(),
            projection_driver=SettlingProjectionDriver(run_store),
        )

    session_host = RoomSessionHost(
        kernel_factory=kernel_for_catalog,
        run_store=run_store,
        epoch_store=epoch_store,
        listener=session_listener,
    )

    observation_sink = session_host.observation_sink()

    projectors = {
        "append_orchestrator_event": MongoAppendEventProjector(event_store).project,
        "deliver_final_message": MongoFinalMessageProjector(
            mongo.collection("room_agent_messages"),
            final_message_delivery,
        ).project,
        "project_terminal_run_status": MongoTerminalRunStatusProjector(
            mongo.collection("runs"),
            mongo.collection("room_agent_messages"),
        ).project,
    }
    projection_worker = ProjectionOutboxWorker(
        run_store=run_store,
        projectors=projectors,
        after_project=projection_listener,
    )

    cancellation_coordinator = A2ACancellationCoordinator(
        ledger=call_ledger,
        room_epochs=epoch_store,
        dispatch=dispatch,
        observations=observation_ingress,
        hitl=hitl_port,
    )
    cancellation_recovery = A2ACancellationRecoveryService(
        coordinator=cancellation_coordinator,
        ledger=call_ledger,
    )
    observation_processor = A2AObservationProcessor(
        inbox=observation_inbox,
        conflicts=observation_conflicts,
        ledger=call_ledger,
        room_epochs=epoch_store,
        artifacts=resources,
        hitl=hitl_port,
        sink=observation_sink,
        checkpoint_reader=checkpoint_reader,
        outcome_reader=checkpoint_reader,
    )
    inbox_recovery = A2AInboxRecoveryService(
        processor=observation_processor,
        inbox=observation_inbox,
    )

    async def recover_dispatch(record: Any) -> None:
        # Re-delivery for checkpointed accepted/ready_to_dispatch calls. The
        # shared dispatch_command helper builds materialized_resources=[];
        # a resource-bearing Run re-dispatch would silently drop attachments
        # and context refs. Until the step-7 resource re-materialization path
        # lands, refuse that case loudly instead of dispatching empty
        # resources.
        if getattr(record, "resource_manifest", None) is not None and getattr(
            record.resource_manifest, "refs", []
        ):
            logger.warning(
                "orchestrator recovery: resource-bearing re-dispatch is "
                "unsupported until step 7; call %s stays due",
                record.call_record_id,
            )
            raise RecoverableAdapterError(
                "resource-bearing re-dispatch is not implemented"
            )
        await dispatch.dispatch(dispatch_command(record))

    call_recovery = A2ACallRecoveryService(
        ledger=call_ledger,
        checkpoints=checkpoint_reader,
        room_epochs=epoch_store,
        dispatch=dispatch,
        observations=observation_ingress,
        recover_dispatch=recover_dispatch,
    )
    artifact_recovery = A2AArtifactRecoveryService(inbox_recovery)

    async def _recovery_noop() -> None:
        # HITL continuation (needs the step-7 auth-reference verifier), generic
        # Run re-entry, and the orchestrator watchdog are bound in later steps.
        return None

    if getattr(settings_obj, "orchestrator_recovery_enabled", False):
        logger.warning(
            "orchestrator recovery enabled while continuation/generic_runs/"
            "watchdog phases are still no-ops; HITL continuations and generic "
            "Run re-entry will not run until step 7 binds them"
        )

    def _due_phase(recover: Callable[..., Any]) -> Callable[[], Any]:
        async def run() -> None:
            await recover(due_at=datetime.now(UTC))

        return run

    # Projection delivery is deliberately bound twice: as the recovery-cycle
    # projection phase AND as the standalone leader-gated projection job.
    # Both surfaces are idempotent (CAS + lease + dedupe) and re-drive the
    # same outbox; the redundancy self-heals whichever worker is behind.
    recovery_cycle = A2ARecoveryCycle(
        cancellation=_due_phase(cancellation_recovery.recover_due),
        continuation=_recovery_noop,
        observations=_due_phase(inbox_recovery.recover_due),
        calls=_due_phase(call_recovery.recover_due),
        artifacts=_due_phase(artifact_recovery.recover_due),
        generic_runs=_recovery_noop,
        projection=projection_worker.run_once,
        watchdog=_recovery_noop,
    )

    return OrchestratorRuntime(
        run_store=run_store,
        event_store=event_store,
        epoch_store=epoch_store,
        binding_store=binding_store,
        call_ledger=call_ledger,
        observation_inbox=observation_inbox,
        observation_conflicts=observation_conflicts,
        hitl_store=hitl_store,
        hitl_port=hitl_port,
        catalog_assembler=catalog_assembler,
        tool_runtime=tool_runtime,
        observation_ingress=observation_ingress,
        observation_processor=observation_processor,
        dispatch=dispatch,
        profile_resolver=profile_resolver,
        profiles=profiles,
        session_host=session_host,
        observation_sink=observation_sink,
        cancellation_coordinator=cancellation_coordinator,
        kernel_factory=kernel_for_catalog,
        projection_worker=projection_worker,
        recovery_cycle=recovery_cycle,
    )


__all__ = [
    "OrchestratorCompositionError",
    "OrchestratorRuntime",
    "create_orchestrator_runtime",
    "validate_orchestrator_runtime",
]
