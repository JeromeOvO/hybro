from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import datetime
from typing import Any

from common.utils.time import utcnow


class HubMongoRepository:
    def __init__(self, mongo: Any, *, clock: Callable[[], datetime] = utcnow) -> None:
        self._mongo = mongo
        self._clock = clock

    async def get_by_id(self, hub_id: str) -> dict | None:
        if hasattr(self._mongo, "get_hub"):
            return await self._mongo.get_hub(hub_id)
        return await self._collection().find_one({"hub_id": hub_id})

    async def get_by_owner(self, owner_id: str) -> list[dict]:
        if hasattr(self._mongo, "get_hubs_by_user"):
            return await self._mongo.get_hubs_by_user(owner_id)
        return await _find_all(self._collection(), {"user_id": owner_id})

    async def upsert(self, hub_id: str, data: dict) -> None:
        if hasattr(self._mongo, "upsert_hub"):
            await self._mongo.upsert_hub(data)
            return
        await self._collection().update_one(
            {"hub_id": hub_id}, {"$set": data}, upsert=True
        )

    async def update_heartbeat(self, hub_id: str) -> None:
        await self.update_hub_status(
            hub_id, last_heartbeat_at=self._clock(), is_online=True
        )

    async def get_stale(self, threshold: datetime) -> list[dict]:
        return await _find_all(
            self._collection(),
            {"last_heartbeat_at": {"$lt": threshold}, "is_online": True},
        )

    async def list_online_hubs_for_liveness(self) -> list[dict]:
        return await _find_all(
            self._collection(),
            {"is_online": True},
            projection={"hub_id": 1, "connection_id": 1},
        )

    async def list_offline_hubs_for_recovery(self, limit: int) -> list[dict]:
        return await _find_all(
            self._collection(),
            {"is_online": False},
            projection={"hub_id": 1},
            limit=limit,
        )

    async def update_hub_status(self, hub_id: str, **fields) -> None:
        if hasattr(self._mongo, "update_hub_status"):
            await self._mongo.update_hub_status(hub_id, **fields)
            return
        await self._collection().update_one({"hub_id": hub_id}, {"$set": fields})

    async def update_hub_status_if_current(
        self, hub_id: str, connection_id: str, **fields
    ) -> bool:
        if hasattr(self._mongo, "update_hub_status_if_current"):
            return bool(
                await self._mongo.update_hub_status_if_current(
                    hub_id, connection_id=connection_id, **fields
                )
            )
        doc = await self._collection().find_one_and_update(
            {"hub_id": hub_id, "connection_id": connection_id}, {"$set": fields}
        )
        return doc is not None

    def _collection(self) -> Any:
        if hasattr(self._mongo, "hubs_collection"):
            return self._mongo.hubs_collection
        return self._mongo.collection("hubs")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _find_all(
    collection: Any,
    query: dict,
    *,
    projection: dict | None = None,
    limit: int | None = None,
) -> list[dict]:
    try:
        find_call = (
            collection.find(query, projection)
            if projection is not None
            else collection.find(query)
        )
    except TypeError:
        find_call = collection.find(query, projection=projection)
    result = await _maybe_await(find_call)
    to_list = getattr(result, "to_list", None)
    if to_list is not None:
        return await _maybe_await(to_list(length=limit))
    rows = list(result or [])
    return rows[:limit] if limit is not None else rows


__all__ = ["HubMongoRepository"]
