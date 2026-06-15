from __future__ import annotations

from typing import Any

from common.protocols import MongoDAL


class RoomQuoteMongoRepository:
    def __init__(self, mongo: MongoDAL, collection_name: str = "room_quotes") -> None:
        self._quotes = mongo.collection(collection_name)

    async def insert(self, snippet: dict | Any) -> str:
        """Insert a quoted snippet record and return its quote id."""
        if hasattr(snippet, "model_dump"):
            payload = snippet.model_dump(mode="json")
        else:
            payload = dict(snippet)
        await self._quotes.insert_one(payload)
        return str(payload.get("quote_id"))

    async def get_by_id(self, quote_id: str) -> dict | None:
        return await self._quotes.find_one({"quote_id": quote_id})

    async def delete_by_id(self, quote_id: str) -> bool:
        return await self._quotes.delete_one({"quote_id": quote_id})

    async def delete_for_room(self, room_id: str) -> int:
        return await self._quotes.delete_many({"room_id": room_id})
