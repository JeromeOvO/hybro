from __future__ import annotations

from models.run import NON_TERMINAL_RUN_STATE_VALUES


class RunMongoRepository:
    def __init__(self, mongo, collection_name: str = "runs") -> None:
        self._runs = mongo.collection(collection_name)

    async def create(self, run: dict) -> str:
        await self._runs.insert_one(dict(run))
        return str(run["run_id"])

    async def find_one(self, query: dict) -> dict | None:
        return await self._runs.find_one(query)

    async def find(
        self, query: dict, *, sort: list[tuple[str, int]] | None = None, limit: int = 0
    ) -> list[dict]:
        kwargs: dict = {}
        if sort is not None:
            kwargs["sort"] = sort
        if limit:
            kwargs["limit"] = limit
        return await self._runs.find(query, **kwargs)

    async def insert_one(self, document: dict) -> str:
        return await self._runs.insert_one(document)

    async def update_one(self, query: dict, update: dict) -> bool:
        return await self._runs.update_one(query, update)

    async def get_by_id(self, run_id: str) -> dict | None:
        return await self._runs.find_one({"run_id": run_id})

    async def get_active_for_room(self, room_id: str) -> list[dict]:
        return await self.find(
            {
                "room_id": room_id,
                "state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)},
            },
            sort=[("updated_at", -1)],
            limit=0,
        )

    async def get_for_room(self, room_id: str) -> list[dict]:
        return await self.find(
            {"room_id": room_id},
            sort=[("updated_at", -1)],
            limit=0,
        )

    async def get_latest_for_rooms(self, room_ids: list[str]) -> list[dict]:
        if not room_ids:
            return []
        return await self._runs.aggregate(
            [
                {
                    "$match": {
                        "room_id": {"$in": room_ids},
                        "state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)},
                    }
                },
                {
                    "$addFields": {
                        "_history_status_priority": {
                            "$switch": {
                                "branches": [
                                    {
                                        "case": {"$eq": ["$state", "awaiting_input"]},
                                        "then": 3,
                                    },
                                    {
                                        "case": {"$eq": ["$state", "processing"]},
                                        "then": 2,
                                    },
                                    {
                                        "case": {"$eq": ["$state", "queued"]},
                                        "then": 1,
                                    },
                                ],
                                "default": 0,
                            }
                        }
                    }
                },
                {
                    "$sort": {
                        "room_id": 1,
                        "_history_status_priority": -1,
                        "updated_at": -1,
                    }
                },
                {"$group": {"_id": "$room_id", "run": {"$first": "$$ROOT"}}},
                {"$replaceRoot": {"newRoot": "$run"}},
                {"$unset": "_history_status_priority"},
            ]
        )

    async def update_state(self, run_id: str, state: str, **fields) -> bool:
        payload = {"$set": {"state": state, **fields}}
        return await self._runs.update_one({"run_id": run_id}, payload)

    async def update(self, run_id: str, update: dict) -> bool:
        return await self._runs.update_one({"run_id": run_id}, update)

    async def get_diverged(self, limit: int) -> list[dict]:
        return await self.find(
            {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
            sort=None,
            limit=limit,
        )

    async def get_room_ids_with_non_terminal_runs(self) -> list[str]:
        ids = await self._runs.distinct(
            "room_id",
            {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
        )
        return [str(room_id) for room_id in ids if room_id]


class RunEventMongoRepository:
    def __init__(self, mongo, collection_name: str = "run_events") -> None:
        self._events = mongo.collection(collection_name)

    async def append(self, run_id: str, event: dict) -> str:
        payload = dict(event)
        payload["run_id"] = run_id
        await self._events.insert_one(payload)
        return str(payload["event_id"])

    async def insert_one(self, document: dict) -> str:
        return await self._events.insert_one(document)

    async def get_for_run(self, run_id: str) -> list[dict]:
        return await self._events.find({"run_id": run_id}, sort=[("seq", 1)])

    async def get_latest(self, run_id: str) -> dict | None:
        rows = await self._events.find({"run_id": run_id}, sort=[("seq", -1)], limit=1)
        return rows[0] if rows else None

    async def find(
        self, query: dict, *, sort: list[tuple[str, int]] | None = None, limit: int = 0
    ) -> list[dict]:
        kwargs: dict = {}
        if sort is not None:
            kwargs["sort"] = sort
        if limit:
            kwargs["limit"] = limit
        return await self._events.find(query, **kwargs)

    async def find_one(self, query: dict, *, sort: list[tuple[str, int]] | None = None):
        kwargs: dict = {"limit": 1}
        if sort is not None:
            kwargs["sort"] = sort
        rows = await self._events.find(query, **kwargs)
        return rows[0] if rows else None

    async def find_one_and_update(self, query: dict, update: dict, **kwargs):
        return await self._events.find_one_and_update(query, update, **kwargs)

    async def update_one(self, query: dict, update: dict) -> bool:
        return await self._events.update_one(query, update)
