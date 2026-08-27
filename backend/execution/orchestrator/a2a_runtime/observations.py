"""Run-addressed generic observation delivery without process-local session state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from ..kernel import KernelLifecycle, OrchestratorKernel
from ..lifecycle import LifecycleEmitter, SessionEvent, SessionEventListener
from ..models import OrchestratorRunState, ToolObservation
from ..ports import CancellationSignal, OrchestratorRunStore

KernelFactory = Callable[
    [OrchestratorRunState], OrchestratorKernel | Awaitable[OrchestratorKernel]
]
SignalFactory = Callable[[], CancellationSignal]
LifecycleFactory = Callable[[OrchestratorRunState], KernelLifecycle | None]


class RunAddressedToolObservationSink:
    def __init__(
        self,
        *,
        run_store: OrchestratorRunStore,
        kernel_factory: KernelFactory,
        signal_factory: SignalFactory,
        lifecycle_factory: LifecycleFactory | None = None,
        listener: SessionEventListener | None = None,
    ) -> None:
        self.run_store = run_store
        self.kernel_factory = kernel_factory
        self.signal_factory = signal_factory
        self.lifecycle_factory = lifecycle_factory
        self.listener = listener

    async def deliver(self, run_id: str, observation: ToolObservation) -> None:
        run = await self.run_store.load(run_id)
        if run is None:
            raise KeyError(run_id)
        built = self.kernel_factory(run)
        kernel = await built if hasattr(built, "__await__") else built
        lifecycle = self.lifecycle_factory(run) if self.lifecycle_factory else None

        if lifecycle is None and self.listener is not None:
            emitter = LifecycleEmitter()
            emitter.subscribe(self.listener)
            sequence = 0

            async def emit_kernel_event(
                event_type: str,
                current_run: OrchestratorRunState,
                payload: dict[str, object],
            ) -> None:
                nonlocal sequence
                sequence += 1
                await emitter.emit(
                    SessionEvent(
                        event_type=event_type,  # type: ignore[arg-type]
                        session_id=current_run.session_id,
                        run_id=current_run.run_id,
                        causation_id=current_run.request.user_message_id,
                        sequence=sequence,
                        timestamp=datetime.now(UTC),
                        payload=payload or {"status": current_run.status},
                        room_id=current_run.room_id,
                        user_message_id=current_run.request.user_message_id,
                        client_request_id=current_run.client_request_id,
                    )
                )

            lifecycle = emit_kernel_event

        await kernel.observe_tool(
            run_id,
            observation,
            signal=self.signal_factory(),
            lifecycle=lifecycle,
        )
