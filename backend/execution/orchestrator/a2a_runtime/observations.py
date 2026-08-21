"""Run-addressed generic observation delivery without process-local session state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..kernel import OrchestratorKernel
from ..models import OrchestratorRunState, ToolObservation
from ..ports import CancellationSignal, OrchestratorRunStore

KernelFactory = Callable[
    [OrchestratorRunState], OrchestratorKernel | Awaitable[OrchestratorKernel]
]
SignalFactory = Callable[[], CancellationSignal]


class RunAddressedToolObservationSink:
    def __init__(
        self,
        *,
        run_store: OrchestratorRunStore,
        kernel_factory: KernelFactory,
        signal_factory: SignalFactory,
    ) -> None:
        self.run_store = run_store
        self.kernel_factory = kernel_factory
        self.signal_factory = signal_factory

    async def deliver(self, run_id: str, observation: ToolObservation) -> None:
        run = await self.run_store.load(run_id)
        if run is None:
            raise KeyError(run_id)
        built = self.kernel_factory(run)
        kernel = await built if hasattr(built, "__await__") else built
        await kernel.observe_tool(run_id, observation, signal=self.signal_factory())
