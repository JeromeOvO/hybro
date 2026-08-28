"""Durable HITL application port for the orchestrator runtime.

The orchestrator's ``HITLApplicationPort`` (see
``execution/orchestrator/a2a_runtime/ports.py``) persists typed A2A interactions
(``A2AInteractionSpec`` + ``HITLRouteSnapshotV2``) and their durable answer
records. This module provides:

* ``HITLApplicationStore`` — the durable store contract;
* ``InMemoryHITLApplicationStore`` — the unit-test double;
* ``DurableHITLApplicationPort`` — the production adapter that mirrors the
  ``InMemoryHITLApplicationPort`` semantics exactly (route fingerprint,
  authenticated answerer, verified auth references, answer idempotency).

The Mongo-backed store lives in ``dal/orchestrator/hitl.py`` so step 5b only has
to construct and inject it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from common.dto.hitl import (
    A2AInteractionSpec,
    HITLQuestionAnswer,
    HITLRouteSnapshotV2,
)
from execution.orchestrator.a2a_runtime.models import (
    AgentCallLedgerRecord,
    DurableHITLAnswerRecord,
    VerifiedAuthReferenceBinding,
)


class HITLApplicationStore(Protocol):
    async def ensure_interaction(
        self,
        *,
        interaction_id: str,
        spec: A2AInteractionSpec,
        route: HITLRouteSnapshotV2,
        fingerprint: str,
    ) -> str: ...

    async def load_interaction(
        self, interaction_id: str
    ) -> StoredHITLInteraction | None: ...

    async def get_eligible_interactions(
        self, room_id: str
    ) -> list[StoredHITLInteraction]: ...

    async def get_published_interactions(
        self, room_id: str
    ) -> list[StoredHITLInteraction]: ...

    async def mark_eligible(self, interaction_id: str) -> str: ...

    async def mark_published(self, interaction_id: str) -> str: ...

    async def abandon(
        self, interaction_id: str, *, call_record_id: str, reason: str
    ) -> str: ...

    async def load_answer(
        self, interaction_id: str, interaction_revision: int
    ) -> DurableHITLAnswerRecord | None: ...

    async def ensure_answer(
        self,
        *,
        interaction_id: str,
        interaction_revision: int,
        record: DurableHITLAnswerRecord,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class StoredHITLInteraction:
    interaction_id: str
    spec: A2AInteractionSpec
    route: HITLRouteSnapshotV2
    fingerprint: str
    eligible: bool
    abandoned: tuple[str, str] | None = None
    published: bool = False


class InMemoryHITLApplicationStore:
    def __init__(self) -> None:
        self._interactions: dict[str, StoredHITLInteraction] = {}
        self._answers: dict[tuple[str, int], DurableHITLAnswerRecord] = {}

    async def ensure_interaction(
        self,
        *,
        interaction_id: str,
        spec: A2AInteractionSpec,
        route: HITLRouteSnapshotV2,
        fingerprint: str,
    ) -> str:
        existing = self._interactions.get(interaction_id)
        if existing is not None:
            if (
                existing.spec == spec
                and existing.route == route
                and existing.fingerprint == fingerprint
            ):
                return "replayed"
            return "conflict"
        self._interactions[interaction_id] = StoredHITLInteraction(
            interaction_id=interaction_id,
            spec=_clone_spec(spec),
            route=_clone_route(route),
            fingerprint=fingerprint,
            eligible=False,
            published=False,
        )
        return "accepted"

    async def load_interaction(
        self, interaction_id: str
    ) -> StoredHITLInteraction | None:
        return self._interactions.get(interaction_id)

    async def get_eligible_interactions(
        self, room_id: str
    ) -> list[StoredHITLInteraction]:
        return [
            i
            for i in self._interactions.values()
            if i.route.room_id == room_id and i.eligible and i.abandoned is None
        ]

    async def get_published_interactions(
        self, room_id: str
    ) -> list[StoredHITLInteraction]:
        return [
            i
            for i in self._interactions.values()
            if i.route.room_id == room_id
            and i.eligible
            and i.abandoned is None
            and i.published
            and (i.interaction_id, i.route.interaction_revision) not in self._answers
        ]

    async def mark_eligible(self, interaction_id: str) -> str:
        stored = self._interactions.get(interaction_id)
        if stored is None:
            return "error"
        if stored.eligible:
            return "replayed"
        self._interactions[interaction_id] = StoredHITLInteraction(
            interaction_id=stored.interaction_id,
            spec=stored.spec,
            route=stored.route,
            fingerprint=stored.fingerprint,
            eligible=True,
            abandoned=stored.abandoned,
            published=stored.published,
        )
        return "accepted"

    async def mark_published(self, interaction_id: str) -> str:
        stored = self._interactions.get(interaction_id)
        if stored is None:
            return "error"
        if stored.published:
            return "replayed"
        self._interactions[interaction_id] = StoredHITLInteraction(
            interaction_id=stored.interaction_id,
            spec=stored.spec,
            route=stored.route,
            fingerprint=stored.fingerprint,
            eligible=stored.eligible,
            abandoned=stored.abandoned,
            published=True,
        )
        return "accepted"

    async def abandon(
        self, interaction_id: str, *, call_record_id: str, reason: str
    ) -> str:
        stored = self._interactions.get(interaction_id)
        if stored is None:
            return "absent"
        if stored.abandoned is not None:
            return "replayed" if stored.abandoned[0] == call_record_id else "conflict"
        lifecycle_close = reason.startswith("terminal_winner:") or (
            reason == "superseded_by_new_interaction"
        )
        if stored.route.call_record_id != call_record_id or (
            not lifecycle_close and not stored.eligible
        ):
            return "conflict"
        if (
            not lifecycle_close
            and (interaction_id, stored.route.interaction_revision) in self._answers
        ):
            return "conflict"
        self._interactions[interaction_id] = StoredHITLInteraction(
            interaction_id=stored.interaction_id,
            spec=stored.spec,
            route=stored.route,
            fingerprint=stored.fingerprint,
            eligible=False,
            abandoned=(call_record_id, reason),
            published=stored.published,
        )
        return "accepted"

    async def load_answer(
        self, interaction_id: str, interaction_revision: int
    ) -> DurableHITLAnswerRecord | None:
        record = self._answers.get((interaction_id, interaction_revision))
        return _clone_answer(record) if record is not None else None

    async def ensure_answer(
        self,
        *,
        interaction_id: str,
        interaction_revision: int,
        record: DurableHITLAnswerRecord,
    ) -> str:
        stored = self._interactions.get(interaction_id)
        if (
            stored is None
            or not stored.eligible
            or stored.abandoned is not None
            or stored.route.interaction_revision != interaction_revision
            or record.interaction_id != interaction_id
            or record.interaction_revision != interaction_revision
            or record.route_fingerprint != stored.route.fingerprint
        ):
            return "conflict"
        key = (interaction_id, interaction_revision)
        existing = self._answers.get(key)
        if existing is not None:
            return (
                "replayed"
                if _answer_identity(existing) == _answer_identity(record)
                else "conflict"
            )
        self._answers[key] = _clone_answer(record)
        return "accepted"


class DurableHITLApplicationPort:
    def __init__(self, *, hitl_store: HITLApplicationStore) -> None:
        self._hitl_store = hitl_store

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
        outcome = await self._hitl_store.ensure_interaction(
            interaction_id=interaction_id,
            spec=interaction,
            route=route,
            fingerprint=interaction_fingerprint,
        )
        if outcome == "conflict":
            raise ValueError("HITL interaction identity conflict")
        if (
            call.state in {"input_required", "auth_required"}
            and call.pending_interaction_id == interaction_id
            and call.interaction_fingerprint == interaction_fingerprint
        ):
            await self._hitl_store.mark_eligible(interaction_id)
        return interaction_id

    async def activate(
        self,
        interaction_id: str,
        *,
        call_record_id: str,
        interaction_fingerprint: str,
    ) -> str:
        stored = await self._hitl_store.load_interaction(interaction_id)
        if stored is None:
            return "error"
        if (
            stored.route.call_record_id != call_record_id
            or stored.fingerprint != interaction_fingerprint
            or stored.abandoned is not None
        ):
            return "conflict"
        return await self._hitl_store.mark_eligible(interaction_id)

    async def get_eligible_interactions(
        self, room_id: str
    ) -> list[tuple[A2AInteractionSpec, HITLRouteSnapshotV2, str]]:
        docs = await self._hitl_store.get_eligible_interactions(room_id)
        return [
            (
                A2AInteractionSpec.model_validate(doc.spec.model_dump(mode="python")),
                HITLRouteSnapshotV2.model_validate(doc.route.model_dump(mode="python")),
                doc.fingerprint,
            )
            for doc in docs
        ]

    async def get_published_interactions(
        self, room_id: str
    ) -> list[tuple[A2AInteractionSpec, HITLRouteSnapshotV2, str]]:
        docs = await self._hitl_store.get_published_interactions(room_id)
        actionable: list[StoredHITLInteraction] = []
        for doc in docs:
            answer = await self._hitl_store.load_answer(
                doc.interaction_id, doc.route.interaction_revision
            )
            if answer is None:
                actionable.append(doc)
        return [
            (
                A2AInteractionSpec.model_validate(doc.spec.model_dump(mode="python")),
                HITLRouteSnapshotV2.model_validate(doc.route.model_dump(mode="python")),
                doc.fingerprint,
            )
            for doc in actionable
        ]

    async def publish(self, interaction_id: str, *, call_record_id: str) -> str:
        stored = await self._hitl_store.load_interaction(interaction_id)
        if stored is None:
            return "error"
        if stored.route.call_record_id != call_record_id:
            return "conflict"
        return await self._hitl_store.mark_published(interaction_id)

    async def abandon(
        self,
        interaction_id: str,
        *,
        call_record_id: str,
        reason: str,
    ) -> str:
        stored = await self._hitl_store.load_interaction(interaction_id)
        if stored is None:
            return "absent"
        if stored.route.call_record_id != call_record_id:
            return "conflict"
        return await self._hitl_store.abandon(
            interaction_id, call_record_id=call_record_id, reason=reason
        )

    async def read_interaction(
        self, interaction_id: str
    ) -> tuple[A2AInteractionSpec, HITLRouteSnapshotV2, str] | None:
        stored = await self._hitl_store.load_interaction(interaction_id)
        if stored is None or not stored.eligible or stored.abandoned is not None:
            return None
        return _clone_spec(stored.spec), _clone_route(stored.route), stored.fingerprint

    async def read_answers(
        self, interaction_id: str, interaction_revision: int
    ) -> list[HITLQuestionAnswer] | None:
        stored = await self._hitl_store.load_interaction(interaction_id)
        if stored is None or not stored.eligible or stored.abandoned is not None:
            return None
        record = await self._hitl_store.load_answer(
            interaction_id, interaction_revision
        )
        return list(record.answers) if record is not None else None

    async def read_answer_record(
        self, interaction_id: str, interaction_revision: int
    ) -> DurableHITLAnswerRecord | None:
        return await self._hitl_store.load_answer(interaction_id, interaction_revision)

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
        stored = await self._hitl_store.load_interaction(interaction_id)
        if stored is None or not stored.eligible or stored.abandoned is not None:
            raise KeyError(interaction_id)
        if (
            stored.route.interaction_revision != interaction_revision
            or stored.route.fingerprint != route_fingerprint
        ):
            raise ValueError("HITL route fingerprint or revision changed")
        inventory = {
            question.question_id: question for question in stored.spec.questions
        }
        if set(inventory) != {answer.question_id for answer in answers}:
            raise ValueError("HITL answer inventory does not match")
        for answer in answers:
            inventory[answer.question_id].validate_answer(answer)
        answer_digest = _digest_json(
            [answer.model_dump(mode="json") for answer in answers]
        )
        record = DurableHITLAnswerRecord(
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
        outcome = await self._hitl_store.ensure_answer(
            interaction_id=interaction_id,
            interaction_revision=interaction_revision,
            record=record,
        )
        if outcome == "conflict":
            raise ValueError("HITL answer identity conflict")
        return answer_digest


def _clone_spec(spec: A2AInteractionSpec) -> A2AInteractionSpec:
    return A2AInteractionSpec.model_validate(spec.model_dump(mode="python"))


def _clone_route(route: HITLRouteSnapshotV2) -> HITLRouteSnapshotV2:
    return HITLRouteSnapshotV2.model_validate(route.model_dump(mode="python"))


def _clone_answer(record: DurableHITLAnswerRecord) -> DurableHITLAnswerRecord:
    return DurableHITLAnswerRecord.model_validate(record.model_dump(mode="python"))


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


def answers_identical(
    first: DurableHITLAnswerRecord, second: DurableHITLAnswerRecord
) -> bool:
    """Compare answer identity, ignoring the per-attempt ``applied_at``."""
    return _answer_identity(first) == _answer_identity(second)


def _digest_json(value: object) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256(canonical.encode()).hexdigest()


__all__ = [
    "DurableHITLApplicationPort",
    "HITLApplicationStore",
    "InMemoryHITLApplicationStore",
    "StoredHITLInteraction",
    "answers_identical",
]
