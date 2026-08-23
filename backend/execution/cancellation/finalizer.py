from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from common.dto import RunInfo
from common.utils.cancellation import CancellationToken
from common.utils.time import utcnow
from execution.orchestration.run_store import (
    DuplicateEventIdConflict,
    OrchestrationRunStore,
    OrchestrationStoreConflict,
)
from models.orchestration import (
    TERMINAL_ORCHESTRATION_STATUSES,
    OrchestrationEventType,
    OrchestrationRunEvent,
    OrchestrationRunState,
    OrchestrationStatus,
)


class CancellationFinalizationConflict(RuntimeError):
    """Raised when a nonterminal run cannot be canceled after bounded retries."""


@dataclass(frozen=True)
class CancellationFinalizationResult:
    status: OrchestrationStatus
    cancellation_applied: bool
    reconciled: bool


class StatusProjectionPort(Protocol):
    async def __call__(
        self,
        *,
        room_id: str,
        message_id: str,
        status: OrchestrationStatus,
    ) -> bool: ...


class MessageCancellationPort(Protocol):
    async def __call__(self, message_id: str) -> object: ...


class ActiveTokenReaderPort(Protocol):
    def __call__(self, message_id: str) -> CancellationToken | None: ...


class ActiveTokenReleasePort(Protocol):
    def __call__(
        self,
        message_id: str,
        token: CancellationToken | None,
    ) -> bool: ...


class CancellationClearPort(Protocol):
    def __call__(self, message_id: str) -> None: ...


class PublicTerminalProjectionPort(Protocol):
    async def __call__(
        self,
        *,
        room_id: str,
        message_id: str,
        status: OrchestrationStatus,
    ) -> None: ...


class AgentTaskCleanupPort(Protocol):
    async def __call__(self, *, room_id: str, message_id: str) -> None: ...


class CancellationMarkerReconciliationPort(Protocol):
    async def __call__(self, message_id: str) -> bool: ...


class PublicRunReaderPort(Protocol):
    async def __call__(self, run_id: str) -> RunInfo | None: ...


class CancellationFinalizationPort(Protocol):
    async def finalize(
        self,
        *,
        room_id: str,
        message_id: str,
        settle_no_run: bool = False,
    ) -> CancellationFinalizationResult: ...


class CancellationFinalizer:
    """Idempotently reconcile one durable cancellation marker across all surfaces."""

    def __init__(
        self,
        *,
        run_store: OrchestrationRunStore | None,
        project_status: StatusProjectionPort,
        broadcast_cancellation: MessageCancellationPort,
        get_active_token: ActiveTokenReaderPort,
        release_active_token: ActiveTokenReleasePort,
        clear_cancellation: CancellationClearPort,
        cancel_hitl: MessageCancellationPort,
        project_public_terminal: PublicTerminalProjectionPort,
        cleanup_agent_tasks: AgentTaskCleanupPort,
        mark_reconciled: CancellationMarkerReconciliationPort,
        get_public_run: PublicRunReaderPort,
    ) -> None:
        self._run_store = run_store
        self._project_status = project_status
        self._broadcast_cancellation = broadcast_cancellation
        self._get_active_token = get_active_token
        self._release_active_token = release_active_token
        self._clear_cancellation = clear_cancellation
        self._cancel_hitl = cancel_hitl
        self._project_public_terminal = project_public_terminal
        self._cleanup_agent_tasks = cleanup_agent_tasks
        self._mark_reconciled = mark_reconciled
        self._get_public_run = get_public_run

    async def _project_or_raise(
        self,
        *,
        room_id: str,
        message_id: str,
        status: OrchestrationStatus,
    ) -> None:
        if not await self._project_status(
            room_id=room_id,
            message_id=message_id,
            status=status,
        ):
            raise RuntimeError("orchestration projection failed")

    async def _mark_reconciled_or_raise(self, message_id: str) -> None:
        if not await self._mark_reconciled(message_id):
            raise RuntimeError("cancellation marker reconciliation failed")
        # Only the finalizer may clear L1, after the durable marker no longer
        # needs retry. Canceled execution paths must retain it while Redis
        # propagation is pending.
        self._clear_cancellation(message_id)

    @staticmethod
    def _propagation_succeeded(result: object) -> bool:
        if result is False:
            return False
        succeeded = getattr(result, "succeeded", True)
        return succeeded if isinstance(succeeded, bool) else True

    async def _broadcast_for_reconciliation(self, message_id: str) -> bool:
        try:
            result = await self._broadcast_cancellation(message_id)
        except Exception:
            # The durable marker remains pending so reconciliation retries both
            # Redis KV and Pub/Sub. Local terminal/HITL/task cleanup still proceeds.
            return False
        return self._propagation_succeeded(result)

    async def _project_preserved_terminal(
        self,
        *,
        room_id: str,
        message_id: str,
        status: OrchestrationStatus,
    ) -> None:
        await self._project_or_raise(
            room_id=room_id,
            message_id=message_id,
            status=status,
        )
        await self._project_public_terminal(
            room_id=room_id,
            message_id=message_id,
            status=status,
        )

    async def _settle_late_run(
        self,
        *,
        room_id: str,
        message_id: str,
    ) -> OrchestrationRunState | None:
        state = await self._terminalize_or_preserve(message_id)
        if state is not None and state.status != OrchestrationStatus.CANCELED:
            await self._project_preserved_terminal(
                room_id=room_id,
                message_id=message_id,
                status=state.status,
            )
        return state

    async def _public_terminal_status(
        self,
        message_id: str,
    ) -> OrchestrationStatus | None:
        run = await self._get_public_run(message_id)
        value = getattr(
            getattr(run, "state", None), "value", getattr(run, "state", None)
        )
        return {
            "completed": OrchestrationStatus.COMPLETED,
            "failed": OrchestrationStatus.FAILED,
            "canceled": OrchestrationStatus.CANCELED,
        }.get(value)

    async def _claim_public_cancellation(
        self,
        *,
        room_id: str,
        message_id: str,
    ) -> OrchestrationStatus | None:
        try:
            await self._project_public_terminal(
                room_id=room_id,
                message_id=message_id,
                status=OrchestrationStatus.CANCELED,
            )
            return None
        except Exception:
            public_status = await self._public_terminal_status(message_id)
            if (
                public_status is not None
                and public_status != OrchestrationStatus.CANCELED
            ):
                return public_status
            raise

    async def finalize(
        self,
        *,
        room_id: str,
        message_id: str,
        settle_no_run: bool = False,
    ) -> CancellationFinalizationResult:
        # Capture the owner before any cancellation await. A completed owner may
        # release itself and a resume may install a new token while finalization
        # is propagating; the final release must never steal that newer token.
        active_token = self._get_active_token(message_id)
        try:
            state = await self._terminalize_or_preserve(message_id)
        except CancellationFinalizationConflict:
            await self._broadcast_cancellation(message_id)
            raise
        if state is not None and state.status != OrchestrationStatus.CANCELED:
            await self._project_preserved_terminal(
                room_id=room_id,
                message_id=message_id,
                status=state.status,
            )
            await self._mark_reconciled_or_raise(message_id)
            return CancellationFinalizationResult(
                status=state.status,
                cancellation_applied=False,
                reconciled=True,
            )
        if state is None:
            public_status = await self._public_terminal_status(message_id)
            if (
                public_status is not None
                and public_status != OrchestrationStatus.CANCELED
            ):
                await self._project_or_raise(
                    room_id=room_id,
                    message_id=message_id,
                    status=public_status,
                )
                await self._mark_reconciled_or_raise(message_id)
                return CancellationFinalizationResult(
                    status=public_status,
                    cancellation_applied=False,
                    reconciled=True,
                )

        await self._project_or_raise(
            room_id=room_id,
            message_id=message_id,
            status=OrchestrationStatus.CANCELED,
        )
        public_winner = await self._claim_public_cancellation(
            room_id=room_id,
            message_id=message_id,
        )
        if public_winner is not None:
            await self._project_or_raise(
                room_id=room_id,
                message_id=message_id,
                status=public_winner,
            )
            await self._mark_reconciled_or_raise(message_id)
            return CancellationFinalizationResult(
                status=public_winner,
                cancellation_applied=False,
                reconciled=True,
            )

        propagation_succeeded = await self._broadcast_for_reconciliation(message_id)
        await self._cancel_hitl(message_id)
        await self._cleanup_agent_tasks(
            room_id=room_id,
            message_id=message_id,
        )

        if state is None and settle_no_run:
            state = await self._settle_late_run(
                room_id=room_id,
                message_id=message_id,
            )

        if state is not None and state.status != OrchestrationStatus.CANCELED:
            await self._mark_reconciled_or_raise(message_id)
            return CancellationFinalizationResult(
                status=state.status,
                cancellation_applied=False,
                reconciled=True,
            )

        # Re-scan after broadcasting cancellation so descendants inserted
        # concurrently with the first cleanup cannot escape reconciliation.
        await self._cleanup_agent_tasks(
            room_id=room_id,
            message_id=message_id,
        )

        reconciled = (state is not None or settle_no_run) and propagation_succeeded
        if reconciled:
            await self._mark_reconciled_or_raise(message_id)
        final_status = (
            state.status
            if state is not None and state.status in TERMINAL_ORCHESTRATION_STATUSES
            else OrchestrationStatus.CANCELED
        )
        if final_status == OrchestrationStatus.CANCELED:
            self._release_active_token(message_id, active_token)
        return CancellationFinalizationResult(
            status=final_status,
            cancellation_applied=final_status == OrchestrationStatus.CANCELED,
            reconciled=reconciled,
        )

    async def _terminalize_or_preserve(
        self,
        message_id: str,
    ) -> OrchestrationRunState | None:
        if self._run_store is None:
            return None
        for _attempt in range(3):
            current = await self._run_store.get_latest_by_user_message_id(message_id)
            if current is None:
                return None
            if current.status in TERMINAL_ORCHESTRATION_STATUSES:
                if current.status == OrchestrationStatus.CANCELED:
                    await self._ensure_terminal_event(current)
                return current

            canceled = current.model_copy(deep=True)
            canceled.status = OrchestrationStatus.CANCELED
            canceled.terminal_reason = "request canceled"
            canceled.state_version += 1
            canceled.updated_at = utcnow()
            canceled.pending_hitl_request_ids.clear()
            canceled.pending_agent_continuations.clear()
            for question in canceled.open_questions:
                if question.get("status") == "open":
                    question["status"] = "canceled"
            canceled.updated_at = utcnow()
            try:
                saved = await self._run_store.save_state(
                    canceled,
                    expected_version=current.state_version,
                )
            except OrchestrationStoreConflict:
                continue
            await self._ensure_terminal_event(saved)
            return saved
        raise CancellationFinalizationConflict(
            f"could not cancel nonterminal orchestration for {message_id}"
        )

    async def _ensure_terminal_event(self, state: OrchestrationRunState) -> None:
        if self._run_store is None:
            return
        try:
            await self._run_store.append_event(
                OrchestrationRunEvent(
                    event_id=(
                        f"{state.run_id}:run-terminal:canceled:{state.state_version}"
                    ),
                    run_id=state.run_id,
                    room_id=state.room_id,
                    type=OrchestrationEventType.RUN_TERMINAL,
                    state_version=state.state_version,
                    payload={
                        "status": OrchestrationStatus.CANCELED.value,
                        "reason": state.terminal_reason,
                    },
                )
            )
        except DuplicateEventIdConflict:
            return
