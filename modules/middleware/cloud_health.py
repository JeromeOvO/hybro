"""CloudHealthMiddleware — on-demand reachability check for cloud agents.

Before dispatching to a cloud (direct) agent, performs a lightweight HTTP
probe to verify the agent endpoint is reachable.  Results are cached with
a short TTL to avoid hammering the agent on every message.

If the probe fails, the agent is immediately marked ``inactive`` in the
database and the dispatch is denied with an explanatory message.  If the
probe succeeds and the agent was previously ``inactive``, it is restored
to ``active`` (auto-recovery).

Hub agents (``agent.hub_id`` is set) are skipped — their liveness is
handled by ``HubTransportMiddleware``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from common.utils.logger import get_logger
from config.settings import settings
from models.agent import AgentStatus
from modules.dispatch_middleware import DispatchContext

if TYPE_CHECKING:
    from models.processing import ProcessingResult
    from services.agent_health_service import AgentHealthService

logger = get_logger(__name__)


class CloudHealthMiddleware:
    def __init__(self, health_service: AgentHealthService) -> None:
        self._health = health_service
        self._cache: dict[str, tuple[bool, float]] = {}

    def _get_cached(self, agent_id: str) -> bool | None:
        entry = self._cache.get(agent_id)
        if entry is None:
            return None
        is_healthy, ts = entry
        if time.monotonic() - ts > settings.cloud_health_cache_ttl:
            del self._cache[agent_id]
            return None
        return is_healthy

    async def pre_dispatch(self, ctx: DispatchContext) -> DispatchContext:
        if ctx.agent.hub_id:
            return ctx

        if ctx.agent.source != "cloud":
            return ctx

        agent_id = ctx.agent.agent_id
        cached = self._get_cached(agent_id)

        if cached is None:
            is_healthy, fetched_card = await self._health.check_agent_health(
                ctx.agent, timeout=settings.cloud_health_check_timeout
            )
            self._cache[agent_id] = (is_healthy, time.monotonic())
            if is_healthy and fetched_card:
                await self._health._update_agent_card_in_db(ctx.agent, fetched_card)
        else:
            is_healthy = cached

        if not is_healthy:
            if ctx.agent.agent_status == AgentStatus.active:
                await self._health.update_agent_status(agent_id, AgentStatus.inactive)
                logger.warning(
                    "CloudHealthMiddleware: agent %s unreachable — marked inactive",
                    agent_id,
                )
            ctx.denied = True
            ctx.deny_reason = "Agent is offline — endpoint is unreachable"
        else:
            if ctx.agent.agent_status == AgentStatus.inactive:
                await self._health.update_agent_status(agent_id, AgentStatus.active)
                logger.info(
                    "CloudHealthMiddleware: agent %s recovered — marked active",
                    agent_id,
                )

        return ctx

    async def post_dispatch(
        self, ctx: DispatchContext, result: ProcessingResult
    ) -> ProcessingResult:
        return result
