"""SDK-confined agent-card health probing helpers."""

from __future__ import annotations

from dataclasses import dataclass

from a2a.types import AgentCard as SDKAgentCard

from common.types import AgentCard

from .constants import AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH


@dataclass(frozen=True)
class AgentCardHealthResult:
    is_healthy: bool
    card: AgentCard | None
    status_code: int | None = None


async def fetch_agent_card_for_health(
    agent_url: str,
    client,
) -> AgentCardHealthResult:
    """Fetch and parse an agent card for health sync without leaking SDK types."""
    base_url = agent_url.rstrip("/")
    response = await client.get(base_url + AGENT_CARD_WELL_KNOWN_PATH)

    if response.status_code == 404:
        response = await client.get(base_url + PREV_AGENT_CARD_WELL_KNOWN_PATH)

    is_healthy = response.status_code < 400
    if not is_healthy:
        return AgentCardHealthResult(
            is_healthy=False,
            card=None,
            status_code=response.status_code,
        )

    try:
        sdk_card = SDKAgentCard(**response.json())
        card = AgentCard.model_validate(sdk_card.model_dump(mode="json"))
    except Exception:
        card = None

    return AgentCardHealthResult(
        is_healthy=True,
        card=card,
        status_code=response.status_code,
    )


__all__ = ["AgentCardHealthResult", "fetch_agent_card_for_health"]
