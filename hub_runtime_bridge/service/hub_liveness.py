from __future__ import annotations


class HubLivenessService:
    def __init__(self, *, repository=None, streams=None, local_is_connected=None) -> None:
        self._repository = repository
        self._streams = streams
        self._local_is_connected = local_is_connected or (lambda hub_id: False)

    async def is_hub_online(self, hub_id: str) -> bool:
        if self._streams:
            return bool(await self._streams.is_hub_alive(hub_id))
        return bool(self._local_is_connected(hub_id))

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        if not self._repository:
            return None
        hub = await self._repository.get_by_id(hub_id)
        return hub.get("user_id") if hub else None


__all__ = ["HubLivenessService"]
