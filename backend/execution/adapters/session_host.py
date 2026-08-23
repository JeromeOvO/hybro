"""Process-local ``RoomAgentSession`` host (one active session per Room).

The host resolves the active Room epoch from the bound epoch store and builds a
``RoomAgentSession`` with a frozen profile/catalog/scope snapshot before Run
creation. Lifecycle ``SessionEvent`` values are forwarded to an injected
listener (the step-6 listener will write to the projection outbox; during the
step-5b dark launch a no-op/recording listener is used).

The host is deliberately unreachable from routes: it exposes only the builder
surface used by the composition root and tests.
"""

from __future__ import annotations

from collections.abc import Callable

from execution.orchestrator.a2a_runtime.catalog import FrozenToolCatalog
from execution.orchestrator.a2a_runtime.observations import (
    RunAddressedToolObservationSink,
)
from execution.orchestrator.a2a_runtime.ports import RoomEpochStore
from execution.orchestrator.kernel import (
    KernelRunResult,
    OrchestratorKernel,
    SystemClock,
    UUIDFactory,
)
from execution.orchestrator.lifecycle import (
    LifecycleEmitter,
    SessionEventListener,
)
from execution.orchestrator.models import (
    CandidateScopeSnapshot,
    FrozenToolCatalogSnapshot,
    OrchestratorProfile,
    RunResourceManifestSnapshot,
    ToolObservation,
    UserMessage,
)
from execution.orchestrator.ports import OrchestratorRunStore
from execution.orchestrator.session import (
    DefaultRunFactory,
    EventCancellationSignal,
    RoomAgentSession,
    RoomAgentSessionConfig,
    RunFactory,
    SessionConflict,
)

KernelForCatalog = Callable[[FrozenToolCatalogSnapshot], OrchestratorKernel]


class RoomSessionHost:
    """Registry of one active ``RoomAgentSession`` per Room."""

    def __init__(
        self,
        *,
        kernel_factory: KernelForCatalog,
        run_store: OrchestratorRunStore,
        epoch_store: RoomEpochStore,
        listener: SessionEventListener | None = None,
        run_factory: RunFactory | None = None,
        clock: SystemClock | None = None,
        id_factory: UUIDFactory | None = None,
    ) -> None:
        self._kernel_factory = kernel_factory
        self._run_store = run_store
        self._epoch_store = epoch_store
        self._listener = listener
        self._clock = clock or SystemClock()
        self._id_factory = id_factory or UUIDFactory()
        self._run_factory = run_factory or DefaultRunFactory(
            clock=self._clock, id_factory=self._id_factory
        )
        self._sessions: dict[str, RoomAgentSession] = {}

    async def create_session(
        self,
        *,
        room_id: str,
        profile: OrchestratorProfile,
        candidate_scope: CandidateScopeSnapshot,
        requesting_subject_id: str,
        frozen_catalog: FrozenToolCatalogSnapshot,
        resource_manifest: RunResourceManifestSnapshot | None = None,
        run_factory: RunFactory | None = None,
    ) -> RoomAgentSession:
        if room_id in self._sessions:
            raise SessionConflict("a session is already active for this Room")
        epoch = await self._epoch_store.read_active(room_id)
        if epoch is None:
            raise SessionConflict("Room epoch is not active")
        config = RoomAgentSessionConfig(
            session_id=f"room:{room_id}:epoch:{epoch.epoch}",
            room_id=room_id,
            profile=profile,
            candidate_scope=candidate_scope,
            room_epoch=epoch.epoch,
            requesting_subject_id=requesting_subject_id,
            tool_catalog=FrozenToolCatalog(frozen_catalog),
            frozen_tool_catalog=frozen_catalog,
            resource_manifest=resource_manifest,
        )
        lifecycle = LifecycleEmitter()
        if self._listener is not None:
            lifecycle.subscribe(self._listener)
        session = RoomAgentSession(
            config=config,
            kernel=self._kernel_factory(frozen_catalog),
            run_store=self._run_store,
            run_factory=run_factory or self._run_factory,
            lifecycle=lifecycle,
            clock=self._clock,
        )
        self._sessions[room_id] = session
        return session

    def get_session(self, room_id: str) -> RoomAgentSession | None:
        return self._sessions.get(room_id)

    def drop_session(self, room_id: str) -> None:
        self._sessions.pop(room_id, None)

    async def prompt(
        self,
        room_id: str,
        message: UserMessage,
        *,
        client_request_id: str | None = None,
    ) -> KernelRunResult:
        return await self._require_session(room_id).prompt(
            message, client_request_id=client_request_id
        )

    async def continue_run(self, room_id: str) -> KernelRunResult:
        return await self._require_session(room_id).continue_run()

    async def observe_tool(
        self, room_id: str, observation: ToolObservation
    ) -> KernelRunResult:
        return await self._require_session(room_id).observe_tool(observation)

    async def abort(self, room_id: str) -> None:
        await self._require_session(room_id).abort()

    def observation_sink(self) -> RunAddressedToolObservationSink:
        """Re-entry surface for recovery workers without a session object."""

        def kernel_for_run(run) -> OrchestratorKernel:
            if run.tool_catalog is None:
                raise SessionConflict("Run has no frozen tool catalog")
            return self._kernel_factory(run.tool_catalog)

        return RunAddressedToolObservationSink(
            run_store=self._run_store,
            kernel_factory=kernel_for_run,
            signal_factory=EventCancellationSignal,
        )

    async def shutdown(self) -> None:
        """Cancel every in-process session task without persisting terminal state.

        This is the graceful-shutdown surface: the asyncio tasks are cancelled
        directly so Runs stay non-terminal and are re-entered by the recovery
        workers (plan 2.3). ``RoomAgentSession.abort`` is the user-facing
        cancellation path and persists ``canceled``; do not call it here.
        """
        for session in list(self._sessions.values()):
            await session.shutdown()
        self._sessions.clear()

    def _require_session(self, room_id: str) -> RoomAgentSession:
        session = self._sessions.get(room_id)
        if session is None:
            raise SessionConflict("no active session for this Room")
        return session


__all__ = [
    "KernelForCatalog",
    "RoomSessionHost",
]
