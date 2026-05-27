from __future__ import annotations

from common.dto import HubCancelCommand, HubDispatchCommand, HubDispatchResult, HubReplyCommand


class HubDispatchAdapter:
    def __init__(self, relay_service, *, liveness_cache=None) -> None:
        self._relay_service = relay_service
        self._liveness_cache = liveness_cache if liveness_cache is not None else {}

    async def send_to_hub(self, command: HubDispatchCommand) -> HubDispatchResult:
        return await self._relay_service.send_to_hub(command)

    async def cancel_hub_task(self, command: HubCancelCommand) -> bool:
        return await self._relay_service.cancel_hub_task(command)

    async def reply_to_hub_task(self, command: HubReplyCommand) -> bool:
        return await self._relay_service.reply_to_hub_task(command)

    def is_hub_online(self, hub_id: str) -> bool:
        return bool(self._liveness_cache.get(hub_id, False))


__all__ = ["HubDispatchAdapter"]
