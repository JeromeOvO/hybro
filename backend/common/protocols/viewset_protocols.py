from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel

from common.protocols.json_types import JsonMap, JsonValue  # noqa: F401

RoutePayload: TypeAlias = BaseModel | JsonMap  # noqa: UP040
ViewSetResult: TypeAlias = BaseModel | JsonMap | None  # noqa: UP040
ViewSetOperation: TypeAlias = Callable[[], Awaitable[ViewSetResult]]  # noqa: UP040


@dataclass(frozen=True)
class ViewSetPaginationParams:
    page: int = 1
    limit: int = 10

    @property
    def skip(self) -> int:
        return (self.page - 1) * self.limit


@dataclass(frozen=True)
class ViewSetFilterParams:
    filters: JsonMap | None = None
    sort_by: str | None = None
    sort_order: int = -1


@runtime_checkable
class ViewSetTransaction(Protocol):
    def start_transaction(self) -> AbstractAsyncContextManager[None]: ...


@runtime_checkable
class ViewSetSessionContext(Protocol):
    async def __aenter__(self) -> ViewSetTransaction: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class ViewSetDatabaseClient(Protocol):
    def start_session(self) -> Awaitable[ViewSetSessionContext]: ...


@runtime_checkable
class ViewSetDatabase(Protocol):
    client: ViewSetDatabaseClient


@runtime_checkable
@runtime_checkable
class ViewSetRepository(Protocol):
    async def create(self, data: RoutePayload) -> ViewSetResult: ...
    async def delete(self, item_id: str | int) -> bool | ViewSetResult: ...
    async def get(self, item_id: str | int) -> ViewSetResult: ...
    async def get_all(
        self,
        pagination: ViewSetPaginationParams | None = None,
        filters: ViewSetFilterParams | None = None,
    ) -> ViewSetResult: ...
    async def patch(self, item_id: str | int, data: RoutePayload) -> ViewSetResult: ...
    async def update(self, item_id: str | int, data: RoutePayload) -> ViewSetResult: ...


@runtime_checkable
class ViewSetDatabaseProvider(Protocol):
    def __call__(self) -> ViewSetDatabase: ...


@runtime_checkable
class ViewSetRepositoryFactory(Protocol):
    def __call__(
        self,
        *,
        collection_name: str,
        db: ViewSetDatabase,
        pk_field: str = "_id",
    ) -> ViewSetRepository: ...


@runtime_checkable
class ViewSetRepositoryProvider(Protocol):
    def get_repository(
        self, *, collection_name: str, pk_field: str = "_id"
    ) -> ViewSetRepository: ...
    async def run_in_transaction(
        self, operation: ViewSetOperation
    ) -> ViewSetResult: ...


__all__ = [
    "RoutePayload",
    "ViewSetDatabase",
    "ViewSetDatabaseClient",
    "ViewSetDatabaseProvider",
    "ViewSetFilterParams",
    "ViewSetOperation",
    "ViewSetPaginationParams",
    "ViewSetRepository",
    "ViewSetRepositoryFactory",
    "ViewSetRepositoryProvider",
    "ViewSetResult",
    "ViewSetSessionContext",
    "ViewSetTransaction",
]
