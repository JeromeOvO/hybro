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

_hub_liveness_reader = None
_agent_registry_writer = None


def bind_agent_liveness_deps(*, hub_liveness_reader=None, agent_registry_writer=None) -> None:
    global _hub_liveness_reader, _agent_registry_writer
    _hub_liveness_reader = hub_liveness_reader
    _agent_registry_writer = agent_registry_writer


def reset_agent_liveness_deps() -> None:
    bind_agent_liveness_deps(hub_liveness_reader=None, agent_registry_writer=None)


async def check_and_sync_liveness(agent: Agent) -> Agent:
    """Probe the agent and sync ``agent_status`` in the DB if it changed.

    - **Cloud agents**: HTTP probe via ``AgentHealthService``.
    - **Hub agents**: Authoritative ``is_hub_alive`` check via ``RelayService``.
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
        is_healthy, fetched_card = await agent_health_service.check_agent_health(
            agent, timeout=settings.cloud_health_check_timeout
        )
    except Exception:
        logger.warning(
            "Liveness probe failed for cloud agent %s — returning stale status",
            agent.agent_id,
            exc_info=True,
        )
        return agent

    if is_healthy and fetched_card:
        await agent_health_service._update_agent_card_in_db(agent, fetched_card)

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
    if _hub_liveness_reader is None or _agent_registry_writer is None:
        return agent

    if not await _is_hub_online(agent.hub_id):
        await _agent_registry_writer.mark_hub_agents_offline(agent.hub_id)
        agent.agent_status = AgentStatus.inactive
        logger.info(
            "Liveness: hub %s disconnected — agent %s marked inactive",
            agent.hub_id,
            agent.agent_id,
        )

    return agent


async def _is_hub_online(hub_id: str) -> bool:
    return bool(await _hub_liveness_reader.is_hub_online(hub_id))
