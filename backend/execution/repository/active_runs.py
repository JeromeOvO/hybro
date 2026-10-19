"""Room ids that have an active canonical orchestrator Run."""

from __future__ import annotations

from execution.orchestrator.persistence import (
    NON_TERMINAL_RUN_STATUSES,
    ORCHESTRATOR_RUNS_COLLECTION,
)


class CanonicalActiveRunRoomReader:
    """Reads ``orchestrator_runs`` with the active-room partial index predicate.

    Lookup errors propagate so callers that gate on this answer fail closed.
    """

    def __init__(self, mongo) -> None:
        self._runs = mongo.collection(ORCHESTRATOR_RUNS_COLLECTION)

    async def get_room_ids_with_non_terminal_runs(self) -> list[str]:
        ids = await self._runs.distinct(
            "room_id",
            {"status": {"$in": list(NON_TERMINAL_RUN_STATUSES)}},
        )
        return [str(room_id) for room_id in ids if room_id]
