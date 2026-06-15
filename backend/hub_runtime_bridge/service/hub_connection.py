from __future__ import annotations

from common.dto import HubAgentCounts, HubInfo


class HubConnectionService:
    def __init__(self, *, repository, liveness_reader, status_reader=None) -> None:
        self._repository = repository
        self._liveness = liveness_reader
        self._status_reader = status_reader

    async def register_hub(self, hub_id: str, owner_id: str) -> HubInfo:
        data = {"hub_id": hub_id, "user_id": owner_id}
        await self._repository.upsert(hub_id, data)
        return HubInfo(hub_id=hub_id, owner_id=owner_id)

    async def get_hub(self, hub_id: str) -> HubInfo | None:
        data = await self._repository.get_by_id(hub_id)
        return await self._to_info(data) if data else None

    async def list_hubs(self, owner_id: str) -> list[HubInfo]:
        return [await self._to_info(item) for item in await self._repository.get_by_owner(owner_id)]

    async def _to_info(self, data: dict) -> HubInfo:
        hub_id = data["hub_id"]
        counts = HubAgentCounts()
        if self._status_reader:
            counts = await self._status_reader.count_hub_agents(hub_id)
        online = await self._liveness.is_hub_online(hub_id)
        return HubInfo(
            hub_id=hub_id,
            owner_id=data.get("user_id") or data.get("owner_id", ""),
            is_online=online,
            agent_count=counts.active + counts.inactive,
            active_agent_count=counts.active,
            inactive_agent_count=counts.inactive,
        )


__all__ = ["HubConnectionService"]
