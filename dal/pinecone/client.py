from __future__ import annotations

import asyncio
from typing import Any

import pinecone

from common.config import settings
from common.dto import VectorRecord, VectorSearchResult
from common.errors import VectorIndexUnavailableError


class VectorDALImpl:
    """Vector DAL backed directly by the synchronous Pinecone SDK."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        index_name: str | None = None,
    ) -> None:
        self._client = client
        self._api_key = settings.pinecone_api_key if api_key is None else api_key
        self._default_index = index_name or settings.pinecone_index_name
        self._indexes: dict[str, Any] = {}

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = pinecone.Pinecone(api_key=self._api_key)
        return self._client

    def _get_index(self, index: str) -> Any:
        if index not in self._indexes:
            self._indexes[index] = self._get_client().Index(index)
        return self._indexes[index]

    async def search(
        self,
        index: str,
        vector: list[float],
        top_k: int,
        filter: dict | None = None,
    ) -> list[VectorSearchResult]:
        pinecone_index = self._get_index(index)
        try:
            response = await asyncio.to_thread(
                pinecone_index.query,
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                filter=filter,
            )
        except Exception as exc:
            if _is_index_unavailable(exc):
                raise VectorIndexUnavailableError(index, "search") from exc
            raise
        matches = _response_value(response, "matches", [])
        return [
            VectorSearchResult(
                id=_response_value(match, "id", ""),
                score=_response_value(match, "score", 0.0),
                metadata=_response_value(match, "metadata", None) or {},
            )
            for match in matches
        ]

    async def upsert(self, index: str, records: list[VectorRecord]) -> None:
        pinecone_index = self._get_index(index)
        vectors = [
            {
                "id": record.id,
                "values": list(record.vector),
                "metadata": dict(record.metadata),
            }
            for record in records
        ]
        try:
            await asyncio.to_thread(pinecone_index.upsert, vectors=vectors)
        except Exception as exc:
            if _is_index_unavailable(exc):
                raise VectorIndexUnavailableError(index, "upsert") from exc
            raise

    async def delete(self, index: str, ids: list[str]) -> None:
        pinecone_index = self._get_index(index)
        await asyncio.to_thread(pinecone_index.delete, ids=ids)

    async def delete_by_filter(self, index: str, filter: dict) -> None:
        pinecone_index = self._get_index(index)
        try:
            await asyncio.to_thread(pinecone_index.delete, filter=filter)
        except Exception as exc:
            if _is_index_unavailable(exc):
                raise VectorIndexUnavailableError(index, "delete_by_filter") from exc
            raise

    async def ping(self) -> bool:
        try:
            pinecone_index = self._get_index(self._default_index)
            await asyncio.to_thread(pinecone_index.describe_index_stats)
            return True
        except Exception:
            return False


def _response_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _is_index_unavailable(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return "notfound" in name or "not found" in message or "404" in message
