"""Narrow injected ports for the unbound orchestrator v3 contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal, Protocol

from .models import (
    A2AOwnershipRecord,
    ModelStreamEvent,
    ModelTurnRequest,
    OrchestratorEvent,
    OrchestratorRunState,
    ProjectionIntent,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

StoreOutcome = Literal["accepted", "replayed", "conflict", "error"]


class RunStoreResult(Protocol):
    @property
    def outcome(self) -> StoreOutcome: ...

    @property
    def run(self) -> OrchestratorRunState | None: ...


class CancellationSignal(Protocol):
    @property
    def cancelled(self) -> bool: ...

    async def wait(self) -> None: ...


class ModelRuntime(Protocol):
    def stream_turn(
        self,
        request: ModelTurnRequest,
        *,
        signal: CancellationSignal,
    ) -> AsyncIterator[ModelStreamEvent]: ...


class ToolRuntime(Protocol):
    async def execute(
        self,
        definition: ToolDefinition,
        call: ToolCall,
        *,
        signal: CancellationSignal,
    ) -> ToolResult: ...


class OrchestratorRunStore(Protocol):
    async def create(
        self, run: OrchestratorRunState, *, command_id: str
    ) -> RunStoreResult: ...

    async def load(self, run_id: str) -> OrchestratorRunState | None: ...

    async def cas_mutate(
        self,
        run: OrchestratorRunState,
        *,
        expected_state_version: int,
        command_id: str,
    ) -> RunStoreResult: ...

    async def claim_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        lease_expires_at: datetime,
    ) -> RunStoreResult: ...

    async def renew_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        lease_expires_at: datetime,
    ) -> RunStoreResult: ...

    async def release_recovery(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        next_attempt_at: datetime | None,
    ) -> RunStoreResult: ...

    async def list_due_runs(
        self, *, due_at: datetime, limit: int
    ) -> list[OrchestratorRunState]: ...

    async def claim_projection_intent(
        self,
        run_id: str,
        intent_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        lease_expires_at: datetime,
    ) -> RunStoreResult: ...

    async def complete_projection_intent(
        self,
        run_id: str,
        intent_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
    ) -> RunStoreResult: ...

    async def block_projection_intent(
        self,
        run_id: str,
        intent_id: str,
        *,
        expected_state_version: int,
        owner_id: str,
        reason: str,
    ) -> RunStoreResult: ...


class OrchestratorEventStore(Protocol):
    async def append(self, event: OrchestratorEvent) -> StoreOutcome: ...

    async def read(
        self, run_id: str, *, after_sequence: int = 0
    ) -> list[OrchestratorEvent]: ...


class A2AOwnershipLookup(Protocol):
    async def find_run_by_task_id(
        self, a2a_task_id: str
    ) -> A2AOwnershipRecord | None: ...

    async def find_run_by_context_id(
        self, a2a_context_id: str
    ) -> A2AOwnershipRecord | None: ...


class EventProjector(Protocol):
    async def project(self, intent: ProjectionIntent) -> StoreOutcome: ...
