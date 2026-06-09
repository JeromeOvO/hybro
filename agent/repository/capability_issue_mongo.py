from __future__ import annotations

from pymongo import ReturnDocument

from common.protocols import MongoDAL


class AgentCapabilityIssueMongoRepository:
    def __init__(
        self,
        mongo: MongoDAL,
        collection_name: str = "agent_capability_issues",
    ) -> None:
        self._issues = mongo.collection(collection_name)

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
