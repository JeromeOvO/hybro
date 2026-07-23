"""On-demand agent liveness check.

Called from the GET /agent/getAgent/{id} endpoint to probe agent
reachability *before* returning the response, ensuring the frontend
always sees an accurate ``agent_status``.
"""

from __future__ import annotations

from typing import Any

from common.config.settings import settings
from common.protocols.hub_protocols import validate_hub_liveness_reader
from common.utils.logger import get_logger
from models.agent import Agent, AgentStatus

logger = get_logger(__name__)

_UNSET = object()


class AgentLivenessService:
    def __init__(
        self,
        *,
        health_service: Any = _UNSET,
        hub_liveness_reader: Any = _UNSET,
        agent_registry_writer: Any = _UNSET,
    ) -> None:
        self._health_service = _UNSET
        self._hub_liveness_reader = _UNSET
        self._agent_registry_writer = _UNSET
        self.bind_deps(
            health_service=health_service,
            hub_liveness_reader=hub_liveness_reader,
            agent_registry_writer=agent_registry_writer,
        )

    def bind_deps(
        self,
        *,
        health_service: Any = _UNSET,
        hub_liveness_reader: Any = _UNSET,
        agent_registry_writer: Any = _UNSET,
    ) -> None:
        if hub_liveness_reader is not _UNSET:
            validate_hub_liveness_reader(hub_liveness_reader)
            self._hub_liveness_reader = hub_liveness_reader
        if agent_registry_writer is not _UNSET:
            self._agent_registry_writer = agent_registry_writer
        if health_service is not _UNSET:
            self._health_service = health_service

    def clear_deps(self) -> None:
        self._health_service = _UNSET
        self._hub_liveness_reader = _UNSET
        self._agent_registry_writer = _UNSET

    async def __call__(self, agent: Agent) -> Agent:
        return await self.check_and_sync_liveness(agent)

    async def check_and_sync_liveness(self, agent: Agent) -> Agent:
        """Probe the agent and sync ``agent_status`` in the DB if it changed.

        - **Cloud agents**: HTTP probe via ``AgentHealthService``.
        - **Hub agents**: Authoritative ``is_hub_alive`` check via ``RelayService``.
        - **Others**: returned unchanged.
        """

        if agent.hub_id:
            return await self._check_hub_agent(agent)

        if agent.source == "cloud":
            return await self._check_cloud_agent(agent)

        return agent

    async def _check_cloud_agent(self, agent: Agent) -> Agent:
        health_service = self._health_service
        if health_service is _UNSET or health_service is None:
            return agent

        try:
            is_healthy, fetched_card = await health_service.check_agent_health(
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
            await health_service._update_agent_card_in_db(agent, fetched_card)

        if not is_healthy and agent.agent_status == AgentStatus.active:
            await health_service.update_agent_status(
                agent.agent_id, AgentStatus.inactive
            )
            agent.agent_status = AgentStatus.inactive
            logger.info("Liveness: cloud agent %s marked inactive", agent.agent_id)

        elif is_healthy and agent.agent_status == AgentStatus.inactive:
            await health_service.update_agent_status(agent.agent_id, AgentStatus.active)
            agent.agent_status = AgentStatus.active
            logger.info(
                "Liveness: cloud agent %s recovered — marked active", agent.agent_id
            )

        return agent

    async def _check_hub_agent(self, agent: Agent) -> Agent:
        if (
            self._hub_liveness_reader is _UNSET
            or self._hub_liveness_reader is None
            or self._agent_registry_writer is _UNSET
            or self._agent_registry_writer is None
        ):
            return agent

        if not await self._is_hub_online(agent.hub_id):
            await self._agent_registry_writer.mark_hub_agents_offline(agent.hub_id)
            agent.agent_status = AgentStatus.inactive
            logger.info(
                "Liveness: hub %s disconnected — agent %s marked inactive",
                agent.hub_id,
                agent.agent_id,
            )

        return agent

    async def _is_hub_online(self, hub_id: str) -> bool:
        return bool(await self._hub_liveness_reader.is_hub_online(hub_id))


_default_liveness_service = AgentLivenessService()


def bind_agent_liveness_deps(
    *,
    hub_liveness_reader: Any = _UNSET,
    agent_registry_writer: Any = _UNSET,
    health_service: Any = _UNSET,
) -> None:
    _default_liveness_service.bind_deps(
        health_service=health_service,
        hub_liveness_reader=hub_liveness_reader,
        agent_registry_writer=agent_registry_writer,
    )


def reset_agent_liveness_deps() -> None:
    _default_liveness_service.clear_deps()


async def check_and_sync_liveness(agent: Agent) -> Agent:
    return await _default_liveness_service.check_and_sync_liveness(agent)


__all__ = [
    "AgentLivenessService",
    "bind_agent_liveness_deps",
    "check_and_sync_liveness",
    "reset_agent_liveness_deps",
]
