import math
from typing import Any

from pydantic import BaseModel

from app_shell.bound import (
    ViewSetDatabaseProvider,
    ViewSetOperation,
    ViewSetRepository,
    ViewSetRepositoryFactory,
    ViewSetResult,
)
from common.protocols import MongoDAL
from models.request import FilterParams, PaginationParams
from models.response import PaginatedResponse, PaginationMeta


class AppShellViewSetRepositoryProvider:
    def __init__(
        self,
        *,
        db_provider: ViewSetDatabaseProvider,
        create_repository: ViewSetRepositoryFactory,
    ) -> None:
        self._db_provider = db_provider
        self._create_repository = create_repository

    def get_repository(
        self, *, collection_name: str, pk_field: str = "_id"
    ) -> ViewSetRepository:
        return self._create_repository(
            collection_name=collection_name,
            db=self._db_provider(),
            pinecone=None,
            pk_field=pk_field,
        )

    async def run_in_transaction(
        self, operation: ViewSetOperation
    ) -> ViewSetResult:
        db = self._db_provider()
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                return await operation()


class AppShellDALViewSetRepository:
    def __init__(self, *, mongo: MongoDAL, collection_name: str, pk_field: str) -> None:
        self._collection = mongo.collection(collection_name)
        self._pk_field = pk_field

    async def get_all(
        self,
        pagination: PaginationParams | None = None,
        filters: FilterParams | None = None,
    ):
        query = dict(filters.filters) if filters and filters.filters else {}
        total = await self._collection.count(query)

        if pagination is None:
            pagination = PaginationParams(page=1, limit=total if total > 0 else 10)

        sort = [(filters.sort_by, filters.sort_order)] if filters and filters.sort_by else None
        items = await self._collection.find(
            query,
            limit=pagination.limit,
            skip=pagination.skip,
            sort=sort,
        )
        total_pages = math.ceil(total / pagination.limit) if total > 0 else 0
        return PaginatedResponse(
            items=items,
            meta=PaginationMeta(
                page=pagination.page,
                limit=pagination.limit,
                total=total,
                total_pages=total_pages,
                has_next=pagination.page < total_pages,
                has_prev=pagination.page > 1,
            ),
        )

    async def get(self, item_id: str | int):
        return await self._collection.find_one({self._pk_field: item_id})

    async def create(self, item):
        item_dict = _payload_to_dict(item)
        inserted_id = await self._collection.insert_one(item_dict)
        lookup_value = item_dict.get(self._pk_field, inserted_id)
        return await self.get(lookup_value)

    async def update(self, item_id: str | int, item: BaseModel):
        await self._collection.update_one(
            {self._pk_field: item_id},
            {"$set": item.model_dump()},
        )
        return await self.get(item_id)

    async def delete(self, item_id: str | int):
        await self._collection.delete_one({self._pk_field: item_id})
        return {"deleted": True}

    async def patch(self, item_id: str | int, item: BaseModel):
        await self._collection.update_one(
            {self._pk_field: item_id},
            {"$set": item.model_dump(exclude_unset=True)},
        )
        return await self.get(item_id)


class AppShellDALViewSetRepositoryProvider:
    def __init__(self, *, mongo: MongoDAL) -> None:
        self._mongo = mongo

    def get_repository(
        self, *, collection_name: str, pk_field: str = "_id"
    ) -> ViewSetRepository:
        return AppShellDALViewSetRepository(
            mongo=self._mongo,
            collection_name=collection_name,
            pk_field=pk_field,
        )

    async def run_in_transaction(
        self, operation: ViewSetOperation
    ) -> ViewSetResult:
        return await operation()


def _payload_to_dict(item: Any) -> dict:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if hasattr(item, "dict"):
        return item.dict()
    return dict(item)


__all__ = [
    "AppShellDALViewSetRepositoryProvider",
    "AppShellViewSetRepositoryProvider",
]
