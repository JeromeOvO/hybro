from __future__ import annotations

from typing import Any

from agent.liveness import AgentLivenessService
from app_shell.agent_health_service import agent_health_service
from models.agent import Agent

_UNSET = object()
_compat_liveness_service = AgentLivenessService(health_service=agent_health_service)


def bind_agent_liveness_deps(
    *,
    health_service: Any = _UNSET,
    hub_liveness_reader: Any = _UNSET,
    agent_registry_writer: Any = _UNSET,
) -> None:
    deps = {}
    if health_service is not _UNSET:
        deps["health_service"] = health_service
    if hub_liveness_reader is not _UNSET:
        deps["hub_liveness_reader"] = hub_liveness_reader
    if agent_registry_writer is not _UNSET:
        deps["agent_registry_writer"] = agent_registry_writer
    _compat_liveness_service.bind_deps(**deps)


def reset_agent_liveness_deps() -> None:
    _compat_liveness_service.clear_deps()
    _compat_liveness_service.bind_deps(health_service=agent_health_service)


async def check_and_sync_liveness(agent: Agent) -> Agent:
    return await _compat_liveness_service.check_and_sync_liveness(agent)

__all__ = [
    "AgentLivenessService",
    "bind_agent_liveness_deps",
    "check_and_sync_liveness",
    "reset_agent_liveness_deps",
]
