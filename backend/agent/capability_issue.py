from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any
from uuid import uuid4

from agent.repository.capability_issue_mongo import AgentCapabilityIssueMongoRepository
from common.config import settings
from common.utils.time import utcnow
from models.agent import AgentCapabilityIssue, IssueStatus


class _ExclusionCache:
    """In-memory TTL cache for the set of excluded agent IDs."""

    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._data: frozenset[str] | None = None
        self._timestamp: float = 0.0

    def get(self, monotonic: float) -> frozenset[str] | None:
        if self._data is not None and (monotonic - self._timestamp) < self._ttl:
            return self._data
        self.clear()
        return None

    def set(self, monotonic: float, data: set[str]) -> None:
        self._data = frozenset(data)
        self._timestamp = monotonic

    def clear(self) -> None:
        self._data = None
        self._timestamp = 0.0


_MAX_ERROR_MESSAGE_LEN = 2000
_MAX_QUERY_TEXT_LEN = 1000


class AgentCapabilityIssueService:
    def __init__(
        self,
        *,
        repository,
        threshold: int | None = None,
        id_factory: Callable[[], str] | None = None,
        now=utcnow,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        self._repo = repository
        self._threshold = (
            threshold if threshold is not None else settings.capability_issue_threshold
        )
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._now = now
        self._cache = _ExclusionCache(cache_ttl_seconds)

    async def record_issue(
        self,
        agent_id: str,
        error_message: str,
        query_text: str,
        room_id: str | None = None,
        message_id: str | None = None,
    ) -> AgentCapabilityIssue:
        issue = AgentCapabilityIssue(
            issue_id=self._new_issue_id(),
            agent_id=agent_id,
            error_message=error_message[:_MAX_ERROR_MESSAGE_LEN],
            query_text=query_text[:_MAX_QUERY_TEXT_LEN],
            room_id=room_id,
            message_id=message_id,
            status=IssueStatus.open,
            created_at=self._now(),
        )
        await self._repo.insert(issue.model_dump(mode="json"))
        self._cache.clear()
        return issue

    async def get_excluded_agent_ids(self) -> frozenset[str]:
        timestamp = monotonic()

        cached = self._cache.get(timestamp)
        if cached is not None:
            return cached

        excluded = await self._repo.list_excluded_agent_ids(threshold=self._threshold)
        self._cache.set(timestamp, set(excluded))
        return frozenset(excluded)

    async def get_issue_by_id(self, issue_id: str) -> AgentCapabilityIssue | None:
        doc = await self._repo.get_by_id(issue_id)
        if doc is None:
            return None
        return AgentCapabilityIssue.model_validate(doc)

    async def get_issues_for_agent(
        self,
        agent_id: str,
        status: IssueStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentCapabilityIssue]:
        docs = await self._repo.list_for_agent(
            agent_id,
            status=status.value if status is not None else None,
            limit=limit,
            offset=offset,
        )
        return [AgentCapabilityIssue.model_validate(doc) for doc in docs]

    async def resolve_issue(
        self,
        issue_id: str,
        provider_id: str,
    ) -> AgentCapabilityIssue | None:
        now = self._now()
        doc = await self._repo.resolve(issue_id, provider_id, now)
        if doc is None:
            return None
        self._cache.clear()
        return AgentCapabilityIssue.model_validate(doc)

    async def resolve_all_for_agent(
        self,
        agent_id: str,
        provider_id: str,
    ) -> int:
        modified = await self._repo.resolve_all_for_agent(
            agent_id,
            provider_id,
            self._now(),
        )
        if modified:
            self._cache.clear()
        return modified

    def _new_issue_id(self) -> str:
        return self._id_factory()


class AgentCapabilityIssueServiceNotBound(RuntimeError):
    """Raised when DAL-backed capability-issue storage has not been bound."""


class AgentCapabilityIssueServiceAdapter:
    """Backward-compatible capability-issue service facade."""

    def __init__(self, delegate: AgentCapabilityIssueService | None = None) -> None:
        self._service = delegate

    def bind(self, delegate: AgentCapabilityIssueService) -> None:
        self._service = delegate

    def bind_repository(self, repository: Any) -> None:
        """Backwards-compat binder retained for phased migration."""
        self._service = AgentCapabilityIssueService(repository=repository)

    def bind_mongo(
        self, mongo: Any, collection_name: str = "agent_capability_issues"
    ) -> None:
        """Bind a MongoDAL directly."""
        self.bind_repository(
            AgentCapabilityIssueMongoRepository(
                mongo=mongo,
                collection_name=collection_name,
            )
        )

    def _get_service(self) -> AgentCapabilityIssueService:
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
    def __init__(self, service: AgentCapabilityIssueService) -> None:
        self._service = service

    async def get_excluded_agent_ids(self) -> frozenset[str]:
        return await self._service.get_excluded_agent_ids()
