"""
Rate Limit Service for Gateway API

Manages rate limiting for Gateway API requests using MongoDB storage
with sliding window counters. Tracks requests per API key and globally.

Records are automatically cleaned up via MongoDB TTL index.
"""

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorCollection

from common.utils.logger import get_logger
from config.settings import settings
from database.mongodb import mongodb
from models.api_key import APIKey

logger = get_logger(__name__)


class GatewayRateLimitService:
    """
    Service for managing Gateway API rate limits.

    Implements sliding window rate limiting for:
    - Per-key limits: Each API key can only make X requests per hour
    - Global limits: Total requests from all keys per hour

    Uses MongoDB for persistent storage with TTL index for automatic cleanup.
    """

    @property
    def _collection(self) -> AsyncIOMotorCollection:
        return mongodb.gateway_api_requests_collection

    async def check_rate_limit(self, api_key: APIKey) -> None:
        from fastapi import HTTPException, status

        if (
            settings.gateway_rate_limit_per_key is None
            and settings.gateway_rate_limit_global is None
        ):
            return

        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)

        if settings.gateway_rate_limit_per_key is not None:
            key_count = await self._collection.count_documents(
                {"key_id": api_key.key_id, "timestamp": {"$gt": cutoff}}
            )
            if key_count >= settings.gateway_rate_limit_per_key:
                logger.warning(
                    f"GatewayRateLimitService: Rate limit exceeded for key "
                    f"{api_key.key_id[:8]}...: {key_count}/{settings.gateway_rate_limit_per_key}"
                )
                retry_after = await self._calculate_retry_after(
                    api_key.key_id, cutoff, is_key_limit=True
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": f"Rate limit exceeded: {settings.gateway_rate_limit_per_key} requests per hour",
                    },
                    headers={"Retry-After": str(retry_after)},
                )

        if settings.gateway_rate_limit_global is not None:
            global_count = await self._collection.count_documents(
                {"timestamp": {"$gt": cutoff}}
            )
            if global_count >= settings.gateway_rate_limit_global:
                logger.warning(
                    f"GatewayRateLimitService: Global rate limit exceeded: "
                    f"{global_count}/{settings.gateway_rate_limit_global}"
                )
                retry_after = await self._calculate_retry_after(
                    None, cutoff, is_key_limit=False
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": "Service temporarily unavailable due to high traffic",
                    },
                    headers={"Retry-After": str(retry_after)},
                )

    async def _calculate_retry_after(
        self,
        key_id: str | None,
        cutoff: datetime,
        is_key_limit: bool,
    ) -> int:
        query: dict = {"timestamp": {"$gt": cutoff}}
        if is_key_limit and key_id:
            query["key_id"] = key_id

        oldest = await self._collection.find_one(query, sort=[("timestamp", 1)])
        if oldest and "timestamp" in oldest:
            expires_at = oldest["timestamp"] + timedelta(hours=1)
            retry_after = (expires_at - datetime.now(timezone.utc)).total_seconds()
            return max(1, int(retry_after))
        return 3600

    async def record_request(self, api_key: APIKey) -> None:
        await self._collection.insert_one(
            {"key_id": api_key.key_id, "timestamp": datetime.now(timezone.utc)}
        )
        logger.debug(
            f"GatewayRateLimitService: Recorded request for key {api_key.key_id[:8]}..."
        )

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
