from __future__ import annotations

from typing import Any

from common.dto.base import to_stored_conversation_keys
from common.protocols import MongoDAL
from common.utils.mongo_indexes import create_index
from common.utils.room_state import delete_room_rows


class RoomQuoteMongoRepository:
    def __init__(self, mongo: MongoDAL, collection_name: str = "room_quotes") -> None:
        self._quotes = mongo.collection(collection_name)

    async def delete_room_state(self, conversation_id: str) -> bool:
        return await delete_room_rows((self._quotes,), conversation_id)

    async def ensure_indexes(self) -> None:
        await create_index(
            self._quotes,
            "room_quotes",
            [("quote_id", 1)],
            name="quote_id_unique",
            unique=True,
        )
        await create_index(
            self._quotes,
            "room_quotes",
            [("room_id", 1)],
            name="room_id_lookup",
        )

    async def insert(self, snippet: dict | Any) -> str:
        """Insert a quoted snippet record and return its quote id."""
        if hasattr(snippet, "model_dump"):
            payload = to_stored_conversation_keys(snippet.model_dump(mode="json"))
        else:
            payload = to_stored_conversation_keys(snippet)
        await self._quotes.insert_one(payload)
        return str(payload.get("quote_id"))

    async def get_by_id(self, quote_id: str) -> dict | None:
        return await self._quotes.find_one({"quote_id": quote_id})

    async def delete_by_id(self, quote_id: str) -> bool:
        return await self._quotes.delete_one({"quote_id": quote_id})

    async def delete_for_room(self, conversation_id: str) -> int:
        return await self._quotes.delete_many({"room_id": conversation_id})
