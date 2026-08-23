"""Mongo-backed durable HITL application store for the orchestrator runtime.

Collection: ``orchestrator_hitl_interactions``. Index provisioning is owned by
``execution/orchestrator/a2a_runtime/persistence.py`` (consumed by
``_ensure_orchestrator_indexes``); constructing and injecting this store is
deferred to step 5b (container wiring).
"""

from __future__ import annotations

from typing import Any

from pymongo.errors import DuplicateKeyError

from common.dto.hitl import A2AInteractionSpec, HITLRouteSnapshotV2
from dal.orchestrator.stores import _restore_utc_datetimes
from execution.adapters.hitl import StoredHITLInteraction, answers_identical
from execution.orchestrator.a2a_runtime.models import DurableHITLAnswerRecord
from execution.orchestrator.a2a_runtime.persistence import (
    HITL_INTERACTIONS_COLLECTION,
)


class MongoHITLApplicationStore:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def ensure_interaction(
        self,
        *,
        interaction_id: str,
        spec: A2AInteractionSpec,
        route: HITLRouteSnapshotV2,
        fingerprint: str,
    ) -> str:
        existing = await self._collection.find_one({"interaction_id": interaction_id})
        if existing is not None:
            return (
                "replayed"
                if _same_interaction(existing, spec, route, fingerprint)
                else "conflict"
            )
        doc = {
            "interaction_id": interaction_id,
            "spec": spec.model_dump(mode="python"),
            "route": route.model_dump(mode="python"),
            "fingerprint": fingerprint,
            "eligible": False,
            "abandoned": None,
            "answers": {},
        }
        try:
            await self._collection.insert_one(doc)
        except DuplicateKeyError:
            winner = await self._collection.find_one({"interaction_id": interaction_id})
            if winner is None:
                raise
            return (
                "replayed"
                if _same_interaction(winner, spec, route, fingerprint)
                else "conflict"
            )
        return "accepted"

    async def load_interaction(
        self, interaction_id: str
    ) -> StoredHITLInteraction | None:
        doc = await self._collection.find_one({"interaction_id": interaction_id})
        if doc is None:
            return None
        return _interaction_from_doc(doc)

    async def mark_eligible(self, interaction_id: str) -> str:
        doc = await self._collection.find_one({"interaction_id": interaction_id})
        if doc is None:
            return "error"
        if doc.get("eligible"):
            return "replayed"
        result = await self._collection.update_one(
            {"interaction_id": interaction_id, "eligible": False},
            {"$set": {"eligible": True}},
        )
        if _changed(result):
            return "accepted"
        winner = await self._collection.find_one({"interaction_id": interaction_id})
        if winner is None:
            return "error"
        return "replayed" if winner.get("eligible") else "conflict"

    async def abandon(
        self, interaction_id: str, *, call_record_id: str, reason: str
    ) -> str:
        doc = await self._collection.find_one({"interaction_id": interaction_id})
        if doc is None:
            return "absent"
        abandoned = doc.get("abandoned")
        if abandoned:
            return (
                "replayed"
                if abandoned.get("call_record_id") == call_record_id
                else "conflict"
            )
        result = await self._collection.update_one(
            {"interaction_id": interaction_id, "abandoned": None},
            {
                "$set": {
                    "abandoned": {"call_record_id": call_record_id, "reason": reason},
                    "eligible": False,
                }
            },
        )
        if _changed(result):
            return "accepted"
        winner = await self._collection.find_one({"interaction_id": interaction_id})
        if winner is None:
            return "absent"
        winner_abandoned = winner.get("abandoned")
        if not winner_abandoned:
            return "conflict"
        return (
            "replayed"
            if winner_abandoned.get("call_record_id") == call_record_id
            else "conflict"
        )

    async def load_answer(
        self, interaction_id: str, interaction_revision: int
    ) -> DurableHITLAnswerRecord | None:
        doc = await self._collection.find_one({"interaction_id": interaction_id})
        if doc is None:
            return None
        answers = doc.get("answers") or {}
        raw = answers.get(str(interaction_revision))
        if raw is None:
            return None
        return DurableHITLAnswerRecord.model_validate(raw)

    async def ensure_answer(
        self,
        *,
        interaction_id: str,
        interaction_revision: int,
        record: DurableHITLAnswerRecord,
    ) -> str:
        result = await self._collection.update_one(
            {
                "interaction_id": interaction_id,
                f"answers.{interaction_revision}": {"$exists": False},
            },
            {
                "$set": {
                    f"answers.{interaction_revision}": record.model_dump(mode="python")
                }
            },
        )
        if getattr(result, "matched_count", 0):
            return "accepted"
        winner = await self.load_answer(interaction_id, interaction_revision)
        if winner is None:
            return "error"
        return "replayed" if answers_identical(winner, record) else "conflict"


def _same_interaction(
    doc: dict[str, Any],
    spec: A2AInteractionSpec,
    route: HITLRouteSnapshotV2,
    fingerprint: str,
) -> bool:
    return (
        doc.get("fingerprint") == fingerprint
        and _restore_utc_datetimes(doc.get("spec")) == spec.model_dump(mode="python")
        and _restore_utc_datetimes(doc.get("route")) == route.model_dump(mode="python")
    )


def _interaction_from_doc(doc: dict[str, Any]) -> StoredHITLInteraction:
    abandoned = doc.get("abandoned")
    abandoned_tuple = (
        (str(abandoned["call_record_id"]), str(abandoned["reason"]))
        if isinstance(abandoned, dict)
        else None
    )
    return StoredHITLInteraction(
        interaction_id=str(doc["interaction_id"]),
        spec=A2AInteractionSpec.model_validate(_restore_utc_datetimes(doc.get("spec"))),
        route=HITLRouteSnapshotV2.model_validate(
            _restore_utc_datetimes(doc.get("route"))
        ),
        fingerprint=str(doc.get("fingerprint") or ""),
        eligible=bool(doc.get("eligible")),
        abandoned=abandoned_tuple,
    )


def _changed(result: Any) -> bool:
    return bool(
        getattr(result, "modified_count", 0) or getattr(result, "matched_count", 0)
    )


__all__ = ["HITL_INTERACTIONS_COLLECTION", "MongoHITLApplicationStore"]
