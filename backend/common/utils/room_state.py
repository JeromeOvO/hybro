"""Room-state deletion shared by the modules that own room-scoped rows."""

from __future__ import annotations

from collections.abc import Iterable

from common.protocols import MongoCollection


async def delete_room_rows(
    collections: Iterable[MongoCollection], conversation_id: str
) -> bool:
    """Delete every row for ``conversation_id``, then report whether none remain."""
    collections = tuple(collections)
    for collection in collections:
        await collection.delete_many({"room_id": conversation_id})
    for collection in collections:
        if await collection.count({"room_id": conversation_id}):
            return False
    return True


__all__ = ["delete_room_rows"]
