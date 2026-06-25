from __future__ import annotations

from agent.inspection import AgentInspectionService
from app_shell.agent_service import agent_service


class AppShellInspectionCenter(AgentInspectionService):
    def __init__(self, *, agent_service_dep=None) -> None:
        service = agent_service_dep or agent_service
        self.agent_service = service

        async def validate_agent_card(card_data):
            return await self.agent_service.validate_agent_card(card_data)

        super().__init__(validator=validate_agent_card)

__all__ = ["AppShellInspectionCenter"]
