from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from agent.url_utils import DOCKER_HOST_ALIAS, normalize_agent_url
from common.dto import AgentCardSnapshot
from common.protocols import AgentCardResolver
from common.url_utils import LOCAL_HOST_ALIASES

_PROBE_CONCURRENCY = 30


class LocalAgentCardProbe:
    def __init__(self, *, host: str, resolver: AgentCardResolver) -> None:
        self._host = host
        self._resolver = resolver
        self._allowed_card_hosts = {
            *LOCAL_HOST_ALIASES,
            DOCKER_HOST_ALIAS,
            host.lower(),
        }

    async def probe_agent_cards(
        self,
        ports: list[int],
    ) -> list[tuple[str, AgentCardSnapshot]]:
        if not ports:
            return []

        queue: asyncio.Queue[int] = asyncio.Queue()
        for port in ports:
            queue.put_nowait(port)

        discovered: list[tuple[str, AgentCardSnapshot]] = []

        async def worker() -> None:
            while True:
                try:
                    port = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    discovery_url = f"http://{self._host}:{port}"
                    card = await self._resolver.resolve_card(discovery_url)
                    if card is not None and self._matches_discovered_endpoint(card, port):
                        discovered.append((discovery_url, card))
                finally:
                    queue.task_done()

        worker_count = min(_PROBE_CONCURRENCY, len(ports))
        await asyncio.gather(
            *(asyncio.create_task(worker()) for _ in range(worker_count))
        )

        unique: dict[str, tuple[str, AgentCardSnapshot]] = {}
        for discovery_url, card in discovered:
            identity = normalize_agent_url(card.url or discovery_url)
            if identity:
                unique.setdefault(identity, (discovery_url, card))
        return list(unique.values())

    def _matches_discovered_endpoint(
        self,
        card: AgentCardSnapshot,
        discovered_port: int,
    ) -> bool:
        """Keep discovery bounded to the host endpoint that served the card."""
        try:
            parsed = urlparse(card.url)
            hostname = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"}:
                return False
            if hostname not in self._allowed_card_hosts:
                return False
            advertised_port = parsed.port
        except ValueError:
            return False

        if advertised_port is None:
            advertised_port = 443 if parsed.scheme == "https" else 80
        return advertised_port == discovered_port


__all__ = ["LocalAgentCardProbe"]
