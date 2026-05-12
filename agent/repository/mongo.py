from __future__ import annotations

from common.protocols import MongoDAL

from agent.constants import AGENT_CARD_NO_OVERWRITE
from agent.url_utils import normalize_agent_url

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

    async def mark_hub_agents_offline(self, hub_id: str) -> int:
        return await self._agents.update_many(
            {"hub_id": hub_id, "agent_status": "active"},
            {"$set": {"agent_status": "inactive"}},
        )

    async def upsert_hub_agent(
        self, hub_id: str, local_agent_id: str, data: dict
    ) -> str:
        query = {"hub_id": hub_id, "local_agent_id": local_agent_id}
        update = _hub_agent_upsert_update({**data, **query})
        doc = await self._find_one_and_update_retrying_normalized_url_collision(
            query,
            update,
            upsert=True,
            return_document=True,
        )
        return doc["agent_id"]

    async def _find_one_and_update_retrying_normalized_url_collision(
        self,
        query: dict,
        update: dict,
        **kwargs,
    ) -> dict:
        try:
            doc = await self._agents.find_one_and_update(query, update, **kwargs)
        except Exception as exc:
            if (
                update.get("$set", {}).get("normalized_url") is None
                or not _is_duplicate_key_error(exc)
            ):
                raise
            retry_update = {
                **update,
                "$set": {**update["$set"], "normalized_url": None},
            }
            doc = await self._agents.find_one_and_update(
                query,
                retry_update,
                **kwargs,
            )
        if doc is None:
            raise RuntimeError("hub agent upsert did not return a document")
        return doc

    async def prune_missing_hub_agents(
        self, hub_id: str, active_agent_ids: list[str]
    ) -> int:
        hub_pruned = await self._agents.update_many(
            {
                "hub_id": hub_id,
                "source": "hub",
                "agent_id": {"$nin": active_agent_ids},
            },
            {"$set": {"agent_status": "inactive"}},
        )
        enriched_pruned = await self._agents.update_many(
            {
                "hub_id": hub_id,
                "source": {"$ne": "hub"},
                "agent_id": {"$nin": active_agent_ids},
            },
            {
                "$set": {"agent_status": "inactive"},
                "$unset": {"hub_id": "", "local_agent_id": ""},
            },
        )
        return hub_pruned + enriched_pruned

    async def activate_agents(self, agent_ids: list[str]) -> int:
        if not agent_ids:
            return 0
        return await self._agents.update_many(
            {"agent_id": {"$in": agent_ids}},
            {"$set": {"agent_status": "active"}},
        )

    async def get_indexed_description_hash(self, agent_id: str) -> str | None:
        doc = await self.get_by_id(agent_id)
        if doc is None:
            return None
        return doc.get("indexed_description_hash") or doc.get("description_hash")

    async def set_indexed_description_hash(self, agent_id: str, desc_hash: str) -> None:
        await self._agents.update_one(
            {"agent_id": agent_id},
            {
                "$set": {
                    "indexed_description_hash": desc_hash,
                    "description_hash": desc_hash,
                }
            },
        )


def _hub_agent_upsert_update(data: dict) -> dict:
    set_data = dict(data)
    agent_id = set_data.pop("agent_id")
    is_public = set_data.pop("is_public", None)
    incoming_card = dict(set_data.pop("agent_card", {}) or {})
    for key, value in incoming_card.items():
        if key not in AGENT_CARD_NO_OVERWRITE:
            set_data[f"agent_card.{key}"] = value
    set_on_insert = {"agent_id": agent_id}
    if is_public is not None:
        set_on_insert["is_public"] = is_public
    return {
        "$set": set_data,
        "$setOnInsert": set_on_insert,
    }


def _is_duplicate_key_error(exc: Exception) -> bool:
    return (
        exc.__class__.__name__ == "DuplicateKeyError"
        or getattr(exc, "code", None) == 11000
    )
