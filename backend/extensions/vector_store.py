from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class VectorStoreError(Exception):
    """Provider-neutral vector store operation failure.

    Adapters must wrap provider SDK and transport failures in this exception so
    future consumers never need to depend on provider-specific error types.
    """


@dataclass(frozen=True, slots=True)
class VectorRecord:
    id: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    id: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    """Optional vector storage seam.

    Implementations must normalize provider-specific distance or similarity
    values so a larger ``score`` always means a more relevant result.
    Operational failures must be raised as :class:`VectorStoreError`.
    """

    async def search(
        self,
        namespace: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[VectorSearchResult]: ...

    async def upsert(
        self,
        namespace: str,
        records: list[VectorRecord],
    ) -> None: ...

    async def delete(self, namespace: str, ids: list[str]) -> None: ...

    async def ping(self) -> bool: ...


__all__ = [
    "VectorRecord",
    "VectorSearchResult",
    "VectorStore",
    "VectorStoreError",
]
