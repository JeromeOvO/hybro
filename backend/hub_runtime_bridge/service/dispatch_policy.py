from __future__ import annotations


class HubDispatchPolicy:
    def __init__(self, liveness_reader) -> None:
        self._liveness_reader = liveness_reader

    async def can_dispatch_to_hub(self, hub_id: str, agent_id: str) -> bool:
        return bool(await self._liveness_reader.is_hub_online(hub_id))


__all__ = ["HubDispatchPolicy"]
