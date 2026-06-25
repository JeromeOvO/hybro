from __future__ import annotations

from agent.route_adapter import AgentRouteAdapter
from app_shell.agent_service import agent_service


class AppShellAgentCenter(AgentRouteAdapter):
    def __init__(self, service=None) -> None:
        super().__init__(service=service or agent_service)


__all__ = ["AppShellAgentCenter"]
