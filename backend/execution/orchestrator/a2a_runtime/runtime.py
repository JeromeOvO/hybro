"""Two-phase ToolRuntime backed by the durable external A2A call ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from ..models import (
    AgentToolInput,
    TextPart,
    ToolAcceptance,
    ToolInvocation,
    ToolResult,
    ToolSuspension,
)
from ..ports import CancellationSignal, InvocationCheckpointReader
from .errors import (
    AmbiguousRemoteEffectError,
    RecoverableAdapterError,
    RecoverableAuthorizationError,
    RecoverableCheckpointError,
    RecoverableEpochError,
    RecoverableResourceError,
    RecoverableTransportError,
)
from .ledger import (
    apply_observation,
    bind_authoritative_aliases,
    ownership_alias_keys,
    transition_call,
)
from .models import A2ADispatchCommand, A2ARuntimePolicy, AgentCallLedgerRecord
from .ports import (
    A2ADispatchPort,
    AgentCallLedgerStore,
    AuthorizationRefreshPort,
    NormalizedObservationRecorder,
    PreparedInvocationSnapshotReader,
    ResourceMaterializerPort,
    RoomEpochStore,
)
from .resources import verify_materialized_digests
from .terminal_interactions import TerminalInteractionFinalizer


class A2AAcceptanceConflict(RuntimeError):
    pass


class A2AAcceptanceDenied(PermissionError):
    pass


class A2AAgentToolRuntime:
    def __init__(
        self,
        *,
        ledger: AgentCallLedgerStore,
        prepared_reader: PreparedInvocationSnapshotReader,
        checkpoint_reader: InvocationCheckpointReader,
        authorization: AuthorizationRefreshPort,
        room_epochs: RoomEpochStore,
        resources: ResourceMaterializerPort,
        dispatch: A2ADispatchPort,
        observations: NormalizedObservationRecorder,
        terminal_finalizer: TerminalInteractionFinalizer,
        policy: A2ARuntimePolicy | None = None,
        worker_id: str = "a2a-runtime",
    ) -> None:
        self.ledger = ledger
        self.prepared_reader = prepared_reader
        self.checkpoint_reader = checkpoint_reader
        self.authorization = authorization
        self.room_epochs = room_epochs
        self.resources = resources
        self.dispatch = dispatch
        self.observations = observations
        self.terminal_finalizer = terminal_finalizer
        self.policy = policy or A2ARuntimePolicy()
        self.worker_id = worker_id

    async def accept(self, invocation: ToolInvocation) -> ToolAcceptance:
        existing = await self.ledger.load(invocation.run_id, invocation.invocation_id)
        if existing is not None:
            if not _invocation_matches_record(invocation, existing):
                raise A2AAcceptanceConflict("invocation replay does not match ledger")
            return existing.acceptance

        prepared = await self.prepared_reader.read_prepared(invocation)
        if prepared is None:
            raise A2AAcceptanceDenied("prepared invocation snapshot is missing")
        identity = _acceptance_material(invocation, prepared)
        binding = prepared.binding
        if (
            binding.binding_id != invocation.tool.binding.binding_id
            or binding.binding_digest != invocation.tool.binding.binding_digest
            or binding.tool_name != invocation.tool.definition.name
            or binding.definition != invocation.tool.definition
            or binding.requesting_subject_digest
            != _digest(prepared.requesting_subject_id)
        ):
            raise A2AAcceptanceConflict("frozen binding does not correlate")
        if not await self.room_epochs.verify_active(
            prepared.room_id, prepared.room_epoch
        ):
            raise A2AAcceptanceDenied("Room epoch is not active")
        parsed = AgentToolInput.model_validate(invocation.arguments)
        resource_refs = [ref.ref_id for ref in prepared.resource_manifest.refs]
        decision = await self.authorization.authorize(
            binding=binding,
            requesting_subject_id=prepared.requesting_subject_id,
            room_id=prepared.room_id,
            room_epoch=prepared.room_epoch,
            resource_refs=resource_refs,
        )
        if decision != "authorized":
            raise A2AAcceptanceDenied(
                "authorization denied"
                if decision == "denied"
                else "authorization unavailable"
            )

        now = datetime.now(UTC)
        call_record_id = _stable("call", invocation.run_id, invocation.invocation_id)
        acceptance_id = _stable(
            "acceptance",
            invocation.run_id,
            invocation.invocation_id,
            invocation.idempotency_key,
        )
        command_id = _stable("dispatch", call_record_id)
        message_id = _stable("message", call_record_id)
        arguments_digest = _digest_json(invocation.arguments)
        dispatch_snapshot = {
            "command_id": command_id,
            "message_id": message_id,
            "task": parsed.task,
            "agent_id": binding.agent_id,
            "skill_id": binding.skill_id,
            "endpoint_scope": binding.endpoint_scope,
            "transport_kind": binding.transport_kind,
            "direct_mode": _select_direct_mode(binding),
            "requesting_subject_digest": _digest(prepared.requesting_subject_id),
            "room_id": prepared.room_id,
            "room_epoch": prepared.room_epoch,
            "deadline_at": invocation.deadline_at,
            "resource_manifest": prepared.resource_manifest,
        }
        record = AgentCallLedgerRecord(
            call_record_id=call_record_id,
            invocation_id=invocation.invocation_id,
            acceptance_id=acceptance_id,
            idempotency_key=invocation.idempotency_key,
            run_id=invocation.run_id,
            room_id=prepared.room_id,
            room_epoch=prepared.room_epoch,
            assistant_message_id=invocation.assistant_message_id,
            source_index=invocation.source_index,
            tool_name=invocation.tool.definition.name,
            binding_id=binding.binding_id,
            binding_digest=binding.binding_digest,
            agent_id=binding.agent_id,
            skill_id=binding.skill_id,
            card_digest=binding.card_digest,
            endpoint_scope_digest=binding.endpoint_scope_digest,
            arguments_digest=arguments_digest,
            requesting_subject_digest=_digest(prepared.requesting_subject_id),
            dispatch_snapshot=dispatch_snapshot,
            resource_manifest=prepared.resource_manifest,
            runtime_policy=self.policy,
            state="accepted",
            transport_kind=binding.transport_kind,
            dispatch_command_id=command_id,
            accepted_at=now,
            updated_at=now,
        )
        outcome = await self.ledger.insert(record)
        if outcome == "conflict":
            raise A2AAcceptanceConflict("call ledger identity conflict")
        if outcome not in {"accepted", "replayed"}:
            raise RuntimeError(f"call ledger acceptance failed: {outcome}")
        persisted = await self.ledger.load(invocation.run_id, invocation.invocation_id)
        if persisted is None or _record_acceptance_material(persisted) != identity:
            raise A2AAcceptanceConflict("persisted acceptance does not correlate")
        return persisted.acceptance

    async def execute(
        self,
        invocation: ToolInvocation,
        acceptance: ToolAcceptance,
        *,
        signal: CancellationSignal,
    ) -> ToolResult | ToolSuspension:
        try:
            return await self._execute(invocation, acceptance, signal=signal)
        except (
            RecoverableAdapterError,
            RecoverableCheckpointError,
            RecoverableAuthorizationError,
            RecoverableEpochError,
            RecoverableResourceError,
            RecoverableTransportError,
            AmbiguousRemoteEffectError,
            TimeoutError,
        ):
            # After durable acceptance, expected persistence/checkpoint outages are
            # recoverable lifecycle states. Never let Kernel translate them into a
            # competing generic terminal ToolResult.
            return _suspension(invocation)

    async def _execute(  # noqa: C901
        self,
        invocation: ToolInvocation,
        acceptance: ToolAcceptance,
        *,
        signal: CancellationSignal,
    ) -> ToolResult | ToolSuspension:
        record = await self.ledger.load(invocation.run_id, invocation.invocation_id)
        if record is None:
            return _result(invocation, "failed", "call_ledger_missing")
        if acceptance != record.acceptance:
            # A mismatched caller cannot finalize or supersede durable call
            # authority, especially while an attached interaction is active.
            return _suspension(invocation)
        if record.terminal_result is not None:
            return await self._finalized_terminal_or_suspension(record, invocation)
        checkpointed = await self.checkpoint_reader.is_acceptance_checkpointed(
            invocation.run_id,
            invocation.invocation_id,
            acceptance.acceptance_id,
            acceptance.idempotency_key,
            record.binding_digest,
        )
        if not checkpointed or signal.cancelled:
            return _suspension(invocation)

        now = datetime.now(UTC)
        claimed = await self.ledger.claim(
            record.call_record_id,
            expected_state_version=record.state_version,
            owner_id=self.worker_id,
            lease_expires_at=now + timedelta(seconds=self.policy.claim_lease_seconds),
            claimed_at=now,
        )
        if claimed is None:
            return await self._persisted_outcome_or_suspension(invocation)
        record = claimed

        # Exhaustive execution fence. Only an accepted/ready call can invoke dispatch.
        if record.state == "dispatching":
            uncertain = transition_call(
                record,
                to_state="delivery_uncertain",
                updated_at=datetime.now(UTC),
                error_code="dispatch_receipt_missing",
                claim_owner=None,
                claim_expires_at=None,
                next_attempt_at=datetime.now(UTC),
            )
            await self.ledger.cas(
                uncertain, expected_state_version=record.state_version
            )
            return _suspension(invocation)
        if record.state not in {"accepted", "ready_to_dispatch"}:
            await self._release(record)
            return _suspension(invocation)

        try:
            if not await self.room_epochs.verify_active(
                record.room_id, record.room_epoch
            ):
                return await self._terminal(
                    record, invocation, "expired", "room_epoch_gone"
                )
            prepared = await self.prepared_reader.read_prepared(invocation)
            if prepared is None or (
                prepared.binding.binding_id != record.binding_id
                or prepared.binding.binding_digest != record.binding_digest
                or _digest(prepared.requesting_subject_id)
                != record.requesting_subject_digest
            ):
                return await self._terminal(
                    record, invocation, "rejected", "prepared_snapshot_mismatch"
                )
            decision = await self.authorization.authorize(
                binding=prepared.binding,
                requesting_subject_id=prepared.requesting_subject_id,
                room_id=record.room_id,
                room_epoch=record.room_epoch,
                resource_refs=[ref.ref_id for ref in record.resource_manifest.refs],
            )
            record = await self._renew_and_verify_epoch(record)
            if record is None:
                return _suspension(invocation)
            if decision == "denied":
                return await self._terminal(
                    record, invocation, "rejected", "authorization_revoked"
                )
            if decision == "transient_failure":
                await self._release(record)
                return _suspension(invocation)
            materialized = await self.resources.materialize(
                record.resource_manifest,
                room_id=record.room_id,
                room_epoch=record.room_epoch,
                allowed_input_modes=prepared.binding.input_modes,
                deadline_at=record.dispatch_snapshot.deadline_at,
            )
            verify_materialized_digests(record.resource_manifest, materialized)
            record = await self._renew_and_verify_epoch(record)
            if record is None:
                return _suspension(invocation)
        except (
            RecoverableAdapterError,
            RecoverableAuthorizationError,
            RecoverableEpochError,
            RecoverableResourceError,
            TimeoutError,
        ):
            await self._release(record)
            raise

        if record.state == "accepted":
            ready = transition_call(
                record, to_state="ready_to_dispatch", updated_at=datetime.now(UTC)
            )
            if await self.ledger.cas(
                ready, expected_state_version=record.state_version
            ) not in {"accepted", "replayed"}:
                return _suspension(invocation)
            record = ready
        if record.state != "ready_to_dispatch":
            await self._release(record)
            return _suspension(invocation)
        dispatching = transition_call(
            record,
            to_state="dispatching",
            updated_at=datetime.now(UTC),
            transport_attempts=record.transport_attempts + 1,
        )
        if await self.ledger.cas(
            dispatching, expected_state_version=record.state_version
        ) not in {"accepted", "replayed"}:
            return _suspension(invocation)
        record = dispatching
        record = await self._renew_and_verify_epoch(record)
        if record is None:
            return _suspension(invocation)

        command = _dispatch_command(record, materialized_resources=materialized)
        try:
            receipt = await self.dispatch.dispatch(command)
        except (
            RecoverableAdapterError,
            RecoverableTransportError,
            AmbiguousRemoteEffectError,
            TimeoutError,
        ):
            # The expired dispatching record is intentionally left for recovery to
            # classify as uncertain when lease ownership was lost during the await.
            renewed = await self._renew_and_verify_epoch(record)
            if renewed is None:
                return _suspension(invocation)
            uncertain = transition_call(
                renewed,
                to_state="delivery_uncertain",
                updated_at=datetime.now(UTC),
                claim_owner=None,
                claim_expires_at=None,
                next_attempt_at=datetime.now(UTC),
            )
            await self.ledger.cas(
                uncertain, expected_state_version=renewed.state_version
            )
            return _suspension(invocation)
        renewed = await self._renew_and_verify_epoch(record)
        if renewed is None:
            return _suspension(invocation)
        record = renewed

        if receipt.outcome == "delivery_uncertain":
            uncertain = transition_call(
                record,
                to_state="delivery_uncertain",
                updated_at=datetime.now(UTC),
                claim_owner=None,
                claim_expires_at=None,
                next_attempt_at=datetime.now(UTC),
            )
            await self.ledger.cas(
                uncertain, expected_state_version=record.state_version
            )
            return _suspension(invocation)
        if receipt.outcome == "accepted":
            try:
                aliases = bind_authoritative_aliases(
                    record, task_id=receipt.task_id, context_id=receipt.context_id
                )
            except ValueError:
                uncertain = transition_call(
                    record,
                    to_state="delivery_uncertain",
                    updated_at=datetime.now(UTC),
                    error_code="authoritative_alias_conflict",
                    claim_owner=None,
                    claim_expires_at=None,
                    next_attempt_at=datetime.now(UTC),
                )
                await self.ledger.cas(
                    uncertain, expected_state_version=record.state_version
                )
                return _suspension(invocation)
            if not any(alias.kind == "task" for alias in aliases):
                uncertain = transition_call(
                    record,
                    to_state="delivery_uncertain",
                    updated_at=datetime.now(UTC),
                    error_code="authoritative_alias_missing",
                    claim_owner=None,
                    claim_expires_at=None,
                    next_attempt_at=datetime.now(UTC),
                )
                await self.ledger.cas(
                    uncertain, expected_state_version=record.state_version
                )
                return _suspension(invocation)
            working = transition_call(
                record,
                to_state="working",
                updated_at=datetime.now(UTC),
                a2a_task_id=receipt.task_id,
                a2a_context_id=receipt.context_id,
                ownership_aliases=aliases,
                ownership_alias_keys=ownership_alias_keys(aliases),
                claim_owner=None,
                claim_expires_at=None,
                next_attempt_at=datetime.now(UTC)
                + timedelta(seconds=self.policy.retry_backoff_initial_seconds),
            )
            outcome = await self.ledger.cas(
                working, expected_state_version=record.state_version
            )
            if outcome not in {"accepted", "replayed"}:
                # A collision or competing terminal winner is recovered from the
                # persisted call; never create a second dispatch.
                return await self._persisted_outcome_or_suspension(invocation)
            return _suspension(invocation)

        observation = receipt.terminal_observation
        if observation is None:
            status = "rejected" if receipt.outcome == "rejected" else "failed"
            return await self._terminal(record, invocation, status, "dispatch_rejected")
        if observation.call_record_id is None:
            observation = observation.model_copy(
                update={"call_record_id": record.call_record_id}
            )
        await self.observations.record(observation)
        renewed = await self._renew_and_verify_epoch(record)
        if renewed is None:
            return _suspension(invocation)
        record = renewed
        terminal = apply_observation(
            record, observation, recent_limit=self.policy.recent_observation_id_limit
        )
        outcome = await self.ledger.cas(
            terminal, expected_state_version=record.state_version
        )
        if outcome not in {"accepted", "replayed"} or terminal.terminal_result is None:
            return await self._persisted_outcome_or_suspension(invocation)
        assert terminal.terminal_result_digest is not None
        await self.observations.mark_executor_outcome(
            observation.observation_id,
            outcome_digest=terminal.terminal_result_digest,
        )
        return await self._finalized_terminal_or_suspension(terminal, invocation)

    async def _terminal(
        self,
        record: AgentCallLedgerRecord,
        invocation: ToolInvocation,
        status: str,
        error_code: str,
    ) -> ToolResult | ToolSuspension:
        result = _result(invocation, status, error_code)
        terminal = transition_call(
            record,
            to_state=status,
            updated_at=datetime.now(UTC),
            terminal_result=result,
            terminal_result_digest=sha256(
                result.model_dump_json().encode()
            ).hexdigest(),
            error_code=error_code,
            error_message=result.error_message,
        )
        outcome = await self.ledger.cas(
            terminal, expected_state_version=record.state_version
        )
        if outcome in {"accepted", "replayed"}:
            return await self._finalized_terminal_or_suspension(terminal, invocation)
        return await self._persisted_outcome_or_suspension(invocation)

    async def _persisted_outcome_or_suspension(
        self, invocation: ToolInvocation
    ) -> ToolResult | ToolSuspension:
        current = await self.ledger.load(invocation.run_id, invocation.invocation_id)
        if current is not None and current.terminal_result is not None:
            return await self._finalized_terminal_or_suspension(current, invocation)
        return _suspension(invocation)

    async def _finalized_terminal_or_suspension(
        self,
        record: AgentCallLedgerRecord,
        invocation: ToolInvocation,
    ) -> ToolResult | ToolSuspension:
        if record.terminal_result is None:
            return _suspension(invocation)
        await self.terminal_finalizer.finalize(record)
        return record.terminal_result

    async def _renew_and_verify_epoch(
        self, record: AgentCallLedgerRecord
    ) -> AgentCallLedgerRecord | None:
        now = datetime.now(UTC)
        renewed = await self.ledger.renew(
            record.call_record_id,
            expected_state_version=record.state_version,
            owner_id=self.worker_id,
            lease_expires_at=now + timedelta(seconds=self.policy.claim_lease_seconds),
            renewed_at=now,
        )
        if renewed is None:
            return None
        if not await self.room_epochs.verify_active(
            renewed.room_id, renewed.room_epoch
        ):
            return None
        return renewed

    async def _release(self, record: AgentCallLedgerRecord) -> None:
        await self.ledger.release(
            record.call_record_id,
            expected_state_version=record.state_version,
            owner_id=self.worker_id,
            next_attempt_at=datetime.now(UTC)
            + timedelta(seconds=self.policy.retry_backoff_initial_seconds),
            released_at=datetime.now(UTC),
        )


def _dispatch_command(
    record: AgentCallLedgerRecord,
    *,
    materialized_resources: list,
) -> A2ADispatchCommand:
    return A2ADispatchCommand(
        command_id=record.dispatch_snapshot.command_id,
        call_record_id=record.call_record_id,
        invocation_id=record.invocation_id,
        message_id=record.dispatch_snapshot.message_id,
        binding_id=record.binding_id,
        agent_id=record.agent_id,
        skill_id=record.skill_id,
        endpoint_scope=record.dispatch_snapshot.endpoint_scope,
        transport_kind=record.transport_kind,
        direct_mode=record.dispatch_snapshot.direct_mode,
        task=record.dispatch_snapshot.task,
        materialized_resources=materialized_resources,
        room_id=record.room_id,
        room_epoch=record.room_epoch,
        deadline_at=record.dispatch_snapshot.deadline_at,
    )


def _select_direct_mode(binding) -> str | None:
    if binding.transport_kind != "direct":
        return None
    capabilities = set(binding.direct_capabilities)
    for mode in ("stream", "sync", "poll"):
        if mode in capabilities:
            return mode
    raise A2AAcceptanceDenied("direct Agent has no supported delivery capability")


def _acceptance_material(invocation: ToolInvocation, prepared) -> tuple[object, ...]:
    return (
        _stable("call", invocation.run_id, invocation.invocation_id),
        invocation.run_id,
        invocation.invocation_id,
        invocation.idempotency_key,
        invocation.tool.binding.binding_digest,
        _digest_json(invocation.arguments),
        prepared.resource_manifest.content_digest,
        prepared.room_epoch,
    )


def _invocation_matches_record(
    invocation: ToolInvocation, record: AgentCallLedgerRecord
) -> bool:
    return (
        record.call_record_id
        == _stable("call", invocation.run_id, invocation.invocation_id)
        and record.run_id == invocation.run_id
        and record.invocation_id == invocation.invocation_id
        and record.idempotency_key == invocation.idempotency_key
        and record.binding_id == invocation.tool.binding.binding_id
        and record.binding_digest == invocation.tool.binding.binding_digest
        and record.tool_name == invocation.tool.definition.name
        and record.arguments_digest == _digest_json(invocation.arguments)
    )


def _record_acceptance_material(record: AgentCallLedgerRecord) -> tuple[object, ...]:
    return (
        record.call_record_id,
        record.run_id,
        record.invocation_id,
        record.idempotency_key,
        record.binding_digest,
        record.arguments_digest,
        record.resource_manifest.content_digest,
        record.room_epoch,
    )


def _result(invocation: ToolInvocation, status: str, error_code: str) -> ToolResult:
    return ToolResult(
        call_id=invocation.invocation_id,
        tool_name=invocation.tool.definition.name,
        status=status,
        content=[TextPart(text="The Agent call could not complete.")],
        artifact_refs=[],
        error_code=error_code,
        error_message=error_code.replace("_", " "),
    )


def _suspension(invocation: ToolInvocation) -> ToolSuspension:
    return ToolSuspension(
        invocation_id=invocation.invocation_id, status="waiting_external"
    )


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _stable(prefix: str, *parts: str) -> str:
    return f"{prefix}-{_digest_json([*parts])}"


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()
