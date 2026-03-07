"""
Gateway Service for Hybro Hub Phase 1

Provides authenticated proxy access to cloud agents for external SDK/hub consumers.
Handles agent lookup, access control, URL masking, usage tracking, and delegation
to A2AService and DiscoveryService.
"""

import copy
from collections.abc import AsyncGenerator

from a2a.types import (
    AgentCard,
    Message,
    SendMessageResponse,
    SendStreamingMessageResponse,
)
from fastapi import HTTPException, status
from pydantic import BaseModel

from common.utils.logger import get_logger
from config.settings import settings
from database.mongodb import mongodb
from models.agent import Agent, AgentStatus
from services.a2a_service import A2AService
from services.a2a_service import a2a_service as _default_a2a_service
from services.discovery_service import DiscoveryService, discovery_service
from services.rate_limit_service import RateLimitService, rate_limit_service

logger = get_logger(__name__)


class GatewayDiscoveryAgentResult(BaseModel):
    """Discovery result enriched with agent_id for gateway consumers."""
    agent_id: str
    agent_card: dict
    match_score: float


class GatewayDiscoveryResponse(BaseModel):
    """Gateway discovery response with agent_id on each result."""
    query: str
    agents: list[GatewayDiscoveryAgentResult]
    count: int


class GatewayService:
    def __init__(
        self,
        a2a_svc: A2AService | None = None,
        discovery_svc: DiscoveryService | None = None,
        rate_limit_svc: RateLimitService | None = None,
    ):
        self._a2a_service = a2a_svc or _default_a2a_service
        self._discovery_service = discovery_svc or discovery_service
        self._rate_limit_service = rate_limit_svc or rate_limit_service

    @property
    def a2a_service(self) -> A2AService:
        return self._a2a_service

    def _gateway_url_for_agent(self, agent_id: str) -> str:
        base = settings.gateway_base_url.rstrip("/") if settings.gateway_base_url else settings.api_prefix
        return f"{base}/gateway/agents/{agent_id}/message/send"

    def mask_agent_card_dict(self, agent_card_dict: dict, agent_id: str) -> dict:
        """Rewrite URL fields in a serialised AgentCard dict to point at the gateway."""
        masked = copy.deepcopy(agent_card_dict)
        if "url" in masked:
            masked["url"] = self._gateway_url_for_agent(agent_id)
        interfaces = masked.get("supportedInterfaces")
        if interfaces and isinstance(interfaces, list):
            for iface in interfaces:
                if isinstance(iface, dict) and "url" in iface:
                    iface["url"] = self._gateway_url_for_agent(agent_id)
        return masked

    def mask_agent_card(self, agent_card: AgentCard, agent_id: str) -> dict:
        """Serialize a typed AgentCard and mask its URL."""
        return self.mask_agent_card_dict(
            agent_card.model_dump(mode="json"), agent_id
        )

    async def get_agent_for_gateway(self, agent_id: str, user_id: str) -> Agent:
        """
        Look up an agent by ID and enforce access control.

        Raises:
            HTTPException 404 if agent doesn't exist or is not active.
            HTTPException 403 if the caller has no access.
        """
        agent = await mongodb.get_agent_by_agent_id(agent_id)
        if agent is None or agent.agent_status != AgentStatus.active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "agent_not_found", "message": "Agent not found or inactive"},
            )
        if not agent.is_public and agent.provider_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "access_denied", "message": "You do not have access to this agent"},
            )
        return agent

    async def discover_agents(
        self, query: str, limit: int | None, user_id: str
    ) -> GatewayDiscoveryResponse:
        """Discover agents and return results with gateway-masked URLs and agent_ids."""
        from services.agent_service import normalize_agent_url

        result = await self._discovery_service.discover_agents(query=query, limit=limit)

        url_to_results: dict[str, list] = {}
        for agent_result in result.agents:
            url = agent_result.agent_card.get("url")
            if url:
                url_to_results.setdefault(str(url), []).append(agent_result)

        if not url_to_results:
            return GatewayDiscoveryResponse(query=result.query, agents=[], count=0)

        raw_urls = list(url_to_results.keys())
        normalized_map = {normalize_agent_url(u): u for u in raw_urls}

        agents = await mongodb.get_agents_with_conditions(
            {"normalized_url": {"$in": list(normalized_map.keys())}},
        )
        url_to_agent_id: dict[str, str] = {}
        for agent in agents:
            if agent.normalized_url and agent.normalized_url in normalized_map:
                url_to_agent_id[normalized_map[agent.normalized_url]] = agent.agent_id

        unresolved = [u for u in raw_urls if u not in url_to_agent_id]
        if unresolved:
            fallback_agents = await mongodb.get_agents_with_conditions(
                {"agent_card.url": {"$in": unresolved}},
            )
            for agent in fallback_agents:
                card_url = str(agent.agent_card.url) if agent.agent_card else None
                if card_url and card_url in url_to_results:
                    url_to_agent_id[card_url] = agent.agent_id

        masked_agents: list[GatewayDiscoveryAgentResult] = []
        for agent_result in result.agents:
            url = str(agent_result.agent_card.get("url", ""))
            agent_id = url_to_agent_id.get(url)
            if not agent_id:
                continue
            masked_card = self.mask_agent_card_dict(agent_result.agent_card, agent_id)
            masked_agents.append(
                GatewayDiscoveryAgentResult(
                    agent_id=agent_id,
                    agent_card=masked_card,
                    match_score=agent_result.match_score,
                )
            )
        return GatewayDiscoveryResponse(query=result.query, agents=masked_agents, count=len(masked_agents))

    async def get_agent_card(self, agent_id: str, user_id: str) -> dict:
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        return self.mask_agent_card(agent.agent_card, agent_id)

    async def send_message(
        self, agent_id: str, message: Message, user_id: str
    ) -> SendMessageResponse:
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        if agent.source == "hub":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "hub_agent_not_directly_callable",
                    "message": "Hub-sourced agents cannot be called directly via the gateway. "
                    "Use the platform UI or relay API instead.",
                },
            )
        await self._check_agent_rate_limit(agent, user_id)

        try:
            response = await self.a2a_service.send_message_sync(agent.agent_card, message)
        except Exception as e:
            logger.error(f"Gateway send_message failed for agent {agent_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": "agent_error", "message": f"Agent communication failed: {e}"},
            ) from e

        await self._record_agent_call(agent_id, success=response is not None)
        return response

    async def prepare_stream(
        self, agent_id: str, message: Message, user_id: str
    ) -> AsyncGenerator[SendStreamingMessageResponse | SendMessageResponse, None]:
        """Validate access eagerly and return an async generator for streaming.

        Raises HTTPException (404/403/429) *before* any SSE bytes are sent,
        so the caller can return a proper HTTP error status.
        """
        agent = await self.get_agent_for_gateway(agent_id, user_id)
        if agent.source == "hub":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "hub_agent_not_directly_callable",
                    "message": "Hub-sourced agents cannot be called directly via the gateway. "
                    "Use the platform UI or relay API instead.",
                },
            )
        await self._check_agent_rate_limit(agent, user_id)
        return self._stream_events(agent, message, agent_id)

    async def _stream_events(
        self, agent: Agent, message: Message, agent_id: str
    ) -> AsyncGenerator[SendStreamingMessageResponse | SendMessageResponse, None]:
        """Lazily stream events from the upstream agent."""
        success = False
        try:
            async for event in self.a2a_service.send_message(agent.agent_card, message):
                success = True
                yield event
        except Exception as e:
            logger.error(f"Gateway stream_message failed for agent {agent_id}: {e}")
            raise
        finally:
            await self._record_agent_call(agent_id, success=success)

    async def _check_agent_rate_limit(self, agent: Agent, user_id: str) -> None:
        if agent.rate_limit_per_user_per_hour is None and agent.rate_limit_system_per_hour is None:
            return
        result = await self._rate_limit_service.check_rate_limit(
            agent_id=agent.agent_id,
            user_id=user_id,
            rate_limit_per_user=agent.rate_limit_per_user_per_hour,
            rate_limit_system=agent.rate_limit_system_per_hour,
        )
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": "rate_limit_exceeded", "message": result.reason or "Rate limit exceeded"},
                headers={"Retry-After": str(result.retry_after_seconds or 60)},
            )

    async def _record_agent_call(self, agent_id: str, *, success: bool) -> None:
        try:
            await mongodb.increment_agent_call_count(agent_id, success=success)
        except Exception as e:
            logger.warning(f"Failed to record agent call for {agent_id}: {e}")


gateway_service = GatewayService()
