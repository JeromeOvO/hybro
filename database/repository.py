import math

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pydantic import BaseModel
from pymongo.results import InsertOneResult

from models.request import FilterParams, PaginationParams
from models.response import PaginatedResponse, PaginationMeta


class Repository:
    def __init__(
        self,
        collection_name: str,
        db: AsyncIOMotorDatabase,
        pinecone,
        pk_field: str = "_id",
    ):
        self.db = db
        self.pinecone = pinecone
        self.collection: AsyncIOMotorCollection = getattr(db, collection_name)
        self.pk_field = pk_field

    # implement generic methods
    async def get_all(
        self,
        pagination: PaginationParams | None = None,
        filters: FilterParams | None = None,
    ):
        query = {}
        if filters and filters.filters:
            query.update(filters.filters)

        # Get total count for pagination metadata
        total = await self.collection.count_documents(query)

        # Build the cursor with filters
        cursor = self.collection.find(query)

        # Apply sorting if specified
        if filters and filters.sort_by:
            cursor = cursor.sort(filters.sort_by, filters.sort_order)

        # Apply pagination if specified, otherwise use default pagination
        if not pagination:
            # Default pagination when none specified - return all items in one page
            pagination = PaginationParams(page=1, limit=total if total > 0 else 10)

        cursor = cursor.skip(pagination.skip).limit(pagination.limit)

        # Calculate pagination metadata
        total_pages = math.ceil(total / pagination.limit) if total > 0 else 0
        has_next = pagination.page < total_pages
        has_prev = pagination.page > 1

        meta = PaginationMeta(
            page=pagination.page,
            limit=pagination.limit,
            total=total,
            total_pages=total_pages,
            has_next=has_next,
            has_prev=has_prev,
        )

        items = await cursor.to_list(length=pagination.limit)
        return PaginatedResponse(items=items, meta=meta)

    async def get(self, item_id):
        return await self.collection.find_one({self.pk_field: item_id})

    async def create(self, item):
        item_dict = item.dict()
        result: InsertOneResult = await self.collection.insert_one(item_dict)
        return await self.collection.find_one(
            {self.pk_field: item_dict.get(self.pk_field, result.inserted_id)}
        )

    async def update(self, item_id: str, item: BaseModel):
        await self.collection.update_one(
            {self.pk_field: item_id}, {"$set": item.model_dump()}
        )
        return await self.get(item_id)

    async def delete(self, item_id: str):
        await self.collection.delete_one({self.pk_field: item_id})
        return {"deleted": True}

    async def patch(self, item_id: str, item: BaseModel):
        update_data = item.model_dump(exclude_unset=True)
        await self.collection.update_one(
            {self.pk_field: item_id}, {"$set": update_data}
        )
        return await self.get(item_id)
