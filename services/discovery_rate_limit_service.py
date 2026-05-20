"""Compatibility shim for legacy discovery API rate limiting imports."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from models.api_key import APIKey
from platform_module.rate_limit import PlatformAPIKeyRateLimiter


class DiscoveryRateLimitService:
    def __init__(self) -> None:
        self._collection = None
        self._per_key_limit: int | None = None
        self._global_limit: int | None = None

    def bind(
        self,
        *,
        collection: object,
        per_key_limit: int,
        global_limit: int,
    ) -> None:
        self._collection = collection
        self._per_key_limit = per_key_limit
        self._global_limit = global_limit

    def _require_delegate(self) -> tuple[object, int, int]:
        if (
            self._collection is None
            or self._per_key_limit is None
            or self._global_limit is None
        ):
            raise RuntimeError(
                "DiscoveryRateLimitService.bind() not called - startup incomplete"
            )
        return self._collection, self._per_key_limit, self._global_limit

    def _limiter(self, collection: object) -> PlatformAPIKeyRateLimiter:
        return PlatformAPIKeyRateLimiter(
            collection=collection,
            clock=lambda: datetime.now(timezone.utc),
            per_key_limit_message=lambda limit: (
                f"Rate limit exceeded: {limit} requests per hour"
            ),
            global_limit_message="Service temporarily unavailable due to high traffic",
        )

    async def check_rate_limit(self, api_key: APIKey) -> None:
        collection, per_key_limit, global_limit = self._require_delegate()
        result = await self._limiter(collection).check_api_key_limit(
            api_key.key_id,
            per_key_limit,
            global_limit,
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
        collection, _per_key_limit, _global_limit = self._require_delegate()
        await self._limiter(collection).record_api_key_request(api_key.key_id)

    async def get_usage_stats(self, key_id: str | None = None) -> dict[str, object]:
        collection, per_key_limit, global_limit = self._require_delegate()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        global_count = await collection.count_documents({"timestamp": {"$gt": cutoff}})
        stats: dict[str, object] = {
            "global_requests_this_hour": global_count,
            "global_limit": global_limit,
        }
        if key_id:
            key_count = await collection.count_documents(
                {"key_id": key_id, "timestamp": {"$gt": cutoff}}
            )
            stats["key_requests_this_hour"] = key_count
            stats["key_limit"] = per_key_limit
        return stats


discovery_rate_limit_service = DiscoveryRateLimitService()
