"""
Compatibility shim for legacy agent rate limiting.

The shared sliding-window logic now lives in ``platform_module.rate_limit``.
This module preserves the old service import path while earlier execution code
is migrated to Platform protocols.
"""

from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorCollection

from database.mongodb import mongodb
from platform_module.rate_limit import (
    AgentRateLimitResult as RateLimitResult,
    PlatformAgentRateLimiter,
)


class RateLimitService:
    @property
    def _collection(self) -> AsyncIOMotorCollection:
        return mongodb.agent_requests_collection

    @property
    def _limiter(self) -> PlatformAgentRateLimiter:
        return PlatformAgentRateLimiter(
            collection=self._collection,
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
        return await self._limiter.check_agent_limit(
            agent_id,
            user_id,
            rate_limit_per_user,
            rate_limit_system,
        )

    async def record_request(self, agent_id: str, user_id: str) -> None:
        await self._limiter.record_agent_request(agent_id, user_id)

    async def get_usage_stats(
        self,
        agent_id: str,
        user_id: str | None = None,
    ) -> dict:
        cutoff = datetime.utcnow() - timedelta(hours=1)
        system_count = await self._collection.count_documents(
            {"agent_id": agent_id, "timestamp": {"$gt": cutoff}}
        )
        stats = {
            "agent_id": agent_id,
            "system_requests_this_hour": system_count,
        }
        if user_id:
            user_count = await self._collection.count_documents(
                {"agent_id": agent_id, "user_id": user_id, "timestamp": {"$gt": cutoff}}
            )
            stats["user_requests_this_hour"] = user_count
        return stats


rate_limit_service = RateLimitService()
