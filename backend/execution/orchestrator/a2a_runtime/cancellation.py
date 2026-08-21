"""Durable-first external A2A call cancellation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from ..models import TextPart
from .errors import (
    AmbiguousRemoteEffectError,
    RecoverableAdapterError,
    RecoverableCheckpointError,
    RecoverableTransportError,
)
from .ledger import TERMINAL_AGENT_CALL_STATES, apply_observation, transition_call
from .models import (
    A2ACancellationCommand,
    A2ARuntimePolicy,
    AgentCallLedgerRecord,
    NormalizedA2AObservation,
)
from .ports import (
    A2ADispatchPort,
    AgentCallLedgerStore,
    HITLApplicationPort,
    NormalizedObservationRecorder,
    RoomEpochStore,
)
from .terminal_interactions import TerminalInteractionFinalizer


class A2ACancellationCoordinator:
    def __init__(
        self,
        *,
        ledger: AgentCallLedgerStore,
        room_epochs: RoomEpochStore,
        dispatch: A2ADispatchPort,
        observations: NormalizedObservationRecorder,
        hitl: HITLApplicationPort,
        policy: A2ARuntimePolicy | None = None,
        worker_id: str = "a2a-cancellation",
    ) -> None:
        self.ledger = ledger
        self.room_epochs = room_epochs
        self.dispatch = dispatch
        self.observations = observations
        self.terminal_interactions = TerminalInteractionFinalizer(hitl)
        self.policy = policy or A2ARuntimePolicy()
        self.worker_id = worker_id

    async def cancel_call(
        self,
        *,
        call_record_id: str,
        reason: str,
        deletion_id: str | None = None,
    ) -> str:
        try:
            return await self._cancel_call(
                call_record_id=call_record_id,
                reason=reason,
                deletion_id=deletion_id,
            )
        except RecoverableAdapterError:
            return "cancel_pending"

    async def _cancel_call(
        self,
        *,
        call_record_id: str,
        reason: str,
        deletion_id: str | None = None,
    ) -> str:
        call = await self.ledger.load_by_record_id(call_record_id)
        if call is None:
            raise KeyError(call_record_id)
        if call.state in TERMINAL_AGENT_CALL_STATES:
            return await self._finalized_state(call)
        if call.state == "cancel_pending" and call.cancellation_command is not None:
            return await self.recover_call(call_record_id=call_record_id)
        if not await self._epoch_authorized(call, deletion_id):
            raise PermissionError("cancellation epoch fence rejected")
        claimed = await self._claim(call)
        if claimed is None:
            return await self._load_finalized_state(call_record_id)
        command_id = f"cancel-{_stable([claimed.call_record_id, reason[:1000], deletion_id or 'active'])}"
        command = A2ACancellationCommand(
            command_id=command_id,
            transport_kind=claimed.transport_kind,
            call_record_id=claimed.call_record_id,
            reason=reason[:1000],
            deletion_id=deletion_id,
            created_at=datetime.now(UTC),
        )
        pending = transition_call(
            claimed,
            to_state="cancel_pending",
            updated_at=datetime.now(UTC),
            cancellation_command=command,
            cancellation_command_id=command_id,
            cancellation_reason=reason[:1000],
            cancellation_state="pending",
        )
        persisted = await self._cas_or_load_winner(
            pending, expected_state_version=claimed.state_version
        )
        if (
            persisted.cancellation_command != command
            or persisted.state in TERMINAL_AGENT_CALL_STATES
        ):
            return await self._finalized_state(persisted)
        return await self._deliver(persisted, inspect=False)

    async def recover_call(self, *, call_record_id: str) -> str:
        try:
            return await self._recover_call(call_record_id=call_record_id)
        except RecoverableAdapterError:
            return "cancel_pending"

    async def _recover_call(self, *, call_record_id: str) -> str:
        call = await self.ledger.load_by_record_id(call_record_id)
        if call is None:
            raise KeyError(call_record_id)
        if call.state in TERMINAL_AGENT_CALL_STATES:
            return await self._finalized_state(call)
        if call.state != "cancel_pending" or call.cancellation_command is None:
            return call.state
        command = call.cancellation_command
        if not await self._epoch_authorized(call, command.deletion_id):
            return call.state
        claimed = await self._claim(call)
        if claimed is None:
            return await self._load_finalized_state(call_record_id)
        inspect = claimed.cancellation_state in {"dispatching", "delivery_uncertain"}
        return await self._deliver(claimed, inspect=inspect)

    async def _deliver(  # noqa: C901
        self, call: AgentCallLedgerRecord, *, inspect: bool
    ) -> str:
        command = call.cancellation_command
        if command is None:
            await self._release(call)
            return "cancel_pending"
        if (
            inspect
            and call.cancellation_attempts
            >= call.runtime_policy.max_uncertain_inspection_attempts
        ):
            return await self._expire(call)
        call_record_id = call.call_record_id
        call = await self._renew_and_verify(call, command.deletion_id)
        if call is None:
            return await self._load_finalized_state(call_record_id)
        dispatching = call.model_copy(
            update={
                "cancellation_state": (
                    "delivery_uncertain" if inspect else "dispatching"
                ),
                "cancellation_attempts": call.cancellation_attempts + 1,
                "state_version": call.state_version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        persisted = await self._cas_or_load_winner(
            dispatching, expected_state_version=call.state_version
        )
        if persisted != dispatching:
            return await self._finalized_state(persisted)
        call = persisted
        try:
            receipt = (
                await self.dispatch.inspect_cancellation(command)
                if inspect
                else await self.dispatch.cancel(command)
            )
        except (
            RecoverableAdapterError,
            RecoverableTransportError,
            AmbiguousRemoteEffectError,
            TimeoutError,
        ):
            renewed = await self._renew_and_verify(call, command.deletion_id)
            if renewed is None:
                return await self._load_finalized_state(call_record_id)
            return await self._mark_uncertain(renewed)
        call = await self._renew_and_verify(call, command.deletion_id)
        if call is None:
            return await self._load_finalized_state(call_record_id)
        if receipt.outcome not in {"accepted", "terminal"}:
            return await self._mark_uncertain(call)
        observation = receipt.terminal_observation or _canceled_observation(
            call, command
        )
        await self.observations.record(observation)
        call = await self._renew_and_verify(call, command.deletion_id)
        if call is None:
            return await self._load_finalized_state(call_record_id)
        terminal = apply_observation(
            call,
            observation,
            recent_limit=call.runtime_policy.recent_observation_id_limit,
        )
        persisted = await self._cas_or_load_winner(
            terminal, expected_state_version=call.state_version
        )
        return await self._finalized_state(persisted)

    async def _expire(self, call: AgentCallLedgerRecord) -> str:
        command = call.cancellation_command
        assert command is not None
        observation = NormalizedA2AObservation(
            observation_id=f"cancellation-expired-{command.command_id}",
            call_record_id=call.call_record_id,
            source_kind="inspection",
            source_identity=f"cancellation-expired:{command.command_id}",
            binding_scope=call.endpoint_scope_digest,
            event_kind="terminal",
            observed_at=datetime.now(UTC),
            task_id=call.a2a_task_id,
            context_id=call.a2a_context_id,
            status="expired",
            content=[TextPart(text="The Agent cancellation could not be reconciled.")],
            error_code="cancellation_uncertainty_exhausted",
            error_message="Cancellation delivery could not be reconciled.",
        )
        await self.observations.record(observation)
        renewed = await self._renew_and_verify(call, command.deletion_id)
        if renewed is None:
            return await self._load_finalized_state(call.call_record_id)
        expired = apply_observation(
            renewed,
            observation,
            recent_limit=renewed.runtime_policy.recent_observation_id_limit,
        )
        persisted = await self._cas_or_load_winner(
            expired, expected_state_version=renewed.state_version
        )
        return await self._finalized_state(persisted)

    async def _mark_uncertain(self, call: AgentCallLedgerRecord) -> str:
        uncertain = call.model_copy(
            update={
                "cancellation_state": "delivery_uncertain",
                "claim_owner": None,
                "claim_expires_at": None,
                "next_attempt_at": datetime.now(UTC)
                + timedelta(seconds=self.policy.retry_backoff_initial_seconds),
                "state_version": call.state_version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        persisted = await self._cas_or_load_winner(
            uncertain, expected_state_version=call.state_version
        )
        return await self._finalized_state(persisted)

    async def _finalized_state(self, record: AgentCallLedgerRecord) -> str:
        if record.state in TERMINAL_AGENT_CALL_STATES:
            await self.terminal_interactions.finalize(record)
        return record.state

    async def _load_finalized_state(self, call_record_id: str) -> str:
        return await self._finalized_state(
            await self._load_durable_winner(call_record_id)
        )

    async def _cas_or_load_winner(
        self,
        candidate: AgentCallLedgerRecord,
        *,
        expected_state_version: int,
    ) -> AgentCallLedgerRecord:
        outcome = await self.ledger.cas(
            candidate, expected_state_version=expected_state_version
        )
        if outcome in {"accepted", "replayed"}:
            return candidate
        return await self._load_durable_winner(candidate.call_record_id)

    async def _load_durable_winner(self, call_record_id: str) -> AgentCallLedgerRecord:
        winner = await self.ledger.load_by_record_id(call_record_id)
        if winner is None:
            raise RecoverableCheckpointError(
                "cancellation CAS winner could not be classified"
            )
        return winner

    async def _epoch_authorized(
        self, call: AgentCallLedgerRecord, deletion_id: str | None
    ) -> bool:
        if deletion_id is None:
            return await self.room_epochs.verify_active(call.room_id, call.room_epoch)
        return await self.room_epochs.verify_cleanup_epoch(
            call.room_id, call.room_epoch, deletion_id
        )

    async def _claim(self, call: AgentCallLedgerRecord) -> AgentCallLedgerRecord | None:
        now = datetime.now(UTC)
        return await self.ledger.claim(
            call.call_record_id,
            expected_state_version=call.state_version,
            owner_id=self.worker_id,
            lease_expires_at=now + timedelta(seconds=self.policy.claim_lease_seconds),
            claimed_at=now,
        )

    async def _renew_and_verify(
        self, call: AgentCallLedgerRecord, deletion_id: str | None
    ) -> AgentCallLedgerRecord | None:
        now = datetime.now(UTC)
        renewed = await self.ledger.renew(
            call.call_record_id,
            expected_state_version=call.state_version,
            owner_id=self.worker_id,
            lease_expires_at=now + timedelta(seconds=self.policy.claim_lease_seconds),
            renewed_at=now,
        )
        if renewed is None or not await self._epoch_authorized(renewed, deletion_id):
            return None
        return renewed

    async def _release(self, call: AgentCallLedgerRecord) -> None:
        await self.ledger.release(
            call.call_record_id,
            expected_state_version=call.state_version,
            owner_id=self.worker_id,
            next_attempt_at=datetime.now(UTC),
            released_at=datetime.now(UTC),
        )

    async def cancel_run(
        self, run_id: str, *, reason: str, deletion_id: str | None = None
    ) -> dict[str, str]:
        results: dict[str, str] = {}
        for call in await self.ledger.list_for_run(run_id):
            results[call.call_record_id] = await self.cancel_call(
                call_record_id=call.call_record_id,
                reason=reason,
                deletion_id=deletion_id,
            )
        return results


def _canceled_observation(
    call: AgentCallLedgerRecord, command: A2ACancellationCommand
) -> NormalizedA2AObservation:
    return NormalizedA2AObservation(
        observation_id=f"cancel-observation-{command.command_id}",
        call_record_id=call.call_record_id,
        source_kind="inspection",
        source_identity=f"cancel:{command.command_id}",
        binding_scope=call.endpoint_scope_digest,
        event_kind="terminal",
        status="canceled",
        observed_at=datetime.now(UTC),
        task_id=call.a2a_task_id,
        context_id=call.a2a_context_id,
        content=[TextPart(text="The Agent call was canceled.")],
        error_code="canceled",
        error_message=command.reason[:500],
    )


def _stable(parts: list[str]) -> str:
    canonical = json.dumps(parts, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode()).hexdigest()
