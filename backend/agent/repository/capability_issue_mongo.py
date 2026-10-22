from __future__ import annotations

from pymongo import ReturnDocument

from common.protocols import MongoDAL
from common.utils.mongo_indexes import create_index


class AgentCapabilityIssueMongoRepository:
    def __init__(
        self,
        mongo: MongoDAL,
        collection_name: str = "agent_capability_issues",
    ) -> None:
        self._issues = mongo.collection(collection_name)

    async def ensure_indexes(self) -> None:
        for keys, name, unique in (
            ([("agent_id", 1), ("status", 1)], "agent_id_status", False),
            ([("status", 1), ("agent_id", 1)], "status_agent_id", False),
            ([("created_at", 1)], "created_at", False),
            ([("issue_id", 1)], "issue_id_unique", True),
        ):
            await create_index(
                self._issues,
                "agent_capability_issues",
                keys,
                name=name,
                unique=unique,
            )

    async def insert(self, issue: dict) -> str:
        inserted = await self._issues.insert_one(dict(issue))
        return str(inserted)

    async def list_excluded_agent_ids(self, *, threshold: int) -> set[str]:
        pipeline = [
            {"$match": {"status": "open"}},
            {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gte": threshold}}},
        ]
        docs = await self._issues.aggregate(pipeline)
        return {
            str(doc["_id"])
            for doc in docs
            if doc is not None and doc.get("_id") is not None
        }

    async def get_by_id(self, issue_id: str) -> dict | None:
        return await self._issues.find_one({"issue_id": issue_id})

    async def list_for_agent(
        self,
        agent_id: str,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        query: dict[str, str] = {"agent_id": agent_id}
        if status is not None:
            query["status"] = status
        return await self._issues.find(
            query,
            sort=[("created_at", -1)],
            skip=offset,
            limit=limit,
        )

    async def resolve(
        self,
        issue_id: str,
        provider_id: str,
        resolved_at,
    ) -> dict | None:
        return await self._issues.find_one_and_update(
            {"issue_id": issue_id, "status": "open"},
            {
                "$set": {
                    "status": "resolved",
                    "resolved_at": resolved_at,
                    "resolved_by": provider_id,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def resolve_all_for_agent(
        self,
        agent_id: str,
        provider_id: str,
        resolved_at,
    ) -> int:
        return await self._issues.update_many(
            {"agent_id": agent_id, "status": "open"},
            {
                "$set": {
                    "status": "resolved",
                    "resolved_at": resolved_at,
                    "resolved_by": provider_id,
                }
            },
        )
