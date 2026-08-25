"""Typed invocation-owned HITL persistence and durable continuation commands."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from common.dto.hitl import (
    A2AInteractionSpec,
    HITLAuthorizationResultAnswer,
    HITLQuestionAnswer,
    HITLRouteSnapshotV2,
)

from ..models import TextPart
from .errors import (
    AmbiguousRemoteEffectError,
    RecoverableAdapterError,
    RecoverableCheckpointError,
    RecoverableTransportError,
)
from .interaction_outcome import (
    emit_hitl_request_events,
    park_call_for_interaction,
)
from .ledger import (
    TERMINAL_AGENT_CALL_STATES,
    apply_observation,
    transition_call,
)
from .models import (
    A2AContinuationCommand,
    A2ARuntimePolicy,
    AgentCallLedgerRecord,
    DurableHITLAnswerRecord,
    HITLAnswerAppliedMarker,
    NormalizedA2AObservation,
    VerifiedAuthReferenceBinding,
)
from .ports import (
    A2ADispatchPort,
    AgentCallLedgerStore,
    AgentToolBindingStore,
    AuthorizationRefreshPort,
    AuthReferenceVerificationPort,
    HITLAbandonOutcome,
    HITLApplicationPort,
    NormalizedObservationRecorder,
    RoomEpochStore,
    StoreOutcome,
)
from .terminal_interactions import TerminalInteractionFinalizer


class InMemoryHITLApplicationPort:
    def __init__(self) -> None:
        self._interactions: dict[
            str, tuple[A2AInteractionSpec, HITLRouteSnapshotV2, str]
        ] = {}
        self._answer_records: dict[tuple[str, int], DurableHITLAnswerRecord] = {}
        self._eligible_interactions: set[str] = set()
        self._abandoned_interactions: dict[str, tuple[str, str]] = {}

    async def create_or_replay(
        self,
        *,
        call: AgentCallLedgerRecord,
        interaction: A2AInteractionSpec,
        interaction_fingerprint: str,
    ) -> str:
        route = HITLRouteSnapshotV2(
            orchestration_run_id=call.run_id,
            call_record_id=call.call_record_id,
            invocation_id=call.invocation_id,
            room_id=call.room_id,
            room_epoch=call.room_epoch,
            binding_id=call.binding_id,
            agent_id=call.agent_id,
            task_id=call.a2a_task_id,
            context_id=call.a2a_context_id,
            interaction_revision=1,
            interaction_fingerprint=interaction_fingerprint,
        )
        interaction_id = interaction.interaction_id
        desired = (
            A2AInteractionSpec.model_validate(interaction.model_dump(mode="python")),
            HITLRouteSnapshotV2.model_validate(route.model_dump(mode="python")),
            interaction_fingerprint,
        )
        existing = self._interactions.get(interaction_id)
        if existing is not None and existing != desired:
            raise ValueError("HITL interaction identity conflict")
        self._interactions.setdefault(interaction_id, desired)
        if (
            call.state in {"input_required", "auth_required"}
            and call.pending_interaction_id == interaction_id
            and call.interaction_fingerprint == interaction_fingerprint
        ):
            self._eligible_interactions.add(interaction_id)
        return interaction_id

    async def activate(
        self,
        interaction_id: str,
        *,
        call_record_id: str,
        interaction_fingerprint: str,
    ) -> StoreOutcome:
        stored = self._interactions.get(interaction_id)
        if stored is None:
            return "error"
        if (
            stored[1].call_record_id != call_record_id
            or stored[2] != interaction_fingerprint
            or interaction_id in self._abandoned_interactions
        ):
            return "conflict"
        if interaction_id in self._eligible_interactions:
            return "replayed"
        self._eligible_interactions.add(interaction_id)
        return "accepted"

    async def abandon(
        self,
        interaction_id: str,
        *,
        call_record_id: str,
        reason: str,
    ) -> HITLAbandonOutcome:
        stored = self._interactions.get(interaction_id)
        if stored is None:
            return "absent"
        if stored[1].call_record_id != call_record_id:
            return "conflict"
        desired = (call_record_id, reason)
        existing = self._abandoned_interactions.get(interaction_id)
        if existing is not None:
            return "replayed" if existing[0] == call_record_id else "conflict"
        self._eligible_interactions.discard(interaction_id)
        self._abandoned_interactions[interaction_id] = desired
        return "accepted"

    def is_abandoned_for_test(self, interaction_id: str) -> bool:
        return interaction_id in self._abandoned_interactions

    def read_interaction_for_test(
        self, interaction_id: str
    ) -> tuple[A2AInteractionSpec, HITLRouteSnapshotV2, str] | None:
        stored = self._interactions.get(interaction_id)
        if (
            stored is None
            or interaction_id not in self._eligible_interactions
            or interaction_id in self._abandoned_interactions
        ):
            return None
        spec, route, fingerprint = stored
        return (
            A2AInteractionSpec.model_validate(spec.model_dump(mode="python")),
            HITLRouteSnapshotV2.model_validate(route.model_dump(mode="python")),
            fingerprint,
        )

    async def read_interaction(
        self, interaction_id: str
    ) -> tuple[A2AInteractionSpec, HITLRouteSnapshotV2, str] | None:
        return self.read_interaction_for_test(interaction_id)

    async def get_eligible_interactions(
        self, room_id: str
    ) -> list[tuple[A2AInteractionSpec, HITLRouteSnapshotV2, str]]:
        interactions = []
        for interaction_id, stored in self._interactions.items():
            if interaction_id not in self._eligible_interactions:
                continue
            if interaction_id in self._abandoned_interactions:
                continue
            spec, route, fingerprint = stored
            if route.room_id != room_id:
                continue
            interactions.append(
                (
                    A2AInteractionSpec.model_validate(spec.model_dump(mode="python")),
                    HITLRouteSnapshotV2.model_validate(route.model_dump(mode="python")),
                    fingerprint,
                )
            )
        return interactions

    async def read_answers(
        self, interaction_id: str, interaction_revision: int
    ) -> list[HITLQuestionAnswer] | None:
        if (
            interaction_id not in self._eligible_interactions
            or interaction_id in self._abandoned_interactions
        ):
            return None
        record = await self.read_answer_record(interaction_id, interaction_revision)
        return list(record.answers) if record is not None else None

    async def read_answer_record(
        self, interaction_id: str, interaction_revision: int
    ) -> DurableHITLAnswerRecord | None:
        record = self._answer_records.get((interaction_id, interaction_revision))
        return _clone_answer_record(record) if record is not None else None

    async def answer(
        self,
        *,
        interaction_id: str,
        interaction_revision: int,
        route_fingerprint: str,
        answers: list[HITLQuestionAnswer],
        authenticated_answerer_id: str,
        verified_auth_reference_digests: list[str],
        verified_auth_references: list[VerifiedAuthReferenceBinding],
    ) -> str:
        stored = self._interactions.get(interaction_id)
        if (
            stored is None
            or interaction_id not in self._eligible_interactions
            or interaction_id in self._abandoned_interactions
        ):
            raise KeyError(interaction_id)
        spec, route, _ = stored
        if route.fingerprint != route_fingerprint:
            raise ValueError("HITL route fingerprint changed")
        inventory = {question.question_id: question for question in spec.questions}
        if set(inventory) != {answer.question_id for answer in answers}:
            raise ValueError("HITL answer inventory does not match")
        for answer in answers:
            inventory[answer.question_id].validate_answer(answer)
        answer_digest = _digest_json(
            [answer.model_dump(mode="json") for answer in answers]
        )
        desired = DurableHITLAnswerRecord(
            interaction_id=interaction_id,
            interaction_revision=interaction_revision,
            route_fingerprint=route_fingerprint,
            authenticated_answerer_id=authenticated_answerer_id,
            answer_digest=answer_digest,
            answers=answers,
            verified_auth_reference_digests=verified_auth_reference_digests,
            verified_auth_references=verified_auth_references,
            applied_at=datetime.now(UTC),
        )
        key = (interaction_id, interaction_revision)
        existing = self._answer_records.get(key)
        if existing is not None and _answer_identity(existing) != _answer_identity(
            desired
        ):
            raise ValueError("HITL answer identity conflict")
        if existing is None:
            self._answer_records[key] = _clone_answer_record(desired)
        return answer_digest


class A2AContinuationCoordinator:
    def __init__(
        self,
        *,
        ledger: AgentCallLedgerStore,
        bindings: AgentToolBindingStore,
        hitl: HITLApplicationPort,
        room_epochs: RoomEpochStore,
        authorization: AuthorizationRefreshPort,
        auth_references: AuthReferenceVerificationPort,
        dispatch: A2ADispatchPort,
        observations: NormalizedObservationRecorder,
        policy: A2ARuntimePolicy | None = None,
        worker_id: str = "a2a-continuation",
        hitl_delivery: Any | None = None,
        run_store: Any | None = None,
    ) -> None:
        self.ledger = ledger
        self.bindings = bindings
        self.hitl = hitl
        self.terminal_interactions = TerminalInteractionFinalizer(hitl)
        self.room_epochs = room_epochs
        self.authorization = authorization
        self.auth_references = auth_references
        self.dispatch = dispatch
        self.observations = observations
        self.policy = policy or A2ARuntimePolicy()
        self.worker_id = worker_id
        self.hitl_delivery = hitl_delivery
        self.run_store = run_store

    async def resume(
        self,
        *,
        call_record_id: str,
        interaction_id: str,
        interaction_revision: int,
        route_fingerprint: str,
        answers: list[HITLQuestionAnswer],
        authenticated_answerer_id: str,
    ) -> str:
        try:
            return await self._resume(
                call_record_id=call_record_id,
                interaction_id=interaction_id,
                interaction_revision=interaction_revision,
                route_fingerprint=route_fingerprint,
                answers=answers,
                authenticated_answerer_id=authenticated_answerer_id,
            )
        except RecoverableAdapterError:
            return "delivery_uncertain"

    async def _resume(  # noqa: C901
        self,
        *,
        call_record_id: str,
        interaction_id: str,
        interaction_revision: int,
        route_fingerprint: str,
        answers: list[HITLQuestionAnswer],
        authenticated_answerer_id: str,
    ) -> str:
        call = await self.ledger.load_by_record_id(call_record_id)
        if call is None:
            raise KeyError(call_record_id)
        answer_digest = _digest_json(
            [answer.model_dump(mode="json") for answer in answers]
        )
        existing_answer = await self.hitl.read_answer_record(
            interaction_id, interaction_revision
        )
        if existing_answer is not None:
            await self._validate_existing_answer_retry(
                call,
                existing_answer,
                interaction_id=interaction_id,
                interaction_revision=interaction_revision,
                route_fingerprint=route_fingerprint,
                answer_digest=answer_digest,
                answers=answers,
                authenticated_answerer_id=authenticated_answerer_id,
            )
            if call.answer_applied is not None:
                self._validate_applied_outcome(call, existing_answer)
                if call.terminal_result is not None:
                    return await self._finalized_state(call)
                if call.continuation_command is not None:
                    if call.state in {"resuming", "delivery_uncertain", "working"}:
                        return await self.recover_call(call_record_id=call_record_id)
                    return await self._finalized_state(call)
        if call.terminal_result is not None:
            raise ValueError("terminal call has no matching applied HITL answer")
        if call.state == "resuming" and call.continuation_command is not None:
            return await self.recover_call(call_record_id=call_record_id)
        self._validate_waiting_call(
            call,
            interaction_id=interaction_id,
            interaction_revision=interaction_revision,
            authenticated_answerer_id=authenticated_answerer_id,
        )
        await self._validate_challenge_and_answers(
            call,
            interaction_id=interaction_id,
            interaction_revision=interaction_revision,
            route_fingerprint=route_fingerprint,
            answers=answers,
        )
        if existing_answer is not None:
            marked = await self._persist_answer_marker(call, existing_answer)
            if marked is None:
                return "delivery_uncertain"
            return await self._create_or_replay_command(marked, existing_answer)
        (
            verified_auth_digests,
            verified_auth_bindings,
        ) = await self._verify_auth_references(
            call,
            interaction_id=interaction_id,
            interaction_revision=interaction_revision,
            route_fingerprint=route_fingerprint,
            answer_digest=answer_digest,
            answers=answers,
            authenticated_answerer_id=authenticated_answerer_id,
        )
        await self.hitl.answer(
            interaction_id=interaction_id,
            interaction_revision=interaction_revision,
            route_fingerprint=route_fingerprint,
            answers=answers,
            authenticated_answerer_id=authenticated_answerer_id,
            verified_auth_reference_digests=verified_auth_digests,
            verified_auth_references=verified_auth_bindings,
        )
        answer_record = await self.hitl.read_answer_record(
            interaction_id, interaction_revision
        )
        if answer_record is None:
            raise RuntimeError("durable HITL answer record is missing")
        marked = await self._persist_answer_marker(call, answer_record)
        if marked is None:
            return "delivery_uncertain"
        return await self._create_or_replay_command(marked, answer_record)

    async def reconcile_answer(self, *, call_record_id: str) -> str:
        try:
            return await self._reconcile_answer(call_record_id=call_record_id)
        except RecoverableAdapterError:
            return "delivery_uncertain"

    async def _reconcile_answer(self, *, call_record_id: str) -> str:
        call = await self.ledger.load_by_record_id(call_record_id)
        if call is None:
            raise KeyError(call_record_id)
        if call.terminal_result is not None:
            return await self._finalized_state(call)
        if call.continuation_command is not None:
            return await self.recover_call(call_record_id=call_record_id)
        if (
            call.state not in {"input_required", "auth_required"}
            or call.pending_interaction_id is None
            or call.interaction_revision is None
        ):
            return call.state
        answer_record = await self.hitl.read_answer_record(
            call.pending_interaction_id, call.interaction_revision
        )
        if answer_record is None:
            return call.state
        if (
            _digest(answer_record.authenticated_answerer_id)
            != call.requesting_subject_digest
        ):
            raise PermissionError("durable answer owner does not match call")
        marked = await self._persist_answer_marker(call, answer_record)
        if marked is None:
            current = await self.ledger.load_by_record_id(call_record_id)
            return current.state if current is not None else "delivery_uncertain"
        return await self._create_or_replay_command(marked, answer_record)

    async def recover_call(self, *, call_record_id: str) -> str:
        try:
            return await self._recover_call(call_record_id=call_record_id)
        except RecoverableAdapterError:
            return "delivery_uncertain"

    async def _recover_call(self, *, call_record_id: str) -> str:
        call = await self.ledger.load_by_record_id(call_record_id)
        if call is None:
            raise KeyError(call_record_id)
        if call.terminal_result is not None:
            return await self._finalized_state(call)
        if call.continuation_command is None:
            return await self.reconcile_answer(call_record_id=call_record_id)
        recoverable_states = {"resuming", "delivery_uncertain"}
        if call.state == "working" and call.continuation_state == "accepted":
            recoverable_states = recoverable_states | {"working"}
        if call.state not in recoverable_states:
            return call.state
        claimed = await self._claim(call)
        if claimed is None:
            return call.state
        inspect = claimed.continuation_state in {
            "dispatching",
            "delivery_uncertain",
            "accepted",
        }
        return await self._deliver(claimed, inspect=inspect)

    async def _validate_existing_answer_retry(
        self,
        call: AgentCallLedgerRecord,
        answer_record: DurableHITLAnswerRecord,
        *,
        interaction_id: str,
        interaction_revision: int,
        route_fingerprint: str,
        answer_digest: str,
        answers: list[HITLQuestionAnswer],
        authenticated_answerer_id: str,
    ) -> None:
        if (
            answer_record.interaction_id != interaction_id
            or answer_record.interaction_revision != interaction_revision
            or answer_record.route_fingerprint != route_fingerprint
            or answer_record.authenticated_answerer_id != authenticated_answerer_id
            or answer_record.answer_digest != answer_digest
            or answer_record.answers != answers
            or _digest(authenticated_answerer_id) != call.requesting_subject_digest
        ):
            raise PermissionError("HITL answer retry changed challenge identity")
        if call.state not in TERMINAL_AGENT_CALL_STATES:
            await self._validate_challenge_and_answers(
                call,
                interaction_id=interaction_id,
                interaction_revision=interaction_revision,
                route_fingerprint=route_fingerprint,
                answers=answers,
            )

    def _validate_applied_outcome(
        self,
        call: AgentCallLedgerRecord,
        answer_record: DurableHITLAnswerRecord,
    ) -> None:
        marker = _answer_marker(answer_record)
        if call.answer_applied != marker:
            raise PermissionError("applied HITL marker changed")
        command = call.continuation_command
        if command is not None and (
            command.interaction_id != answer_record.interaction_id
            or command.interaction_revision != answer_record.interaction_revision
            or command.answer_digest != answer_record.answer_digest
            or command.answers != answer_record.answers
        ):
            raise PermissionError("applied HITL continuation identity changed")
        consumed = {
            binding.reference_digest: binding
            for binding in call.consumed_auth_references
        }
        if any(
            consumed.get(binding.reference_digest) != binding
            for binding in answer_record.verified_auth_references
        ):
            raise PermissionError("applied HITL proof identity changed")

    def _validate_waiting_call(
        self,
        call: AgentCallLedgerRecord,
        *,
        interaction_id: str,
        interaction_revision: int,
        authenticated_answerer_id: str,
    ) -> None:
        if (
            call.state not in {"input_required", "auth_required"}
            or call.pending_interaction_id != interaction_id
            or call.interaction_revision != interaction_revision
            or call.interaction_fingerprint is None
        ):
            raise ValueError("call is not waiting on this interaction")
        if _digest(authenticated_answerer_id) != call.requesting_subject_digest:
            raise PermissionError("authenticated answerer does not own this call")

    async def _validate_challenge_and_answers(
        self,
        call: AgentCallLedgerRecord,
        *,
        interaction_id: str,
        interaction_revision: int,
        route_fingerprint: str,
        answers: list[HITLQuestionAnswer],
    ) -> None:
        stored = await self.hitl.read_interaction(interaction_id)
        if stored is None:
            raise ValueError("HITL interaction is missing")
        spec, route, interaction_fingerprint = stored
        if (
            route.fingerprint != route_fingerprint
            or route.interaction_revision != interaction_revision
            or route.interaction_fingerprint != interaction_fingerprint
            or interaction_fingerprint != call.interaction_fingerprint
            or route.call_record_id != call.call_record_id
        ):
            raise ValueError("HITL challenge identity changed")
        inventory = {question.question_id: question for question in spec.questions}
        if set(inventory) != {answer.question_id for answer in answers}:
            raise ValueError("HITL answer inventory does not match")
        for answer in answers:
            inventory[answer.question_id].validate_answer(answer)

    async def _verify_auth_references(
        self,
        call: AgentCallLedgerRecord,
        *,
        interaction_id: str,
        interaction_revision: int,
        route_fingerprint: str,
        answer_digest: str,
        answers: list[HITLQuestionAnswer],
        authenticated_answerer_id: str,
    ) -> tuple[list[str], list[VerifiedAuthReferenceBinding]]:
        verified: list[str] = []
        bindings: list[VerifiedAuthReferenceBinding] = []
        if call.interaction_fingerprint is None:
            raise ValueError("call has no interaction fingerprint")
        for answer in answers:
            if not isinstance(answer.answer, HITLAuthorizationResultAnswer):
                continue
            reference = answer.answer.authorization_reference
            challenge_digest = _digest_json(
                {
                    "interaction_id": interaction_id,
                    "interaction_revision": interaction_revision,
                    "route_fingerprint": route_fingerprint,
                    "interaction_fingerprint": call.interaction_fingerprint,
                    "question_id": answer.question_id,
                }
            )
            issuer_proof = await self.auth_references.verify(
                reference,
                authenticated_answerer_id=authenticated_answerer_id,
                call_record_id=call.call_record_id,
                binding_id=call.binding_id,
                binding_digest=call.binding_digest,
                room_id=call.room_id,
                room_epoch=call.room_epoch,
                interaction_id=interaction_id,
                interaction_revision=interaction_revision,
                route_fingerprint=route_fingerprint,
                interaction_fingerprint=call.interaction_fingerprint,
                question_id=answer.question_id,
                challenge_digest=challenge_digest,
                answer_digest=answer_digest,
            )
            if not issuer_proof:
                raise PermissionError("authorization reference was not verified")
            binding = VerifiedAuthReferenceBinding(
                reference_digest=_digest(reference),
                proof_digest=_digest_json(
                    {
                        "issuer_proof": issuer_proof,
                        "challenge_digest": challenge_digest,
                        "answer_digest": answer_digest,
                    }
                ),
                interaction_id=interaction_id,
                interaction_revision=interaction_revision,
                route_fingerprint=route_fingerprint,
                interaction_fingerprint=call.interaction_fingerprint,
                question_id=answer.question_id,
                challenge_digest=challenge_digest,
                answer_digest=answer_digest,
            )
            prior = next(
                (
                    item
                    for item in call.consumed_auth_references
                    if item.reference_digest == binding.reference_digest
                ),
                None,
            )
            if prior is not None and prior != binding:
                raise PermissionError(
                    "authorization reference was already consumed by another challenge"
                )
            verified.append(binding.proof_digest)
            bindings.append(binding)
        return sorted(set(verified)), sorted(
            set(bindings), key=lambda item: (item.question_id, item.reference_digest)
        )

    async def _persist_answer_marker(
        self,
        call: AgentCallLedgerRecord,
        answer_record: DurableHITLAnswerRecord,
    ) -> AgentCallLedgerRecord | None:
        marker = _answer_marker(answer_record)
        if call.answer_applied is not None:
            return call if call.answer_applied == marker else None
        consumed = list(call.consumed_auth_references)
        for binding in answer_record.verified_auth_references:
            prior = next(
                (
                    item
                    for item in consumed
                    if item.reference_digest == binding.reference_digest
                ),
                None,
            )
            if prior is not None and prior != binding:
                raise PermissionError(
                    "authorization reference was already consumed by another challenge"
                )
            if prior is None:
                consumed.append(binding)
        marked = call.model_copy(
            update={
                "answer_applied": marker,
                "consumed_auth_references": consumed,
                "state_version": call.state_version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        persisted = await self._cas_or_load_winner(
            marked, expected_state_version=call.state_version
        )
        return persisted if persisted.answer_applied == marker else None

    async def _create_or_replay_command(
        self,
        call: AgentCallLedgerRecord,
        answer_record: DurableHITLAnswerRecord,
    ) -> str:
        if call.continuation_command is not None:
            return await self.recover_call(call_record_id=call.call_record_id)
        binding = await self.bindings.load(call.binding_id)
        if binding is None or (
            binding.binding_digest != call.binding_digest
            or binding.requesting_subject_digest != call.requesting_subject_digest
        ):
            raise PermissionError("frozen continuation binding is unavailable")
        if call.a2a_task_id is None or call.a2a_context_id is None:
            raise ValueError("continuation requires authoritative task/context IDs")
        claimed = await self._claim(call)
        if claimed is None:
            current = await self.ledger.load_by_record_id(call.call_record_id)
            return current.state if current is not None else "delivery_uncertain"
        if not await self.room_epochs.verify_active(
            claimed.room_id, claimed.room_epoch
        ):
            await self._release(claimed)
            raise PermissionError("Room epoch is inactive")
        authorization = await self.authorization.authorize(
            binding=binding,
            requesting_subject_id=answer_record.authenticated_answerer_id,
            room_id=claimed.room_id,
            room_epoch=claimed.room_epoch,
            resource_refs=[ref.ref_id for ref in claimed.resource_manifest.refs],
        )
        claimed = await self._renew_and_verify(claimed)
        if claimed is None:
            return "delivery_uncertain"
        if authorization != "authorized":
            await self._release(claimed)
            raise PermissionError("continuation authorization failed")
        command = A2AContinuationCommand(
            command_id=f"continuation-{_stable([claimed.call_record_id, answer_record.interaction_id, str(answer_record.interaction_revision), answer_record.answer_digest])}",
            transport_kind=claimed.transport_kind,
            call_record_id=claimed.call_record_id,
            interaction_id=answer_record.interaction_id,
            interaction_revision=answer_record.interaction_revision,
            answer_digest=answer_record.answer_digest,
            answers=answer_record.answers,
            binding_id=claimed.binding_id,
            binding_digest=claimed.binding_digest,
            requesting_subject_digest=claimed.requesting_subject_digest,
            task_id=claimed.a2a_task_id,
            context_id=claimed.a2a_context_id,
            room_id=claimed.room_id,
            room_epoch=claimed.room_epoch,
            created_at=answer_record.applied_at,
        )
        resuming = transition_call(
            claimed,
            to_state="resuming",
            updated_at=datetime.now(UTC),
            continuation_command=command,
            continuation_state="pending",
        )
        persisted = await self._cas_or_load_winner(
            resuming, expected_state_version=claimed.state_version
        )
        if (
            persisted.continuation_command != command
            or persisted.state in TERMINAL_AGENT_CALL_STATES
        ):
            return await self._finalized_state(persisted)
        return await self._deliver(persisted, inspect=False)

    async def _deliver(  # noqa: C901
        self, call: AgentCallLedgerRecord, *, inspect: bool
    ) -> str:
        command = call.continuation_command
        if command is None:
            await self._release(call)
            return call.state
        if (
            command.binding_id != call.binding_id
            or command.binding_digest != call.binding_digest
            or command.requesting_subject_digest != call.requesting_subject_digest
        ):
            await self._release(call)
            raise PermissionError("continuation command identity changed")
        if (
            inspect
            and call.continuation_attempts
            >= call.runtime_policy.max_uncertain_inspection_attempts
        ):
            return await self._expire(call)
        call = await self._renew_and_verify(call)
        if call is None:
            return "delivery_uncertain"
        dispatching = call.model_copy(
            update={
                "continuation_state": (
                    "delivery_uncertain" if inspect else "dispatching"
                ),
                "continuation_attempts": call.continuation_attempts + 1,
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
            receipt, call = await self._run_fenced_continuation(
                call, command, inspect=inspect
            )
        except (
            RecoverableAdapterError,
            RecoverableTransportError,
            AmbiguousRemoteEffectError,
            TimeoutError,
        ):
            latest = await self.ledger.load_by_record_id(call.call_record_id)
            if latest is not None and latest.claim_owner == self.worker_id:
                call = latest
            renewed = await self._renew_and_verify(call)
            if renewed is None:
                return "delivery_uncertain"
            return await self._mark_uncertain(renewed)
        if receipt.terminal_observation is not None:
            observation = receipt.terminal_observation
            if observation.call_record_id is None:
                observation = observation.model_copy(
                    update={"call_record_id": call.call_record_id}
                )
            _, inbox_record = await self.observations.record(observation)
            call = await self._renew_and_verify(call)
            if call is None:
                return "delivery_uncertain"
            observation = inbox_record.observation
            if (
                observation.event_kind != "terminal"
                or observation.artifact_refs
                or inbox_record.state == "claimed"
                or inbox_record.delivery_route == "observation_sink"
            ):
                return await self._mark_uncertain(call)
            terminal = apply_observation(
                call,
                observation,
                recent_limit=call.runtime_policy.recent_observation_id_limit,
            )
            if terminal.continuation_command is not None:
                terminal = terminal.model_copy(
                    update={"continuation_state": "accepted"}
                )
            persisted = await self._cas_or_load_winner(
                terminal, expected_state_version=call.state_version
            )
            if (
                inbox_record.delivery_route == "unresolved"
                and inbox_record.delivery_state == "pending"
                and persisted.terminal_result_digest is not None
                and observation.observation_id in persisted.recent_observation_ids
                and persisted.terminal_result_digest == terminal.terminal_result_digest
            ):
                await self.observations.mark_executor_outcome(
                    observation.observation_id,
                    outcome_digest=persisted.terminal_result_digest,
                )
            return await self._finalized_state(persisted)
        if (
            receipt.outcome == "interaction"
            and receipt.interaction_observation is not None
        ):
            if _should_retry_continuation_send(call, receipt.interaction_observation):
                # Inspect still seeing the answered challenge (or a cleared
                # status.message) means the continuation send may not have
                # progressed the Agent. Resend once from inspect. If the
                # continue send itself returns that same stale challenge,
                # stay delivery_uncertain so recovery backs off — do not
                # re-park the answered interaction or untyped-complete.
                if inspect:
                    return await self._deliver(call, inspect=False)
                return await self._mark_uncertain(call)
            return await self._park_interaction(call, receipt.interaction_observation)
        if receipt.outcome == "accepted":
            working = transition_call(
                call,
                to_state="working",
                updated_at=datetime.now(UTC),
                continuation_state="accepted",
                claim_owner=None,
                claim_expires_at=None,
                next_attempt_at=datetime.now(UTC)
                + timedelta(seconds=self.policy.retry_backoff_initial_seconds),
            )
            persisted = await self._cas_or_load_winner(
                working, expected_state_version=call.state_version
            )
            return await self._finalized_state(persisted)
        return await self._mark_uncertain(call)

    async def _park_interaction(
        self, call: AgentCallLedgerRecord, observation: NormalizedA2AObservation
    ) -> str:
        if observation.call_record_id is None:
            observation = observation.model_copy(
                update={"call_record_id": call.call_record_id}
            )
        prior_interaction_id = call.pending_interaction_id
        if prior_interaction_id is None and call.answer_applied is not None:
            prior_interaction_id = call.answer_applied.interaction_id
        _, inbox_record = await self.observations.record(observation)
        call = await self._renew_and_verify(call)
        if call is None:
            return "delivery_uncertain"
        observation = inbox_record.observation
        if _blocks_untyped_interaction_completion(call, observation):
            # Continuation mid-flight can observe input_required after the A2A
            # server clears status.message (metadata lost). Completing that as
            # an untyped tool result lets the kernel narrate the ask as a final
            # answer and kills multi-round HITL. Stay uncertain so recovery
            # can resend / wait for a typed challenge.
            return await self._mark_uncertain(call)
        try:
            persisted, kind = await park_call_for_interaction(
                call=call,
                observation=observation,
                hitl=self.hitl,
                cas=self._cas_or_load_winner_for_park,
            )
        except RecoverableCheckpointError:
            return await self._mark_uncertain(call)
        if kind == "typed_waiting":
            await self._after_typed_park(
                persisted,
                observation,
                prior_interaction_id=prior_interaction_id,
            )
        elif kind in {"untyped_completed", "invalid_failed"}:
            await self._mark_parked_terminal_outcome(persisted, observation)
        return await self._finalized_state(persisted)

    async def _cas_or_load_winner_for_park(
        self, candidate: AgentCallLedgerRecord, expected: int
    ) -> AgentCallLedgerRecord:
        return await self._cas_or_load_winner(
            candidate, expected_state_version=expected
        )

    async def _after_typed_park(
        self,
        persisted: AgentCallLedgerRecord,
        observation: NormalizedA2AObservation,
        *,
        prior_interaction_id: str | None,
    ) -> None:
        if persisted.pending_interaction_id is None:
            return
        if (
            prior_interaction_id is not None
            and prior_interaction_id != persisted.pending_interaction_id
        ):
            with suppress(Exception):
                await self.hitl.abandon(
                    prior_interaction_id,
                    call_record_id=persisted.call_record_id,
                    reason="superseded_by_new_interaction",
                )
        raw_spec = observation.interaction_spec
        if raw_spec is None:
            return
        interaction = A2AInteractionSpec.model_validate(raw_spec)
        await emit_hitl_request_events(
            record=persisted,
            interaction=interaction,
            interaction_id=persisted.pending_interaction_id,
            hitl_delivery=self.hitl_delivery,
            run_store=self.run_store,
        )

    async def _mark_parked_terminal_outcome(
        self,
        persisted: AgentCallLedgerRecord,
        observation: NormalizedA2AObservation,
    ) -> None:
        if (
            persisted.terminal_result_digest is None
            or observation.observation_id not in persisted.recent_observation_ids
        ):
            return
        with suppress(Exception):
            await self.observations.mark_executor_outcome(
                observation.observation_id,
                outcome_digest=persisted.terminal_result_digest,
            )

    async def _expire(self, call: AgentCallLedgerRecord) -> str:
        command = call.continuation_command
        assert command is not None
        observation = NormalizedA2AObservation(
            observation_id=f"continuation-expired-{command.command_id}",
            call_record_id=call.call_record_id,
            source_kind="inspection",
            source_identity=f"continuation-expired:{command.command_id}",
            binding_scope=call.endpoint_scope_digest,
            event_kind="terminal",
            observed_at=command.created_at,
            task_id=call.a2a_task_id,
            context_id=call.a2a_context_id,
            status="expired",
            content=[TextPart(text="The Agent continuation could not be reconciled.")],
            error_code="continuation_uncertainty_exhausted",
            error_message="Continuation delivery could not be reconciled.",
        )
        _, inbox_record = await self.observations.record(observation)
        renewed = await self._renew_and_verify(call)
        if renewed is None:
            return "delivery_uncertain"
        observation = inbox_record.observation
        if (
            observation.event_kind != "terminal"
            or observation.artifact_refs
            or inbox_record.state == "claimed"
            or inbox_record.delivery_route == "observation_sink"
        ):
            return await self._mark_uncertain(renewed)
        expired = apply_observation(
            renewed,
            observation,
            recent_limit=renewed.runtime_policy.recent_observation_id_limit,
        )
        if expired.continuation_command is not None:
            expired = expired.model_copy(update={"continuation_state": "accepted"})
        persisted = await self._cas_or_load_winner(
            expired, expected_state_version=renewed.state_version
        )
        if (
            inbox_record.delivery_route == "unresolved"
            and inbox_record.delivery_state == "pending"
            and persisted.terminal_result_digest is not None
            and observation.observation_id in persisted.recent_observation_ids
            and persisted.terminal_result_digest == expired.terminal_result_digest
        ):
            await self.observations.mark_executor_outcome(
                observation.observation_id,
                outcome_digest=persisted.terminal_result_digest,
            )
        return await self._finalized_state(persisted)

    async def _mark_uncertain(self, call: AgentCallLedgerRecord) -> str:
        if call.state == "resuming":
            uncertain = transition_call(
                call,
                to_state="delivery_uncertain",
                updated_at=datetime.now(UTC),
                continuation_state="delivery_uncertain",
                claim_owner=None,
                claim_expires_at=None,
                next_attempt_at=datetime.now(UTC)
                + timedelta(seconds=self.policy.retry_backoff_initial_seconds),
            )
        else:
            uncertain = call.model_copy(
                update={
                    "continuation_state": "delivery_uncertain",
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
        winner = await self.ledger.load_by_record_id(candidate.call_record_id)
        if winner is None:
            raise RecoverableCheckpointError(
                "continuation CAS winner could not be classified"
            )
        return winner

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
        self, call: AgentCallLedgerRecord
    ) -> AgentCallLedgerRecord | None:
        now = datetime.now(UTC)
        renewed = await self.ledger.renew(
            call.call_record_id,
            expected_state_version=call.state_version,
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

    async def _run_fenced_continuation(
        self,
        call: AgentCallLedgerRecord,
        command: A2AContinuationCommand,
        *,
        inspect: bool,
    ) -> tuple[Any, AgentCallLedgerRecord]:
        current_record = [call]
        stop_heartbeat = asyncio.Event()

        async def _heartbeat_loop() -> None:
            interval = self.policy.claim_renew_interval_seconds

            while not stop_heartbeat.is_set():
                try:
                    await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval)
                    break
                except TimeoutError:
                    pass
                if stop_heartbeat.is_set():
                    break
                renewed = await self._renew_and_verify(current_record[0])
                if renewed is None:
                    break
                current_record[0] = renewed

        heartbeat_task = asyncio.create_task(_heartbeat_loop())
        continuation_task = asyncio.create_task(
            self.dispatch.inspect_continuation(command)
            if inspect
            else self.dispatch.continue_task(command)
        )

        try:
            done, _ = await asyncio.wait(
                {continuation_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED
            )

            if not stop_heartbeat.is_set() and continuation_task not in done:
                raise RecoverableAdapterError(
                    "claim lease or room epoch was lost during continuation"
                )

            receipt = await continuation_task
            return receipt, current_record[0]
        finally:
            stop_heartbeat.set()
            for task in (continuation_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.gather(
                    continuation_task, heartbeat_task, return_exceptions=True
                )

    async def _release(self, call: AgentCallLedgerRecord) -> None:
        await self.ledger.release(
            call.call_record_id,
            expected_state_version=call.state_version,
            owner_id=self.worker_id,
            next_attempt_at=datetime.now(UTC),
            released_at=datetime.now(UTC),
        )


def _should_retry_continuation_send(
    call: AgentCallLedgerRecord, observation: NormalizedA2AObservation
) -> bool:
    """True when inspect still shows the challenge we already answered.

    That means the continuation send never landed, so recovery must resend
    instead of re-parking the same interaction.

    Also true when the task is still interactive but the typed
    ``interaction_spec`` is missing (status.message cleared into history
    while state remains input-required). Missing-spec must not untyped-
    complete during continuation — that completes the call and the kernel
    writes the ask as a final HYBRO answer.
    """
    marker = call.answer_applied
    if marker is None or call.continuation_command is None:
        return False
    if observation.event_kind not in {"input_required", "auth_required"}:
        return False
    raw = observation.interaction_spec
    if not isinstance(raw, dict):
        return True
    return raw.get("interaction_id") == marker.interaction_id


def _blocks_untyped_interaction_completion(
    call: AgentCallLedgerRecord, observation: NormalizedA2AObservation
) -> bool:
    """Block untyped completion while a HITL continuation is still in flight."""
    if call.answer_applied is None or call.continuation_command is None:
        return False
    if observation.event_kind not in {"input_required", "auth_required"}:
        return False
    return observation.interaction_spec is None


def _answer_marker(record: DurableHITLAnswerRecord) -> HITLAnswerAppliedMarker:
    return HITLAnswerAppliedMarker(
        interaction_id=record.interaction_id,
        interaction_revision=record.interaction_revision,
        route_fingerprint=record.route_fingerprint,
        answer_digest=record.answer_digest,
        answerer_digest=_digest(record.authenticated_answerer_id),
        verified_auth_reference_digests=record.verified_auth_reference_digests,
        verified_auth_references=record.verified_auth_references,
        applied_at=record.applied_at,
    )


def _answer_identity(record: DurableHITLAnswerRecord) -> tuple[object, ...]:
    return (
        record.interaction_id,
        record.interaction_revision,
        record.route_fingerprint,
        record.authenticated_answerer_id,
        record.answer_digest,
        tuple(record.answers),
        tuple(record.verified_auth_reference_digests),
        tuple(record.verified_auth_references),
    )


def _clone_answer_record(record: DurableHITLAnswerRecord) -> DurableHITLAnswerRecord:
    return DurableHITLAnswerRecord.model_validate(record.model_dump(mode="python"))


def _stable(parts: list[str]) -> str:
    return _digest_json(parts)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()
