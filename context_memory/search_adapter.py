from __future__ import annotations

from typing import Any

from common.utils.time import utcnow


class ContextMemorySearchAdapter:
    """Compatibility adapter for app shell memory search consumers.

    Delegates legacy-oriented operations to a ``ContextMemoryFacade``-compatible
    object while keeping the app shell's existing response surface.
    """

    def __init__(self, facade: Any | None = None) -> None:
        self._facade = facade

    def bind_facade(self, facade: Any) -> None:
        self._facade = facade

    def _require_facade(self):
        if self._facade is None:
            raise RuntimeError(
                "ContextMemorySearchAdapter has no facade bound. "
                "Call bind_facade() before use."
            )
        return self._facade

    async def search(
        self,
        query: str,
        room_id: str,
        user_id: str | None = None,
        limit: int | None = None,
    ):
        facade = self._require_facade()
        payload = await facade.legacy_search(
            query=query,
            room_id=room_id,
            user_id=user_id,
            limit=limit,
        )
        return _coerce_search_payload(payload)

    async def index_turn_for_search(self, room_id: str, turn_doc: dict) -> bool:
        facade = self._require_facade()
        return await facade.index_turn_for_search(room_id, turn_doc)

    async def delete_room_index(self, room_id: str) -> bool:
        facade = self._require_facade()
        return await facade.delete_room_index(room_id)


def _coerce_search_payload(payload: Any) -> dict:
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "query"):
        return {
            "query": payload.query,
            "room_id": payload.room_id,
            "results": list(payload.results),
            "total_matches": payload.total_matches,
            "search_time_ms": payload.search_time_ms,
            "searched_at": payload.searched_at or utcnow(),
            "vector_search_used": payload.vector_search_used,
            "keyword_search_used": payload.keyword_search_used,
            "temporal_decay_applied": payload.temporal_decay_applied,
            "mmr_applied": payload.mmr_applied,
        }
    return {
        "query": "",
        "room_id": "",
        "results": [],
        "total_matches": 0,
        "search_time_ms": 0.0,
        "searched_at": utcnow(),
        "vector_search_used": False,
        "keyword_search_used": False,
        "temporal_decay_applied": False,
        "mmr_applied": False,
    }
