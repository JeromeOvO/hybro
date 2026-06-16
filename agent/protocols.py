from __future__ import annotations

from typing import Protocol, runtime_checkable

from models.agent import Agent, AgentCapabilityIssue, IssueStatus
from models.request import AgentSettingsUpdateRequest
from models.response import AgentCenterResponse


@runtime_checkable
class AgentCenterCompatibility(Protocol):
    async def register_agent_from_route(
        self, *, agent_url: str, provider_id: str
    ) -> AgentCenterResponse: ...
    async def get_agents_by_provider_for_route(
        self, *, provider_id: str
    ) -> AgentCenterResponse: ...
    async def delete_agent_from_route(
        self, *, agent_id: str, provider_id: str
    ) -> AgentCenterResponse: ...
    async def update_agent_settings_from_route(
        self,
        *,
        agent_id: str,
        provider_id: str,
        settings: AgentSettingsUpdateRequest,
    ) -> AgentCenterResponse: ...
    async def get_agent_card_from_url_for_route(
        self, *, agent_url: str
    ) -> AgentCenterResponse: ...
    async def get_visible_agent_for_route(
        self, *, agent_id: str, user_id: str | None
    ) -> AgentCenterResponse: ...
    async def list_visible_agents_for_route(
        self, *, user_id: str | None, active_only: bool = False
    ) -> AgentCenterResponse: ...
    async def list_agents_with_conditions_for_route(
        self, *, user_id: str | None
    ) -> AgentCenterResponse: ...
    def finalize_agent_response_for_route(
        self, response: AgentCenterResponse
    ) -> AgentCenterResponse: ...


@runtime_checkable
class AgentCapabilityIssueStore(Protocol):
    async def get_issues_for_agent(
        self,
        agent_id: str,
        *,
        status: IssueStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentCapabilityIssue]: ...
    async def resolve_all_for_agent(self, agent_id: str, provider_id: str) -> int: ...
    async def get_issue_by_id(self, issue_id: str) -> AgentCapabilityIssue | None: ...
    async def resolve_issue(
        self, issue_id: str, provider_id: str
    ) -> AgentCapabilityIssue | None: ...


@runtime_checkable
class AgentLivenessChecker(Protocol):
    async def __call__(self, agent: Agent) -> Agent: ...


__all__ = [
    "AgentCapabilityIssueStore",
    "AgentCenterCompatibility",
    "AgentLivenessChecker",
]
