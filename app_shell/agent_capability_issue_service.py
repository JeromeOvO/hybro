"""
Agent Capability Issue Service

Tracks capability errors (agent returns errors for tasks it claims to support)
and provides an exclusion set for Pinecone searches so agents with repeated
failures are skipped in favour of working alternatives.

Key design choices:
- Agents are excluded after `capability_issue_threshold` (default 2) open issues.
  A single error is tolerated as a possible transient failure.
- Exclusion set is cached in-memory with ~60s TTL to avoid per-search DB hits.
- Issues are NOT auto-resolved — the agent owner must resolve via API.
"""

import uuid
from time import monotonic

from pymongo import ReturnDocument

from common.config.settings import settings
from common.utils.logger import get_logger
from common.utils.time import utcnow
from database.mongodb import mongodb
from models.agent import AgentCapabilityIssue, IssueStatus

logger = get_logger(__name__)

# Cache TTL in seconds
_EXCLUSION_CACHE_TTL: float = 60.0

# Field length limits to prevent unbounded storage
_MAX_ERROR_MESSAGE_LEN: int = 2000
_MAX_QUERY_TEXT_LEN: int = 1000


class _ExclusionCache:
    """In-memory TTL cache for the set of excluded agent IDs."""

    def __init__(self, ttl: float = _EXCLUSION_CACHE_TTL):
        self._ttl = ttl
        self._data: frozenset[str] | None = None
        self._timestamp: float = 0.0

    def get(self) -> frozenset[str] | None:
        if self._data is not None and (monotonic() - self._timestamp) < self._ttl:
            return self._data
        return None

    def set(self, data: set[str]) -> None:
        self._data = frozenset(data)
        self._timestamp = monotonic()

    def clear(self) -> None:
        self._data = None
        self._timestamp = 0.0


class AgentCapabilityIssueService:
    def __init__(self):
        self._cache = _ExclusionCache()

    @property
    def _collection(self):
        return mongodb.agent_capability_issues_collection

    async def record_issue(
        self,
        agent_id: str,
        error_message: str,
        query_text: str,
        room_id: str | None = None,
        message_id: str | None = None,
    ) -> AgentCapabilityIssue:
        """Record a new capability issue for an agent."""
        issue = AgentCapabilityIssue(
            issue_id=str(uuid.uuid4()),
            agent_id=agent_id,
            error_message=error_message[:_MAX_ERROR_MESSAGE_LEN],
            query_text=query_text[:_MAX_QUERY_TEXT_LEN],
            room_id=room_id,
            message_id=message_id,
            status=IssueStatus.open,
            created_at=utcnow(),
        )
        await self._collection.insert_one(issue.model_dump(mode="json"))
        self._cache.clear()
        logger.info(
            "Recorded capability issue %s for agent %s",
            issue.issue_id,
            agent_id,
        )
        return issue

    async def get_excluded_agent_ids(self) -> frozenset[str]:
        """Return agent IDs with open issue count >= threshold.

        Uses an in-memory cache (~60s TTL) to avoid hitting MongoDB on every
        search request.
        """
        cached = self._cache.get()
        if cached is not None:
            return cached

        threshold = settings.capability_issue_threshold
        pipeline = [
            {"$match": {"status": IssueStatus.open.value}},
            {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gte": threshold}}},
        ]
        cursor = self._collection.aggregate(pipeline)
        excluded = set()
        async for doc in cursor:
            excluded.add(doc["_id"])

        self._cache.set(excluded)
        if excluded:
            logger.info(
                "Capability issue exclusion set: %d agent(s) excluded", len(excluded)
            )
        return self._cache.get()

    async def get_issue_by_id(
        self, issue_id: str
    ) -> AgentCapabilityIssue | None:
        """Return a single issue by its issue_id, or None."""
        doc = await self._collection.find_one({"issue_id": issue_id})
        if not doc:
            return None
        doc.pop("_id", None)
        return AgentCapabilityIssue.model_validate(doc)

    async def get_issues_for_agent(
        self,
        agent_id: str,
        status: IssueStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AgentCapabilityIssue]:
        """Return issues for a specific agent, optionally filtered by status."""
        query: dict = {"agent_id": agent_id}
        if status is not None:
            query["status"] = status.value
        cursor = (
            self._collection.find(query)
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
        )
        issues = []
        async for doc in cursor:
            doc.pop("_id", None)
            issues.append(AgentCapabilityIssue.model_validate(doc))
        return issues

    async def resolve_issue(
        self,
        issue_id: str,
        provider_id: str,
    ) -> AgentCapabilityIssue | None:
        """Mark a single issue as resolved. Returns the updated issue or None."""
        now = utcnow()
        result = await self._collection.find_one_and_update(
            {"issue_id": issue_id, "status": IssueStatus.open.value},
            {
                "$set": {
                    "status": IssueStatus.resolved.value,
                    "resolved_at": now,
                    "resolved_by": provider_id,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if result:
            self._cache.clear()
            result.pop("_id", None)
            return AgentCapabilityIssue.model_validate(result)
        return None

    async def resolve_all_for_agent(
        self,
        agent_id: str,
        provider_id: str,
    ) -> int:
        """Bulk resolve all open issues for an agent. Returns count resolved."""
        now = utcnow()
        result = await self._collection.update_many(
            {"agent_id": agent_id, "status": IssueStatus.open.value},
            {
                "$set": {
                    "status": IssueStatus.resolved.value,
                    "resolved_at": now,
                    "resolved_by": provider_id,
                }
            },
        )
        if result.modified_count > 0:
            self._cache.clear()
            logger.info(
                "Resolved %d issues for agent %s",
                result.modified_count,
                agent_id,
            )
        return result.modified_count


class CapabilityIssueExclusionReader:
    def __init__(self, service: AgentCapabilityIssueService | None = None) -> None:
        self._service = service or capability_issue_service

    async def get_excluded_agent_ids(self) -> frozenset[str]:
        return await self._service.get_excluded_agent_ids()


# Singleton
capability_issue_service = AgentCapabilityIssueService()
