"""Mongo repository for the durable orchestrator Run event inventory."""

from __future__ import annotations

from pymongo.errors import DuplicateKeyError

from execution.orchestrator.events import evaluate_event_append
from execution.orchestrator.models import OrchestratorEvent
from execution.orchestrator.ports import StoreOutcome

from .stores import (
    AsyncMongoCollection,
    _bounded,
    _from_document,
    _to_list,
    _without_mongo_id,
)


class MongoOrchestratorEventStore:
    """Append events behind the pure ordering/idempotency evaluation.

    The ``(event_id)`` and ``(run_id, sequence)`` unique indexes arbitrate
    concurrent appenders: an insert that loses the race is re-read so an exact
    winner is classified as ``replayed`` while a divergent occupant is
    ``conflict``. Contiguous-sequence enforcement comes from
    ``evaluate_event_append`` before any write.
    """

    def __init__(self, collection: AsyncMongoCollection) -> None:
        self.collection = _bounded(collection)

    async def append(self, event: OrchestratorEvent) -> StoreOutcome:
        existing = await self.read(event.run_id)
        evaluation = evaluate_event_append(existing, event)
        if evaluation.outcome != "accepted":
            return evaluation.outcome
        try:
            await self.collection.insert_one(
                _without_mongo_id(event.model_dump(mode="python"))
            )
        except DuplicateKeyError:
            current = await self.collection.find_one({"event_id": event.event_id})
            if current is None:
                return "conflict"
            if _from_document(OrchestratorEvent, current) == event:
                return "replayed"
            return "conflict"
        return "accepted"

    async def read(
        self, run_id: str, *, after_sequence: int = 0
    ) -> list[OrchestratorEvent]:
        cursor = self.collection.find({"run_id": run_id})
        events = sorted(
            (
                _from_document(OrchestratorEvent, value)
                for value in await _to_list(cursor)
            ),
            key=lambda item: item.sequence,
        )
        return [event for event in events if event.sequence > after_sequence]

    async def delete_by_epoch(self, room_id: str, room_epoch: int) -> int:
        result = await self.collection.delete_many(
            {"room_id": room_id, "room_epoch": room_epoch}
        )
        return int(getattr(result, "deleted_count", 0))


__all__ = ["MongoOrchestratorEventStore"]
