from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class QueryFilter(FrozenDTO):
    criteria: dict[str, Any] = Field(default_factory=dict)


class PaginationParams(FrozenDTO):
    page: int = 1
    limit: int = 20


class SortOrder(FrozenDTO):
    field: str
    direction: str = "asc"


class VectorRecord(FrozenDTO):
    id: str
    vector: list[float]
    metadata: dict[str, Any] = Field(default_factory=dict)


class VectorSearchResult(FrozenDTO):
    id: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "PaginationParams",
    "QueryFilter",
    "SortOrder",
    "VectorRecord",
    "VectorSearchResult",
]
