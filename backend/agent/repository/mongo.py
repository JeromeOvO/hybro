from __future__ import annotations

from agent.url_utils import normalize_agent_url
from common.protocols import MongoDAL

_LEGACY_NORMALIZED_URL_SCAN_LIMIT = 500


class AgentMongoRepository:
    def __init__(self, mongo: MongoDAL, collection_name: str = "agents") -> None:
        self._agents = mongo.collection(collection_name)

    async def get_by_id(self, agent_id: str) -> dict | None:
        return await self._agents.find_one({"agent_id": agent_id})

    async def get_by_ids(self, agent_ids: list[str]) -> list[dict]:
        return await self._agents.find({"agent_id": {"$in": agent_ids}})

    async def get_by_provider(self, provider_id: str) -> list[dict]:
        return await self._agents.find({"provider_id": provider_id})

    async def get_by_source(self, source: str) -> list[dict]:
        return await self._agents.find({"source": source})

    async def get_public(self, limit: int = 50) -> list[dict]:
        return await self._agents.find(
            {"$or": [{"is_public": True}, {"is_public": {"$exists": False}}]},
            limit=limit,
        )

    async def find_by_normalized_url(
        self, normalized_url: str, provider_id: str | None = None
    ) -> dict | None:
        exact_query: dict = {"normalized_url": normalized_url}
        if provider_id is not None:
            exact_query["provider_id"] = provider_id
        exact = await self._agents.find_one(exact_query)
        if exact is not None:
            return exact

        legacy_query: dict = {"normalized_url": {"$exists": False}}
        if provider_id is not None:
            legacy_query["provider_id"] = provider_id
        legacy_docs = await self._agents.find(
            legacy_query,
            limit=_LEGACY_NORMALIZED_URL_SCAN_LIMIT,
        )
        for doc in legacy_docs:
            card_url = (doc.get("agent_card") or {}).get("url")
            if card_url and normalize_agent_url(card_url) == normalized_url:
                return doc
        return None

    async def list_visible(
        self,
        *,
        user_id: str | None = None,
        active_only: bool = False,
        agent_ids: list[str] | None = None,
        query: dict | None = None,
        limit: int = 0,
    ) -> list[dict]:
        visibility = [{"is_public": True}, {"is_public": {"$exists": False}}]
        if user_id is not None:
            visibility.append({"provider_id": user_id})

        conditions: list[dict] = []
        if query:
            conditions.append(dict(query))
        conditions.append({"$or": visibility})
        if active_only:
            conditions.append({"agent_status": "active"})
        if agent_ids is not None:
            conditions.append({"agent_id": {"$in": agent_ids}})

        final_query = conditions[0] if len(conditions) == 1 else {"$and": conditions}

        kwargs = {"limit": limit} if limit else {}
        return await self._agents.find(final_query, **kwargs)

    async def text_search(
        self, agent_ids: list[str], query: str, limit: int
    ) -> list[dict]:
        if not agent_ids:
            return []
        return await self._agents.find(
            {
                "agent_id": {"$in": agent_ids},
                "$text": {"$search": query},
            },
            projection={
                "agent_id": 1,
                "score": {"$meta": "textScore"},
            },
            sort=[("score", {"$meta": "textScore"})],
            limit=limit,
        )

    async def upsert(self, agent_id: str, data: dict) -> None:
        await self._agents.update_one(
            {"agent_id": agent_id},
            {"$set": {**data, "agent_id": agent_id}},
            upsert=True,
        )

    async def delete(self, agent_id: str) -> bool:
        return await self._agents.delete_one({"agent_id": agent_id})

    async def update(self, agent_id: str, updates: dict) -> dict | None:
        await self._agents.update_one(
            {"agent_id": agent_id},
            {"$set": updates},
        )
        return await self.get_by_id(agent_id)

    async def public_url_exists(self, subdomain: str, base_domain: str) -> bool:
        escaped = base_domain.replace(".", "\\.")
        count = await self._agents.count(
            {"public_url": {"$regex": f"://{subdomain}\\.{escaped}"}}
        )
        return count > 0

    async def update_health(self, agent_id: str, healthy: bool) -> None:
        status = "active" if healthy else "inactive"
        await self._agents.update_one(
            {"agent_id": agent_id},
            {"$set": {"agent_status": status}},
        )

    async def mark_agents_inactive(
        self,
        agent_ids: list[str],
        *,
        source: str,
    ) -> int:
        if not agent_ids:
            return 0
        return await self._agents.update_many(
            {
                "agent_id": {"$in": agent_ids},
                "source": source,
                "agent_status": {"$ne": "inactive"},
            },
            {"$set": {"agent_status": "inactive"}},
        )

    async def increment_agent_call_count(self, agent_id: str, *, success: bool) -> None:
        update = {"$inc": {"call_count": 1}}
        if success:
            update["$inc"]["call_success_count"] = 1
        await self._agents.update_one({"agent_id": agent_id}, update)

    async def activate_agents(self, agent_ids: list[str]) -> int:
        if not agent_ids:
            return 0
        return await self._agents.update_many(
            {"agent_id": {"$in": agent_ids}},
            {"$set": {"agent_status": "active"}},
        )
