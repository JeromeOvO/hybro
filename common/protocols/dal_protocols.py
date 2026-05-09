from typing import Any, AsyncIterator, Protocol, runtime_checkable

from common.dto import QueryFilter, VectorRecord, VectorSearchResult


@runtime_checkable
class MongoCollection(Protocol):
    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None: ...
    def find(self, query: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...
    async def insert_one(self, document: dict[str, Any]) -> Any: ...
    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any]
    ) -> Any: ...
    async def delete_one(self, query: dict[str, Any]) -> Any: ...


@runtime_checkable
class MongoDAL(Protocol):
    def collection(self, name: str) -> MongoCollection: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class RedisKV(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(
        self, key: str, value: str, expire_seconds: int | None = None
    ) -> None: ...
    async def delete(self, key: str) -> int: ...


@runtime_checkable
class RedisPubSub(Protocol):
    async def publish(self, channel: str, message: str) -> int: ...
    async def subscribe(self, channel: str) -> AsyncIterator[str]: ...


@runtime_checkable
class RedisStreams(Protocol):
    async def append(self, stream: str, values: dict[str, Any]) -> str: ...
    async def read(
        self, stream: str, last_id: str = "$", count: int | None = None
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class VectorDAL(Protocol):
    async def upsert(self, records: list[VectorRecord]) -> None: ...
    async def query(
        self, vector: list[float], top_k: int, filters: QueryFilter | None = None
    ) -> list[VectorSearchResult]: ...
    async def delete(self, ids: list[str]) -> None: ...


@runtime_checkable
class ObjectStorageDAL(Protocol):
    async def put_object(
        self, key: str, content: bytes, content_type: str | None = None
    ) -> None: ...
    async def get_object(self, key: str) -> bytes | None: ...
    async def delete_object(self, key: str) -> None: ...


@runtime_checkable
class DistributedLock(Protocol):
    async def acquire(self, key: str, ttl_seconds: int) -> bool: ...
    async def release(self, key: str) -> None: ...


@runtime_checkable
class LeaderElector(Protocol):
    async def is_leader(self, key: str) -> bool: ...
    async def campaign(self, key: str, ttl_seconds: int) -> bool: ...


@runtime_checkable
class IndexRegistry(Protocol):
    async def ensure_indexes(self) -> None: ...
    async def list_indexes(self) -> list[str]: ...


__all__ = [
    "DistributedLock",
    "IndexRegistry",
    "LeaderElector",
    "MongoCollection",
    "MongoDAL",
    "ObjectStorageDAL",
    "RedisKV",
    "RedisPubSub",
    "RedisStreams",
    "VectorDAL",
]
