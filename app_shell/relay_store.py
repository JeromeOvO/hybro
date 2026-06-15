from __future__ import annotations

from typing import Any

from common.protocols import AgentRepository, HubRepository, MongoDAL


class AppShellRelayHubStore:
    """Relay compatibility store backed by DAL repositories."""

    def __init__(
        self,
        *,
        mongo: MongoDAL,
        hub_repository: HubRepository,
        agent_repository: AgentRepository,
    ) -> None:
        self._mongo = mongo
        self._hub_repository = hub_repository
        self._agent_repository = agent_repository

    def collection(self, name: str):
        return self._mongo.collection(name)

    async def upsert_hub(self, hub: dict) -> None:
        hub_id = str(hub.get("hub_id") or "")
        if not hub_id:
            raise ValueError("hub_id is required")
        await self._hub_repository.upsert(hub_id, dict(hub))

    async def get_hub(self, hub_id: str) -> dict | None:
        return await self._hub_repository.get_by_id(hub_id)

    async def get_hubs_by_user(self, user_id: str) -> list[dict]:
        return await self._hub_repository.get_by_owner(user_id)

    async def update_hub_status(self, hub_id: str, **fields: Any) -> None:
        await self._hub_repository.update_hub_status(hub_id, **fields)

    async def update_hub_status_if_current(
        self,
        hub_id: str,
        connection_id: str,
        **fields: Any,
    ) -> bool:
        return await self._hub_repository.update_hub_status_if_current(
            hub_id,
            connection_id=connection_id,
            **fields,
        )

    async def count_hub_agents(self, hub_id: str) -> tuple[int, int]:
        return await self._agent_repository.count_hub_agents(hub_id)

    async def increment_agent_call_count(self, agent_id: str, *, success: bool) -> None:
        await self._agent_repository.increment_agent_call_count(
            agent_id,
            success=success,
        )
