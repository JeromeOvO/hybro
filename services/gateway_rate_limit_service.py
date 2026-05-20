"""
Compatibility shim for Gateway API rate limiting.

Counter/window behavior is owned by ``platform_module.rate_limit``. This file
keeps the legacy singleton import path until API routes are protocol-bound.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorCollection

from config.settings import settings
from database.mongodb import mongodb
from models.api_key import APIKey
from platform_module.rate_limit import PlatformAPIKeyRateLimiter


class GatewayRateLimitService:
    @property
    def _collection(self) -> AsyncIOMotorCollection:
        return mongodb.gateway_api_requests_collection

    @property
    def _limiter(self) -> PlatformAPIKeyRateLimiter:
        return PlatformAPIKeyRateLimiter(
            collection=self._collection,
            clock=lambda: datetime.now(timezone.utc),
            per_key_limit_message=lambda limit: (
                f"Rate limit exceeded: {limit} requests per hour"
            ),
            global_limit_message="Service temporarily unavailable due to high traffic",
        )

    async def check_rate_limit(self, api_key: APIKey) -> None:
        result = await self._limiter.check_api_key_limit(
            api_key.key_id,
            settings.gateway_rate_limit_per_key,
            settings.gateway_rate_limit_global,
        )
        if result.allowed:
            return
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "message": result.message,
            },
            headers={"Retry-After": str(result.retry_after_seconds or 3600)},
        )

    async def record_request(self, api_key: APIKey) -> None:
        await self._limiter.record_api_key_request(api_key.key_id)

    async def get_usage_stats(self, key_id: str | None = None) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        global_count = await self._collection.count_documents(
            {"timestamp": {"$gt": cutoff}}
        )
        stats: dict = {
            "global_requests_this_hour": global_count,
            "global_limit": settings.gateway_rate_limit_global,
        }
        if key_id:
            key_count = await self._collection.count_documents(
                {"key_id": key_id, "timestamp": {"$gt": cutoff}}
            )
            stats["key_requests_this_hour"] = key_count
            stats["key_limit"] = settings.gateway_rate_limit_per_key
        return stats


gateway_rate_limit_service = GatewayRateLimitService()
