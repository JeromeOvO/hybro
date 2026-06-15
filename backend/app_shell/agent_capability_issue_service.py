"""Compatibility facade for capability-issue services.

The production implementation moved to ``agent.capability_issue`` and a
module-owned repository in ``agent.repository.capability_issue_mongo``.
This shim keeps the historical ``app_shell.agent_capability_issue_service``
shape so current startup and route wiring can continue incrementally.
"""

from __future__ import annotations

from typing import Any

from agent.capability_issue import AgentCapabilityIssueService as _DomainIssueService
from agent.repository.capability_issue_mongo import AgentCapabilityIssueMongoRepository
from common.utils.logger import get_logger
from models.agent import AgentCapabilityIssue, IssueStatus

logger = get_logger(__name__)


class AgentCapabilityIssueServiceNotBound(RuntimeError):
    """Raised when DAL-backed capability-issue storage has not been bound."""


class AgentCapabilityIssueServiceAdapter:
    """Backward-compatible capability-issue service facade."""

    def __init__(self, delegate: _DomainIssueService | None = None) -> None:
        self._service = delegate

    def bind(self, delegate: _DomainIssueService) -> None:
        self._service = delegate

    def bind_repository(self, repository: Any) -> None:
        """Backwards-compat binder retained for phased migration."""
        self._service = _DomainIssueService(repository=repository)

    def bind_mongo(self, mongo: Any, collection_name: str = "agent_capability_issues") -> None:
        """Bind a MongoDAL directly."""
        self.bind_repository(
            AgentCapabilityIssueMongoRepository(
                mongo=mongo,
                collection_name=collection_name,
            )
        )

    def _get_service(self) -> _DomainIssueService:
        if self._service is None:
            raise AgentCapabilityIssueServiceNotBound(
                "Capability-issue service is not bound. Call bind_repository/bind_mongo "
                "during startup before resolution paths use it."
            )
        return self._service

    async def record_issue(
        self,
        agent_id: str,
        error_message: str,
        query_text: str,
        room_id: str | None = None,
        message_id: str | None = None,
    ) -> AgentCapabilityIssue:
        return await self._get_service().record_issue(
            agent_id=agent_id,
            error_message=error_message,
            query_text=query_text,
            room_id=room_id,
            message_id=message_id,
        )

    async def get_excluded_agent_ids(self) -> frozenset[str]:
        return await self._get_service().get_excluded_agent_ids()

    async def get_issue_by_id(self, issue_id: str) -> AgentCapabilityIssue | None:
        return await self._get_service().get_issue_by_id(issue_id)

    async def get_issues_for_agent(
        self,
        agent_id: str,
        status: IssueStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentCapabilityIssue]:
        return await self._get_service().get_issues_for_agent(
            agent_id=agent_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def resolve_issue(
        self,
        issue_id: str,
        provider_id: str,
    ) -> AgentCapabilityIssue | None:
        return await self._get_service().resolve_issue(issue_id, provider_id)

    async def resolve_all_for_agent(
        self,
        agent_id: str,
        provider_id: str,
    ) -> int:
        return await self._get_service().resolve_all_for_agent(
            agent_id=agent_id,
            provider_id=provider_id,
        )


class CapabilityIssueExclusionReader:
    def __init__(
        self,
        service: AgentCapabilityIssueServiceAdapter | None = None,
    ) -> None:
        self._service = service or capability_issue_service

    async def get_excluded_agent_ids(self) -> frozenset[str]:
        return await self._service.get_excluded_agent_ids()


capability_issue_service = AgentCapabilityIssueServiceAdapter()
