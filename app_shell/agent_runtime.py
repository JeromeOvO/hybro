from __future__ import annotations

from app_shell.agent_service import agent_service
from models.request import AgentCenterRequest
from models.response import AgentCenterResponse


class AppShellAgentCenter:
    def __init__(self, service=None):
        self.agent_service = service or agent_service

    async def get_agent_card_from_url(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        return await self.agent_service.get_agent_card_from_url(request)

    async def register_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        return await self.agent_service.register_agent(request)

    async def update_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        return await self.agent_service.update_agent(request)

    async def remove_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        return await self.agent_service.remove_agent(request)

    async def query_agent_by_agent_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        return await self.agent_service.query_agent_by_agent_id(request)

    async def get_all_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        return await self.agent_service.get_all_agents(request)

    async def get_all_active_agents(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        return await self.agent_service.get_all_active_agents(request)

    async def get_agents_with_conditions(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        return await self.agent_service.get_agents_with_conditions(request)

    async def query_similar_agents(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        return await self.agent_service.query_similar_agents(request)

    async def get_agents_by_provider_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        return await self.agent_service.get_agents_by_provider_id(request)

    def _mask_sensitive_information(
        self, response: AgentCenterResponse, fields: list[str]
    ) -> AgentCenterResponse:
        return self.agent_service._mask_sensitive_information(response, fields)


__all__ = ["AppShellAgentCenter"]
