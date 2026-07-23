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


__all__ = [
    "PaginationParams",
    "QueryFilter",
    "SortOrder",
]
