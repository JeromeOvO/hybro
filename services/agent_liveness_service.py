"""On-demand agent liveness check.

Called from the GET /agent/getAgent/{id} endpoint to probe agent
reachability *before* returning the response, ensuring the frontend
always sees an accurate ``agent_status``.
"""

from __future__ import annotations

from common.utils.logger import get_logger
from config.settings import settings
from models.agent import Agent, AgentStatus

logger = get_logger(__name__)


async def check_and_sync_liveness(agent: Agent) -> Agent:
    """Probe the agent and sync ``agent_status`` in the DB if it changed.

    - **Cloud agents**: HTTP probe via ``AgentHealthService``.
    - **Hub agents**: In-memory ``is_hub_connected`` check via ``RelayService``.
    - **Others**: returned unchanged.
    """

    if agent.hub_id:
        return await _check_hub_agent(agent)

    if agent.source == "cloud":
        return await _check_cloud_agent(agent)

    return agent


async def _check_cloud_agent(agent: Agent) -> Agent:
    from services.agent_health_service import agent_health_service

    try:
        is_healthy = await agent_health_service.check_agent_health(
            agent, timeout=settings.cloud_health_check_timeout
        )
    except Exception:
        logger.warning(
            "Liveness probe failed for cloud agent %s — returning stale status",
            agent.agent_id,
            exc_info=True,
        )
        return agent

    if not is_healthy and agent.agent_status == AgentStatus.active:
        await agent_health_service.update_agent_status(
            agent.agent_id, AgentStatus.inactive
        )
        agent.agent_status = AgentStatus.inactive
        logger.info("Liveness: cloud agent %s marked inactive", agent.agent_id)

    elif is_healthy and agent.agent_status == AgentStatus.inactive:
        await agent_health_service.update_agent_status(
            agent.agent_id, AgentStatus.active
        )
        agent.agent_status = AgentStatus.active
        logger.info("Liveness: cloud agent %s recovered — marked active", agent.agent_id)

    return agent


async def _check_hub_agent(agent: Agent) -> Agent:
    try:
        from services.relay_service import relay_service as _svc
    except ImportError:
        return agent

    if _svc is None:
        return agent

    if not _svc.is_hub_connected(agent.hub_id):
        await _svc.mark_hub_agents_offline(agent.hub_id)
        agent.agent_status = AgentStatus.inactive
        logger.info(
            "Liveness: hub %s disconnected — agent %s marked inactive",
            agent.hub_id,
            agent.agent_id,
        )

    return agent
