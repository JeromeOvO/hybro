"""Process-global binding for the orchestrator room epoch store.

The epoch store is a Mongo repository injected at runtime startup. Keeping the
binding in ``dal.orchestrator`` (rather than the production-unbound
``execution.orchestrator`` runtime) lets the runtime package stay free of
production composition concerns while still exposing a fail-fast dependency
for consumers that need the durable epoch store.
"""

from __future__ import annotations

from typing import Any

from .stores import MongoRoomEpochStore

_room_epoch_store: MongoRoomEpochStore | None = None


def bind_room_epoch_store(store: MongoRoomEpochStore) -> None:
    global _room_epoch_store

    _room_epoch_store = store


def reset_room_epoch_store() -> None:
    global _room_epoch_store

    _room_epoch_store = None


def require_room_epoch_store() -> MongoRoomEpochStore:
    if _room_epoch_store is None:
        raise RuntimeError("MongoRoomEpochStore has not been bound")
    return _room_epoch_store


class EpochScopedOrchestratorCleanup:
    """Delete all seven orchestrator collections at exact (room_id, epoch) scope.

    Room-state cleanup intentionally skips every orchestrator collection
    (``excluded_from_room_state_delete``) so a deletion can only remove the
    exact incarnation's data: bindings, calls, observations, and conflicts use
    their ``delete_by_epoch`` stores; Runs are deleted by
    ``request.room_epoch``; Run events use the event store's epoch delete. The
    epoch row itself is the tombstone/high-water mark and is never deleted
    here.
    """

    def __init__(
        self,
        *,
        bindings: Any,
        calls: Any,
        observations: Any,
        conflicts: Any,
        runs: Any,
        run_events: Any,
    ) -> None:
        self._bindings = bindings
        self._calls = calls
        self._observations = observations
        self._conflicts = conflicts
        self._runs = runs
        self._run_events = run_events

    async def delete_by_epoch(self, room_id: str, room_epoch: int) -> int:
        deleted = 0
        for store in (
            self._bindings,
            self._calls,
            self._observations,
            self._conflicts,
            self._run_events,
        ):
            deleted += await store.delete_by_epoch(room_id, room_epoch)
        result = await self._runs.delete_many(
            {"room_id": room_id, "request.room_epoch": room_epoch}
        )
        deleted += int(getattr(result, "deleted_count", 0) or 0)
        return deleted
