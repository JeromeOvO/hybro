from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from common.config import settings
from common.protocols import MongoChangeStream


class MongoCollectionAdapter:
    """Thin adapter from Motor collection operations to MongoCollection."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def find_one(self, query: dict, **kwargs) -> dict | None:
        return await self._collection.find_one(query, **kwargs)

    async def find(self, query: dict, **kwargs) -> list[dict]:
        projection = kwargs.pop("projection", None)
        limit = kwargs.pop("limit", None)
        skip = kwargs.pop("skip", None)
        sort = kwargs.pop("sort", None)
        exhaust = kwargs.pop("exhaust", False)
        length = None if exhaust else (limit if limit is not None else 1000)

        if projection is not None:
            cursor = self._collection.find(query, projection=projection, **kwargs)
        else:
            cursor = self._collection.find(query, **kwargs)

        if sort is not None:
            cursor = cursor.sort(sort)
        if skip is not None:
            cursor = cursor.skip(skip)
        if limit is not None:
            cursor = cursor.limit(limit)

        return await cursor.to_list(length=length)

    async def find_one_and_update(
        self,
        query: dict,
        update: dict | list[dict],
        **kwargs,
    ) -> dict | None:
        return await self._collection.find_one_and_update(query, update, **kwargs)

    async def insert_one(self, document: dict) -> str:
        result = await self._collection.insert_one(document)
        return str(result.inserted_id)

    async def insert_many(self, documents: list[dict]) -> list[str]:
        result = await self._collection.insert_many(documents)
        return [str(inserted_id) for inserted_id in result.inserted_ids]

    async def update_one(
        self, query: dict, update: dict | list[dict], **kwargs
    ) -> bool:
        result = await self._collection.update_one(query, update, **kwargs)
        return result.modified_count > 0 or result.upserted_id is not None

    async def replace_one(self, query: dict, replacement: dict, **kwargs) -> bool:
        result = await self._collection.replace_one(query, replacement, **kwargs)
        return result.modified_count > 0 or result.upserted_id is not None

    async def update_many(self, query: dict, update: dict) -> int:
        result = await self._collection.update_many(query, update)
        return result.modified_count

    async def delete_one(self, query: dict) -> bool:
        result = await self._collection.delete_one(query)
        return result.deleted_count > 0

    async def delete_many(self, query: dict) -> int:
        result = await self._collection.delete_many(query)
        return result.deleted_count

    async def count(self, query: dict) -> int:
        return await self._collection.count_documents(query)

    async def aggregate(self, pipeline: list[dict]) -> list[dict]:
        cursor = self._collection.aggregate(pipeline)
        return await cursor.to_list(length=1000)

    async def create_index(self, keys: list[tuple], **kwargs) -> str:
        return await self._collection.create_index(keys, **kwargs)

    async def create_indexes(self, indexes: list, **kwargs) -> list[str]:
        return await self._collection.create_indexes(indexes, **kwargs)

    async def index_information(self) -> dict:
        return await self._collection.index_information()

    async def drop_index(self, name: str) -> None:
        await self._collection.drop_index(name)

    async def bulk_write(self, operations: list, **kwargs) -> Any:
        result = await self._collection.bulk_write(operations, **kwargs)
        return result.raw_result if hasattr(result, "raw_result") else result

    async def find_one_by_stable_or_native_id(
        self, stable_id_field: str, id_value: str
    ) -> dict | None:
        doc = await self._collection.find_one({stable_id_field: id_value})
        if doc is not None:
            return doc

        try:
            from bson import ObjectId

            if ObjectId.is_valid(id_value):
                return await self._collection.find_one({"_id": ObjectId(id_value)})
        except Exception:
            return None
        return None

    async def distinct(self, key: str, query: dict | None = None) -> list:
        return await self._collection.distinct(key, query or {})

    def watch(self, pipeline: list[dict] | None = None, **kwargs) -> MongoChangeStream:
        return self._collection.watch(pipeline or [], **kwargs)


class MongoDALImpl:
    """MongoDB DAL backed directly by Motor."""

    def __init__(
        self,
        *,
        client: AsyncIOMotorClient | None = None,
        database: Any | None = None,
        url: str | None = None,
        db_name: str | None = None,
    ) -> None:
        self._client = client
        self._db = database
        self._url = url or settings.mongodb_url
        self._db_name = db_name or settings.mongodb_db_name

    def collection(self, name: str) -> MongoCollectionAdapter:
        if self._db is None:
            if self._client is None:
                raise ConnectionError("MongoDB client is not connected")
            self._db = self._client[self._db_name]
        return MongoCollectionAdapter(self._db[name])

    async def connect(self) -> None:
        if self._client is None:
            kwargs: dict[str, Any] = {}
            max_pool_size = getattr(settings, "mongodb_max_pool_size", None)
            min_pool_size = getattr(settings, "mongodb_min_pool_size", None)
            if max_pool_size is not None:
                kwargs["maxPoolSize"] = max_pool_size
            if min_pool_size is not None:
                kwargs["minPoolSize"] = min_pool_size
            self._client = AsyncIOMotorClient(self._url, **kwargs)
        self._db = self._client[self._db_name]
        await self._client.admin.command("ping")

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._db = None

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.admin.command("ping")
            return True
        except Exception:
            return False
