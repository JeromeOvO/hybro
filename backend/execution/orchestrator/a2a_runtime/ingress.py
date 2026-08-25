"""Authenticated durable observation ingress and restart-safe process manager."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from pydantic import ValidationError

from common.dto.hitl import A2AInteractionSpec

from ..kernel import KernelConflict
from ..models import TextPart, ToolObservation, ToolSuspension
from ..ports import InvocationCheckpointReader, InvocationOutcomeCheckpointReader
from .errors import RecoverableAdapterError, RecoverableCheckpointError
from .ledger import (
    TERMINAL_AGENT_CALL_STATES,
    ConflictingTerminalObservation,
    apply_observation,
    transition_call,
)
from .models import (
    A2AObservationConflictRecord,
    A2AObservationInboxRecord,
    A2ARuntimePolicy,
    AgentCallLedgerRecord,
    NormalizedA2AObservation,
)
from .ports import (
    AgentCallLedgerStore,
    HITLApplicationPort,
    NormalizedObservationRecorder,
    ObservationConflictStore,
    ObservationInboxStore,
    ObservationIngressAuthenticator,
    ResourceMaterializerPort,
    RoomEpochStore,
    ToolObservationSink,
)
from .terminal_interactions import TerminalInteractionFinalizer

ObservationNormalizer = Callable[
    [bytes], NormalizedA2AObservation | Awaitable[NormalizedA2AObservation]
]


class ObservationIngressError(ValueError):
    pass


class _ObservationEpochConsumed(Exception):
    pass


class RejectExternalIngressAuthenticator:
    """Safe default for compositions that only record trusted internal evidence."""

    async def authenticate(self, **_: object) -> str:
        raise PermissionError("external observation ingress is not configured")


class A2AObservationIngress(NormalizedObservationRecorder):
    def __init__(
        self,
        *,
        inbox: ObservationInboxStore,
        conflicts: ObservationConflictStore,
        ledger: AgentCallLedgerStore,
        authenticator: ObservationIngressAuthenticator,
        policy: A2ARuntimePolicy | None = None,
    ) -> None:
        self.inbox = inbox
        self.conflicts = conflicts
        self.ledger = ledger
        self.authenticator = authenticator
        self.policy = policy or A2ARuntimePolicy()

    async def ingest(
        self,
        *,
        source_kind: str,
        headers: dict[str, str],
        body: bytes,
        normalize: ObservationNormalizer,
    ) -> tuple[str, A2AObservationInboxRecord]:
        """Authenticated external ingress. Trusted internal evidence uses record()."""
        await self.authenticator.authenticate(
            source_kind=source_kind, headers=headers, body=body
        )
        if len(body) > self.policy.max_callback_body_bytes:
            raise ObservationIngressError("callback body exceeds configured limit")
        normalized = normalize(body)
        observation = (
            await normalized if inspect.isawaitable(normalized) else normalized
        )
        if observation.source_kind != source_kind:
            raise ObservationIngressError("source kind does not correlate")
        return await self.record(observation)

    async def record(  # noqa: C901
        self, observation: NormalizedA2AObservation
    ) -> tuple[str, A2AObservationInboxRecord]:
        """Record trusted direct/poll/inspection evidence before applying it."""
        payload = observation.model_dump_json()
        if len(payload.encode()) > self.policy.max_normalized_observation_bytes:
            raise ObservationIngressError(
                "normalized observation exceeds configured limit"
            )
        payload_digest = sha256(payload.encode()).hexdigest()
        call = await self._resolve_lineage(observation)
        if call is None:
            # Unresolved evidence is rejected before persistence so it cannot become
            # private durable data that evades Room-epoch cleanup.
            raise ObservationIngressError("observation lineage is unresolved")
        record = A2AObservationInboxRecord(
            observation_id=observation.observation_id,
            source_kind=observation.source_kind,
            source_identity=observation.source_identity,
            payload_digest=payload_digest,
            received_at=datetime.now(UTC),
            binding_scope=observation.binding_scope,
            room_id=call.room_id,
            room_epoch=call.room_epoch,
            call_record_id=call.call_record_id,
            task_id=observation.task_id,
            context_id=observation.context_id,
            agent_id=observation.agent_id,
            event_kind=observation.event_kind,
            observation=observation,
        )
        existing = await self.inbox.load_by_source_identity(observation.source_identity)
        outcome = await self.inbox.insert(record)
        if outcome == "conflict":
            if existing is None:
                existing = await self.inbox.load_by_source_identity(
                    observation.source_identity
                )
            if existing is None:
                raise ObservationIngressError("observation identity conflict")
            conflict = A2AObservationConflictRecord(
                conflict_id=_stable(
                    "observation-conflict",
                    observation.source_identity,
                    existing.payload_digest,
                    payload_digest,
                ),
                room_id=existing.room_id,
                room_epoch=existing.room_epoch,
                source_identity=observation.source_identity,
                accepted_observation_id=existing.observation_id,
                accepted_payload_digest=existing.payload_digest,
                conflicting_payload_digest=payload_digest,
                binding_scope=observation.binding_scope,
                received_at=datetime.now(UTC),
            )
            conflict_outcome = await self.conflicts.insert(conflict)
            if conflict_outcome not in {"accepted", "replayed"}:
                raise ObservationIngressError("conflict audit persistence failed")
            return "conflict", existing
        if outcome == "replayed":
            if existing is None:
                existing = await self.inbox.load(record.observation_id)
            if existing is None:
                raise ObservationIngressError("replayed observation is missing")
            return outcome, existing
        if outcome != "accepted":
            raise ObservationIngressError(f"observation persistence failed: {outcome}")
        return outcome, record

    async def _resolve_lineage(
        self, observation: NormalizedA2AObservation
    ) -> AgentCallLedgerRecord | None:
        call = (
            await self.ledger.load_by_record_id(observation.call_record_id)
            if observation.call_record_id is not None
            else await self.ledger.find_by_alias(
                observation.binding_scope,
                task_id=observation.task_id,
                context_id=observation.context_id,
            )
        )
        if call is None or call.endpoint_scope_digest != observation.binding_scope:
            return None
        if observation.call_record_id not in {None, call.call_record_id}:
            return None
        if observation.agent_id not in {None, call.agent_id}:
            return None
        if call.a2a_task_id is not None and observation.task_id not in {
            None,
            call.a2a_task_id,
        }:
            return None
        if call.a2a_context_id is not None and observation.context_id not in {
            None,
            call.a2a_context_id,
        }:
            return None
        return call

    async def mark_executor_outcome(
        self,
        observation_id: str,
        *,
        outcome_digest: str,
    ) -> None:
        record = await self.inbox.load(observation_id)
        if record is None:
            raise KeyError(observation_id)
        if record.delivery_route not in {"unresolved", "executor"}:
            raise ObservationIngressError("observation already routed to sink")
        updated = record.model_copy(
            update={
                "state": "outcome_pending",
                "delivery_route": "executor",
                "delivery_state": "checkpointed",
                "outcome_digest": outcome_digest,
                "state_version": record.state_version + 1,
            }
        )
        outcome = await self.inbox.cas(
            updated, expected_state_version=record.state_version
        )
        if outcome not in {"accepted", "replayed"}:
            raise ObservationIngressError("executor outcome checkpoint failed")


class A2AObservationProcessor:
    def __init__(
        self,
        *,
        inbox: ObservationInboxStore,
        conflicts: ObservationConflictStore,
        ledger: AgentCallLedgerStore,
        room_epochs: RoomEpochStore,
        artifacts: ResourceMaterializerPort,
        hitl: HITLApplicationPort,
        sink: ToolObservationSink,
        checkpoint_reader: InvocationCheckpointReader,
        outcome_reader: InvocationOutcomeCheckpointReader,
        policy: A2ARuntimePolicy | None = None,
        worker_id: str = "a2a-observation",
    ) -> None:
        self.inbox = inbox
        self.conflicts = conflicts
        self.ledger = ledger
        self.room_epochs = room_epochs
        self.artifacts = artifacts
        self.hitl = hitl
        self.terminal_interactions = TerminalInteractionFinalizer(hitl)
        self.sink = sink
        self.checkpoint_reader = checkpoint_reader
        self.outcome_reader = outcome_reader
        self.policy = policy or A2ARuntimePolicy()
        self.worker_id = worker_id

    async def process(self, observation_id: str) -> str:
        try:
            return await self._process(observation_id)
        except RecoverableAdapterError:
            return "retryable"
        except _ObservationEpochConsumed:
            return "accepted"

    async def _process(self, observation_id: str) -> str:  # noqa: C901
        current = await self.inbox.load(observation_id)
        if current is None:
            raise KeyError(observation_id)
        if current.state in {"completed", "quarantined"}:
            return "replayed"
        now = datetime.now(UTC)
        token = _stable(
            "inbox-claim",
            observation_id,
            str(current.state_version),
            self.worker_id,
            now.isoformat(),
        )
        claimed = await self.inbox.claim(
            observation_id,
            expected_state_version=current.state_version,
            owner_id=self.worker_id,
            claim_token=token,
            lease_expires_at=now + timedelta(seconds=self.policy.claim_lease_seconds),
            claimed_at=now,
        )
        if claimed is None:
            return "conflict"
        record = await self._resolve_call(claimed)
        if record is None:
            return await self._release_inbox(claimed, state="pending")
        if (
            claimed.room_id != record.room_id
            or claimed.room_epoch != record.room_epoch
            or claimed.call_record_id != record.call_record_id
        ):
            return await self._release_inbox(claimed, state="quarantined")
        if claimed.delivery_route == "executor" and claimed.outcome_digest is not None:
            checkpointed = await self.outcome_reader.is_outcome_checkpointed(
                record.run_id,
                record.invocation_id,
                claimed.outcome_digest,
            )
            if record.state in TERMINAL_AGENT_CALL_STATES:
                await self._close_terminal_interaction_or_retry(claimed, record)
            if not checkpointed and record.state in TERMINAL_AGENT_CALL_STATES:
                # The live executor finished the call before the Run's
                # suspension was durably checkpointed, so the kernel never
                # received the result through the executor path. If the Run
                # is now suspended for this invocation, deliver through the
                # sink (kernel application is idempotent) instead of leaving
                # the row in outcome_pending forever.
                suspended = False
                for suspended_status in (
                    "waiting_external",
                    "input_required",
                    "auth_required",
                ):
                    if await self.checkpoint_reader.is_suspension_checkpointed(
                        record.run_id, record.invocation_id, suspended_status
                    ):
                        suspended = True
                        break
                if suspended:
                    try:
                        await self.sink.deliver(
                            record.run_id,
                            _to_tool_observation(record, claimed.observation),
                        )
                    except KernelConflict:
                        pass
                    checkpointed = await self.outcome_reader.is_outcome_checkpointed(
                        record.run_id,
                        record.invocation_id,
                        claimed.outcome_digest,
                    )
                    if checkpointed:
                        return await self._release_inbox(
                            claimed,
                            state="completed",
                            route="observation_sink",
                            delivery_state="completed",
                            outcome_digest=claimed.outcome_digest,
                        )
            return await self._release_inbox(
                claimed,
                state="completed" if checkpointed else "outcome_pending",
                route="executor",
                delivery_state="completed" if checkpointed else "checkpointed",
            )
        claimed = await self._renew_and_verify_epoch(claimed, record)
        if claimed is None:
            return "conflict"

        observation = claimed.observation
        interaction: A2AInteractionSpec | None = None
        fingerprint: str | None = None
        if observation.event_kind in {"input_required", "auth_required"}:
            try:
                if observation.interaction_spec is None:
                    raise ValueError("typed interaction metadata is missing")
                interaction = A2AInteractionSpec.model_validate(
                    observation.interaction_spec
                )
                fingerprint = _digest_json(interaction.model_dump(mode="json"))
            except (ValidationError, ValueError):
                observation = observation.model_copy(
                    update={
                        "event_kind": "terminal",
                        "status": "failed",
                        "content": [
                            TextPart(
                                text="The Agent returned invalid interaction metadata."
                            )
                        ],
                        "artifact_refs": [],
                        "interaction_spec": None,
                        "error_code": "invalid_interaction_metadata",
                        "error_message": "Agent interaction metadata was invalid.",
                    }
                )

        if observation.artifact_refs:
            artifact_refs = await self.artifacts.materialize_inbound_artifacts(
                call=record,
                artifact_refs=observation.artifact_refs,
                observation_id=observation.observation_id,
            )
            observation = observation.model_copy(
                update={"artifact_refs": artifact_refs}
            )
            claimed = await self._renew_and_verify_epoch(claimed, record)
            if claimed is None:
                return "conflict"
        interaction_already_attached = (
            interaction is not None
            and fingerprint is not None
            and record.state == observation.event_kind
            and record.pending_interaction_id == interaction.interaction_id
            and record.interaction_fingerprint == fingerprint
        )
        try:
            changed = (
                record
                if interaction_already_attached
                else apply_observation(
                    record,
                    observation,
                    recent_limit=self.policy.recent_observation_id_limit,
                )
            )
        except ConflictingTerminalObservation as conflict:
            await self._record_terminal_conflict(claimed, record, conflict)
            claimed = await self._renew_and_verify_epoch(claimed, record)
            if claimed is None:
                return "conflict"
            await self._close_terminal_interaction_or_retry(claimed, record)
            return await self._release_inbox(claimed, state="completed")
        if changed != record:
            try:
                winner = await self._cas_or_load_call_winner(
                    changed, expected_state_version=record.state_version
                )
            except RecoverableAdapterError:
                await self._release_inbox(claimed, state="pending")
                raise
            if winner != changed and winner.state not in TERMINAL_AGENT_CALL_STATES:
                return await self._release_inbox(claimed, state="pending")
            record = winner

        if record.state in TERMINAL_AGENT_CALL_STATES:
            await self._close_terminal_interaction_or_retry(claimed, record)
            if interaction is not None:
                await self._close_interaction_or_retry(
                    claimed,
                    interaction_id=interaction.interaction_id,
                    call_record_id=record.call_record_id,
                    terminal_state=record.state,
                )
                return await self._release_inbox(claimed, state="completed")

        if interaction_already_attached:
            claimed = await self._renew_and_verify_epoch(
                claimed,
                record,
                interaction_id=interaction.interaction_id,
            )
            if claimed is None:
                return "conflict"
            try:
                await self._activate_interaction(
                    interaction.interaction_id,
                    call_record_id=record.call_record_id,
                    interaction_fingerprint=fingerprint,
                )
            except RecoverableAdapterError:
                await self._release_inbox(claimed, state="pending")
                raise
            claimed = await self._renew_and_verify_epoch(
                claimed,
                record,
                interaction_id=interaction.interaction_id,
            )
            if claimed is None:
                return "conflict"
            try:
                activated_record = await self._reload_call_after_activation(
                    record.call_record_id
                )
            except RecoverableAdapterError:
                await self._release_inbox(claimed, state="pending")
                raise
            if activated_record.state in TERMINAL_AGENT_CALL_STATES:
                await self._close_terminal_interaction_or_retry(
                    claimed, activated_record
                )
                return await self._release_inbox(claimed, state="completed")
            if not _matches_attached_interaction(
                activated_record,
                event_kind=observation.event_kind,
                interaction_id=interaction.interaction_id,
                interaction_revision=1,
                interaction_fingerprint=fingerprint,
            ):
                return await self._release_inbox(claimed, state="pending")
            record = activated_record
            interaction = None
            fingerprint = None

        if interaction is not None and fingerprint is not None:
            claimed = await self._renew_and_verify_epoch(claimed, record)
            if claimed is None:
                return "conflict"
            interaction_id = await self.hitl.create_or_replay(
                call=record,
                interaction=interaction,
                interaction_fingerprint=fingerprint,
            )
            claimed = await self._renew_and_verify_epoch(claimed, record)
            if claimed is None:
                return "conflict"
            required = transition_call(
                record,
                to_state=claimed.observation.event_kind,
                updated_at=datetime.now(UTC),
                pending_interaction_id=interaction_id,
                interaction_revision=1,
                interaction_fingerprint=fingerprint,
            )
            try:
                winner = await self._cas_or_load_call_winner(
                    required, expected_state_version=record.state_version
                )
            except RecoverableAdapterError:
                await self._release_inbox(claimed, state="pending")
                raise
            if winner.state in TERMINAL_AGENT_CALL_STATES:
                await self._close_interaction_or_retry(
                    claimed,
                    interaction_id=interaction_id,
                    call_record_id=record.call_record_id,
                    terminal_state=winner.state,
                )
                return await self._release_inbox(claimed, state="completed")
            if winner != required:
                return await self._release_inbox(claimed, state="pending")
            record = winner
            claimed = await self._renew_and_verify_epoch(
                claimed, record, interaction_id=interaction_id
            )
            if claimed is None:
                return "conflict"
            try:
                await self._activate_interaction(
                    interaction_id,
                    call_record_id=record.call_record_id,
                    interaction_fingerprint=record.interaction_fingerprint or "",
                )
            except RecoverableAdapterError:
                await self._release_inbox(claimed, state="pending")
                raise
            claimed = await self._renew_and_verify_epoch(
                claimed, record, interaction_id=interaction_id
            )
            if claimed is None:
                return "conflict"
            try:
                activated_record = await self._reload_call_after_activation(
                    record.call_record_id
                )
            except RecoverableAdapterError:
                await self._release_inbox(claimed, state="pending")
                raise
            if activated_record.state in TERMINAL_AGENT_CALL_STATES:
                await self._close_terminal_interaction_or_retry(
                    claimed, activated_record
                )
                return await self._release_inbox(claimed, state="completed")
            if not _matches_attached_interaction(
                activated_record,
                event_kind=observation.event_kind,
                interaction_id=interaction_id,
                interaction_revision=1,
                interaction_fingerprint=fingerprint,
            ):
                return await self._release_inbox(claimed, state="pending")
            record = activated_record

        tool_observation = _to_tool_observation(record, observation)
        suspension_checkpointed = (
            await self.checkpoint_reader.is_suspension_checkpointed(
                record.run_id, record.invocation_id, "waiting_external"
            )
        )
        claimed = await self._renew_and_verify_epoch(claimed, record)
        if claimed is None:
            return "conflict"
        if suspension_checkpointed:
            await self.sink.deliver(record.run_id, tool_observation)
            claimed = await self._renew_and_verify_epoch(claimed, record)
            if claimed is None:
                return "conflict"
            processed = await self.outcome_reader.has_processed_observation(
                record.run_id, record.invocation_id, observation.observation_id
            )
            if not processed:
                return await self._release_inbox(
                    claimed,
                    state="session_applied",
                    route="observation_sink",
                    outcome_digest=_digest_json(
                        tool_observation.model_dump(mode="json")
                    ),
                )
            return await self._release_inbox(
                claimed,
                state="completed",
                route="observation_sink",
                delivery_state="completed",
                outcome_digest=_digest_json(tool_observation.model_dump(mode="json")),
            )
        return await self._release_inbox(
            claimed,
            state="outcome_pending",
            route="executor",
            outcome_digest=record.terminal_result_digest,
        )

    async def defer_poison(
        self, observation_id: str, *, error: str, now: datetime
    ) -> str:
        current = await self.inbox.load(observation_id)
        if current is None:
            return "error"
        if current.state != "claimed" or current.claim_owner != self.worker_id:
            return "conflict"
        quarantine = current.attempt_count >= self.policy.max_transport_attempts
        backoff = min(
            self.policy.retry_backoff_initial_seconds
            * (2 ** max(current.attempt_count - 1, 0)),
            self.policy.retry_backoff_max_seconds,
        )
        return await self._release_inbox(
            current,
            state="quarantined" if quarantine else "pending",
            next_attempt_at=None if quarantine else now + timedelta(seconds=backoff),
            last_error=error[:500],
        )

    async def _activate_interaction(
        self,
        interaction_id: str,
        *,
        call_record_id: str,
        interaction_fingerprint: str,
    ) -> None:
        activated = await self.hitl.activate(
            interaction_id,
            call_record_id=call_record_id,
            interaction_fingerprint=interaction_fingerprint,
        )
        if activated not in {"accepted", "replayed"}:
            raise RecoverableCheckpointError(
                "attached HITL aggregate could not be activated"
            )

    async def _reload_call_after_activation(
        self, call_record_id: str
    ) -> AgentCallLedgerRecord:
        record = await self.ledger.load_by_record_id(call_record_id)
        if record is None:
            raise RecoverableCheckpointError(
                "activated HITL call authority could not be classified"
            )
        return record

    async def _close_terminal_interaction_or_retry(
        self,
        claimed: A2AObservationInboxRecord,
        record: AgentCallLedgerRecord,
    ) -> None:
        interaction_id = record.pending_interaction_id
        if interaction_id is None:
            return
        await self._close_interaction_or_retry(
            claimed,
            interaction_id=interaction_id,
            call_record_id=record.call_record_id,
            terminal_state=record.state,
        )

    async def _close_interaction_or_retry(
        self,
        claimed: A2AObservationInboxRecord,
        *,
        interaction_id: str,
        call_record_id: str,
        terminal_state: str,
    ) -> None:
        try:
            await self.terminal_interactions.finalize_interaction(
                interaction_id=interaction_id,
                call_record_id=call_record_id,
                terminal_state=terminal_state,
            )
        except RecoverableAdapterError:
            await self._release_inbox(claimed, state="pending")
            raise

    async def _cas_or_load_call_winner(
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
        winner = await self.ledger.load_by_record_id(candidate.call_record_id)
        if winner is None:
            raise RecoverableCheckpointError(
                "observation call CAS winner could not be classified"
            )
        return winner

    async def _resolve_call(
        self, claimed: A2AObservationInboxRecord
    ) -> AgentCallLedgerRecord | None:
        if claimed.call_record_id is not None:
            record = await self.ledger.load_by_record_id(claimed.call_record_id)
            if record is None or record.endpoint_scope_digest != claimed.binding_scope:
                return None
            return record
        return await self.ledger.find_by_alias(
            claimed.binding_scope,
            task_id=claimed.task_id,
            context_id=claimed.context_id,
        )

    async def _renew_and_verify_epoch(
        self,
        claimed: A2AObservationInboxRecord,
        record: AgentCallLedgerRecord,
        *,
        interaction_id: str | None = None,
    ) -> A2AObservationInboxRecord | None:
        now = datetime.now(UTC)
        renewed = await self.inbox.renew(
            claimed.observation_id,
            expected_state_version=claimed.state_version,
            owner_id=self.worker_id,
            claim_token=claimed.claim_token or "",
            lease_expires_at=now + timedelta(seconds=self.policy.claim_lease_seconds),
            renewed_at=now,
        )
        if renewed is None:
            return None
        if not await self.room_epochs.verify_active(record.room_id, record.room_epoch):
            try:
                if interaction_id is not None:
                    await self.terminal_interactions.finalize_interaction(
                        interaction_id=interaction_id,
                        call_record_id=record.call_record_id,
                        terminal_state="room_epoch_inactive",
                    )
                else:
                    await self.terminal_interactions.finalize(record)
            except RecoverableAdapterError:
                await self._release_inbox(renewed, state="pending")
                raise
            await self._release_inbox(renewed, state="completed")
            raise _ObservationEpochConsumed
        return renewed

    async def _record_terminal_conflict(
        self,
        claimed: A2AObservationInboxRecord,
        record: AgentCallLedgerRecord,
        conflict: ConflictingTerminalObservation,
    ) -> None:
        audit = A2AObservationConflictRecord(
            conflict_id=_stable(
                "terminal-conflict",
                record.call_record_id,
                claimed.observation_id,
                conflict.persisted_digest,
                conflict.conflicting_digest,
            ),
            room_id=record.room_id,
            room_epoch=record.room_epoch,
            source_identity=claimed.source_identity,
            accepted_observation_id=(
                record.recent_observation_ids[-1]
                if record.recent_observation_ids
                else claimed.observation_id
            ),
            accepted_payload_digest=conflict.persisted_digest,
            conflicting_payload_digest=conflict.conflicting_digest,
            binding_scope=claimed.binding_scope,
            received_at=datetime.now(UTC),
        )
        outcome = await self.conflicts.insert(audit)
        if outcome not in {"accepted", "replayed"}:
            raise ObservationIngressError("terminal conflict audit persistence failed")

    async def _release_inbox(
        self,
        record: A2AObservationInboxRecord,
        *,
        state: str,
        route: str | None = None,
        delivery_state: str | None = None,
        outcome_digest: str | None = None,
        next_attempt_at: datetime | None = None,
        last_error: str | None = None,
    ) -> str:
        updated = record.model_copy(
            update={
                "state": state,
                "delivery_route": route or record.delivery_route,
                "delivery_state": delivery_state or record.delivery_state,
                "outcome_digest": outcome_digest or record.outcome_digest,
                "claim_owner": None,
                "claim_token": None,
                "claim_expires_at": None,
                "next_attempt_at": next_attempt_at,
                "last_error": last_error,
                "state_version": record.state_version + 1,
            }
        )
        return await self.inbox.cas(
            updated,
            expected_state_version=record.state_version,
            owner_id=self.worker_id,
            claim_token=record.claim_token,
        )


def _matches_attached_interaction(
    record: AgentCallLedgerRecord,
    *,
    event_kind: str,
    interaction_id: str,
    interaction_revision: int,
    interaction_fingerprint: str,
) -> bool:
    return (
        record.state == event_kind
        and record.pending_interaction_id == interaction_id
        and record.interaction_revision == interaction_revision
        and record.interaction_fingerprint == interaction_fingerprint
    )


def _to_tool_observation(
    record: AgentCallLedgerRecord, observation: NormalizedA2AObservation
) -> ToolObservation:
    if record.terminal_result is not None:
        outcome = record.terminal_result
    else:
        status = (
            observation.event_kind
            if observation.event_kind in {"input_required", "auth_required"}
            else "waiting_external"
        )
        outcome = ToolSuspension(invocation_id=record.invocation_id, status=status)
    return ToolObservation(
        observation_id=observation.observation_id,
        invocation_id=record.invocation_id,
        outcome=outcome,
        observed_at=observation.observed_at,
    )


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()


def _stable(prefix: str, *parts: str) -> str:
    return f"{prefix}-{_digest_json([*parts])}"
