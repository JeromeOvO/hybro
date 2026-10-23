from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from common.dto import AgentRouteResponse
from common.protocols import AgentNetworkTargetResolver
from common.protocols.json_types import JsonValue
from models.agent import AgentCapabilityIssue, IssueStatus
from models.agent_group import AgentGroup
from models.agent_network import (
    AgentDiscoveryResponse,
    AgentGroupListResponse,
    AgentProfile,
    AgentSearchResponse,
)
from models.request import InspectionCenterRequest
from models.response import InspectionCenterResponse


@runtime_checkable
class AgentNetworkAccess(AgentNetworkTargetResolver, Protocol):
    async def discover(self, group_id: str | None = None) -> AgentDiscoveryResponse: ...
    async def list_groups(self) -> AgentGroupListResponse: ...
    async def search(
        self, query: str, group_id: str | None = None, limit: int = 5
    ) -> AgentSearchResponse: ...
    async def get_profile(self, agent_id: str) -> AgentProfile: ...


@runtime_checkable
class AgentCatalog(Protocol):
    async def register(
        self, *, agent_url: str, provider_id: str
    ) -> AgentRouteResponse: ...
    async def list_for_provider(self, *, provider_id: str) -> AgentRouteResponse: ...
    async def delete(
        self, *, agent_id: str, provider_id: str
    ) -> AgentRouteResponse: ...
    async def resolve_card(self, *, agent_url: str) -> AgentRouteResponse: ...
    async def get_visible(
        self, *, agent_id: str, user_id: str | None
    ) -> AgentRouteResponse: ...
    async def list_visible(
        self, *, user_id: str | None, active_only: bool = False
    ) -> AgentRouteResponse: ...


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
class AgentGroupStore(Protocol):
    async def add_agent_group(self, group: AgentGroup) -> bool: ...
    async def delete_agent_group(self, group_id: str) -> bool: ...
    async def get_agent_group_by_id(self, group_id: str) -> AgentGroup | None: ...
    async def get_agent_groups_by_owner(self, owner_id: str) -> list[AgentGroup]: ...
    async def update_agent_group(
        self, group_id: str, updates: dict[str, str | list[str]]
    ) -> bool: ...


@runtime_checkable
class AgentSuggestionService(Protocol):
    async def suggest_agents(
        self,
        message_text: str,
        top_k: int = 3,
        user_id: str | None = None,
    ) -> AgentSuggestionResult: ...


@dataclass(frozen=True)
class AgentSuggestion:
    agent_id: str
    name: str
    reason: str
    score: float | None = None


@dataclass(frozen=True)
class AgentSuggestionResult:
    suggested_agents: list[AgentSuggestion] = field(default_factory=list)
    analysis: str | None = None
    confidence: float | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)


def serialize_agent_suggestion_result(
    result: AgentSuggestionResult,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "suggested_agents": [
            {
                key: value
                for key, value in {
                    "agent_id": agent.agent_id,
                    "name": agent.name,
                    "reason": agent.reason,
                    "score": agent.score,
                }.items()
                if value is not None
            }
            for agent in result.suggested_agents
        ]
    }
    if result.analysis is not None:
        payload["analysis"] = result.analysis
    if result.confidence is not None:
        payload["confidence"] = result.confidence
    payload.update(result.metadata)
    return payload


@runtime_checkable
class AgentInspection(Protocol):
    async def inspect_a2a_connection(
        self, request: InspectionCenterRequest
    ) -> InspectionCenterResponse: ...
    async def inspect_agent_card(
        self, request: InspectionCenterRequest
    ) -> InspectionCenterResponse: ...


__all__ = [
    "AgentCapabilityIssueStore",
    "AgentCatalog",
    "AgentGroupStore",
    "AgentInspection",
    "AgentNetworkAccess",
    "AgentSuggestion",
    "AgentSuggestionResult",
    "AgentSuggestionService",
    "serialize_agent_suggestion_result",
]
