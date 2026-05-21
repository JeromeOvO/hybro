from __future__ import annotations

from typing import Any


class RateLimitCollectionAdapter:
    def __init__(self, collection: Any, collection_name: str) -> None:
        self._collection = collection
        self.collection_name = collection_name

    async def count_documents(self, query: dict) -> int:
        if hasattr(self._collection, "count_documents"):
            return await self._collection.count_documents(query)
        return await self._collection.count(query)

    async def find_one(
        self, query: dict, sort: list[tuple[str, int]] | None = None
    ) -> dict | None:
        return await self._collection.find_one(query, sort=sort)

    async def insert_one(self, doc: dict):
        return await self._collection.insert_one(doc)


class MongoFileMetadataRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create(self, data: dict) -> str:
        await self._collection.insert_one(data)
        return data["file_id"]

    async def get(self, file_id: str) -> dict | None:
        return await self._collection.find_one({"file_id": file_id})

    async def delete(self, file_id: str) -> bool:
        return await self._collection.delete_one({"file_id": file_id})

    async def list_for_room(self, room_id: str) -> list[dict]:
        return await self._collection.find({"room_id": room_id})
