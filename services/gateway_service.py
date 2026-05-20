"""Compatibility shim for the legacy gateway service import path.

Gateway behavior is owned by ``platform_module.gateway`` and is bound into
``api.gateway`` during application startup. This module remains only for
temporary legacy imports until the services package is removed.
"""

from collections.abc import AsyncIterator

from common.protocols import GatewayService as GatewayServiceProtocol


class GatewayService:
    def __init__(
        self,
        delegate: GatewayServiceProtocol | None = None,
        **_legacy_dependencies: object,
    ) -> None:
        self._delegate = delegate

    def bind(self, delegate: GatewayServiceProtocol) -> None:
        self._delegate = delegate

    def _require_delegate(self) -> GatewayServiceProtocol:
        if self._delegate is None:
            raise RuntimeError(
                "services.gateway_service is a legacy shim and has not been bound "
                "to the Platform gateway service"
            )
        return self._delegate

    async def discover_agents(
        self, query: str, limit: int | None, user_id: str
    ) -> object:
        return await self._require_delegate().discover_agents(query, limit, user_id)

    async def get_agent_card(self, agent_id: str, user_id: str) -> dict[str, object]:
        return await self._require_delegate().get_agent_card(agent_id, user_id)

    async def send_message(
        self, agent_id: str, message: object, user_id: str
    ) -> object:
        return await self._require_delegate().send_message(agent_id, message, user_id)

    async def prepare_stream(
        self, agent_id: str, message: object, user_id: str
    ) -> AsyncIterator[dict[str, object]]:
        return await self._require_delegate().prepare_stream(agent_id, message, user_id)

    async def stream_message(
        self, agent_id: str, message: object, user_id: str
    ) -> AsyncIterator[dict[str, object]]:
        async for event in self._require_delegate().stream_message(
            agent_id, message, user_id
        ):
            yield event

    def mask_agent_card_dict(
        self, agent_card_dict: dict[str, object], agent_id: str
    ) -> dict[str, object]:
        delegate = self._require_delegate()
        mask = getattr(delegate, "mask_agent_card_dict", None)
        if not callable(mask):
            raise RuntimeError("Bound Platform gateway does not expose card masking")
        return mask(agent_card_dict, agent_id)


def bind_gateway_service(delegate: GatewayServiceProtocol) -> None:
    gateway_service.bind(delegate)


gateway_service = GatewayService()
