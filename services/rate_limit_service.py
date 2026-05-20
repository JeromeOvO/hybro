"""Compatibility shim for legacy agent rate limiting imports."""

from datetime import datetime, timedelta

from platform_module.rate_limit import (
    AgentRateLimitResult as RateLimitResult,
    PlatformAgentRateLimiter,
)


class RateLimitService:
    def __init__(self) -> None:
        self._collection = None

    def bind(self, *, collection: object) -> None:
        self._collection = collection

    def _require_delegate(self) -> object:
        if self._collection is None:
            raise RuntimeError("RateLimitService.bind() not called - startup incomplete")
        return self._collection

    def _limiter(self, collection: object) -> PlatformAgentRateLimiter:
        return PlatformAgentRateLimiter(
            collection=collection,
            clock=datetime.utcnow,
            window_seconds=3600,
        )

    async def check_rate_limit(
        self,
        agent_id: str,
        user_id: str,
        rate_limit_per_user: int | None,
        rate_limit_system: int | None,
    ) -> RateLimitResult:
        collection = self._require_delegate()
        return await self._limiter(collection).check_agent_limit(
            agent_id,
            user_id,
            rate_limit_per_user,
            rate_limit_system,
        )

    async def record_request(self, agent_id: str, user_id: str) -> None:
        collection = self._require_delegate()
        await self._limiter(collection).record_agent_request(agent_id, user_id)

    async def get_usage_stats(
        self,
        agent_id: str,
        user_id: str | None = None,
    ) -> dict[str, object]:
        collection = self._require_delegate()
        cutoff = datetime.utcnow() - timedelta(hours=1)
        system_count = await collection.count_documents(
            {"agent_id": agent_id, "timestamp": {"$gt": cutoff}}
        )
        stats: dict[str, object] = {
            "agent_id": agent_id,
            "system_requests_this_hour": system_count,
        }
        if user_id:
            user_count = await collection.count_documents(
                {"agent_id": agent_id, "user_id": user_id, "timestamp": {"$gt": cutoff}}
            )
            stats["user_requests_this_hour"] = user_count
        return stats


rate_limit_service = RateLimitService()
