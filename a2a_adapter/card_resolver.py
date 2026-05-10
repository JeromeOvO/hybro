import logging
import time

import httpx
from a2a.types import AgentCard

from common.dto import AgentCardSnapshot

from .translators import a2a_card_to_snapshot


logger = logging.getLogger(__name__)


class AgentCardResolverImpl:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        cache_ttl: int = 300,
        timeout: int = 10,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, AgentCardSnapshot]] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def resolve_card(self, agent_url: str) -> AgentCardSnapshot | None:
        normalized_url = agent_url.rstrip("/")
        cached = self._cache.get(normalized_url)
        now = time.monotonic()
        if cached and now - cached[0] < self._cache_ttl:
            return cached[1]

        try:
            response = await self._client.get(
                f"{normalized_url}/.well-known/agent.json"
            )
            response.raise_for_status()
            payload = response.json()
            card = AgentCard(**payload)
            snapshot = a2a_card_to_snapshot(card, normalized_url)
        except Exception as exc:
            logger.warning(
                "Failed to resolve A2A agent card for %s: %s",
                normalized_url,
                exc,
                exc_info=True,
            )
            return None

        self._cache[normalized_url] = (now, snapshot)
        return snapshot

    async def supports_push_notifications(self, agent_url: str) -> bool:
        card = await self.resolve_card(agent_url)
        if card is None:
            return False
        accepted = {"push_notifications", "push-notifications", "pushNotifications"}
        return bool(accepted & set(card.capabilities))

    async def supports_streaming(self, agent_url: str) -> bool:
        card = await self.resolve_card(agent_url)
        if card is None:
            return False
        accepted = {"streaming", "stream", "message/stream"}
        return bool(accepted & set(card.capabilities))


__all__ = ["AgentCardResolverImpl"]
