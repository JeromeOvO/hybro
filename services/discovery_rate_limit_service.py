"""
Rate Limit Service for Discovery API

Manages rate limiting for Discovery API requests using MongoDB storage
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


class DiscoveryRateLimitService:
    """
    Service for managing Discovery API rate limits.
    
    Implements sliding window rate limiting for:
    - Per-key limits: Each API key can only make X requests per hour
    - Global limits: Total requests from all keys per hour
    
    Uses MongoDB for persistent storage with TTL index for automatic cleanup.
    """
    
    @property
    def _collection(self) -> AsyncIOMotorCollection:
        """Get the MongoDB collection for rate limiting."""
        return mongodb.discovery_api_requests_collection
    
    async def check_rate_limit(self, api_key: APIKey) -> None:
        """
        Check if the API key has exceeded rate limits.
        
        Args:
            api_key: The validated API key
            
        Raises:
            HTTPException 429: If rate limit is exceeded
        """
        from fastapi import HTTPException, status
        
        # Skip if no limits configured
        if settings.discovery_rate_limit_per_key is None and settings.discovery_rate_limit_global is None:
            return
        
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        
        # Check per-key limit
        if settings.discovery_rate_limit_per_key is not None:
            key_count = await self._collection.count_documents({
                "key_id": api_key.key_id,
                "timestamp": {"$gt": cutoff},
            })
            
            if key_count >= settings.discovery_rate_limit_per_key:
                logger.warning(
                    f"DiscoveryRateLimitService: Rate limit exceeded for key {api_key.key_id[:8]}...: "
                    f"{key_count}/{settings.discovery_rate_limit_per_key}"
                )
                # Calculate retry_after based on oldest request in window
                retry_after = await self._calculate_retry_after(api_key.key_id, cutoff, is_key_limit=True)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": f"Rate limit exceeded: {settings.discovery_rate_limit_per_key} requests per hour",
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        
        # Check global limit
        if settings.discovery_rate_limit_global is not None:
            global_count = await self._collection.count_documents({
                "timestamp": {"$gt": cutoff},
            })
            
            if global_count >= settings.discovery_rate_limit_global:
                logger.warning(
                    f"DiscoveryRateLimitService: Global rate limit exceeded: "
                    f"{global_count}/{settings.discovery_rate_limit_global}"
                )
                retry_after = await self._calculate_retry_after(None, cutoff, is_key_limit=False)
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
        """
        Calculate accurate retry_after_seconds based on the oldest request in the window.
        
        Args:
            key_id: The API key ID (only used for key limits)
            cutoff: The cutoff time for the sliding window
            is_key_limit: Whether this is a key limit or global limit
            
        Returns:
            Seconds until the oldest request expires from the window
        """
        query = {"timestamp": {"$gt": cutoff}}
        if is_key_limit and key_id:
            query["key_id"] = key_id
        
        # Find the oldest request in the current window
        oldest = await self._collection.find_one(
            query,
            sort=[("timestamp", 1)]  # Ascending = oldest first
        )
        
        if oldest and "timestamp" in oldest:
            oldest_time = oldest["timestamp"]
            # Time until oldest request expires = 1 hour from when it was made
            expires_at = oldest_time + timedelta(hours=1)
            retry_after = (expires_at - datetime.now(timezone.utc)).total_seconds()
            return max(1, int(retry_after))  # At least 1 second
        
        # Fallback to 1 hour if we can't find the oldest request
        return 3600
    
    async def record_request(self, api_key: APIKey) -> None:
        """
        Record a Discovery API request for rate limiting.
        Call this after a request has been successfully made.
        
        Args:
            api_key: The API key used for the request
        """
        await self._collection.insert_one({
            "key_id": api_key.key_id,
            "timestamp": datetime.now(timezone.utc),
        })
        
        logger.debug(
            f"DiscoveryRateLimitService: Recorded request for key {api_key.key_id[:8]}..."
        )
    
    async def get_usage_stats(
        self,
        key_id: str | None = None,
    ) -> dict:
        """
        Get current rate limit usage statistics.
        
        Args:
            key_id: Optional key ID to get key-specific stats
            
        Returns:
            Dictionary with usage statistics
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        
        global_count = await self._collection.count_documents({
            "timestamp": {"$gt": cutoff},
        })
        
        stats = {
            "global_requests_this_hour": global_count,
            "global_limit": settings.discovery_rate_limit_global,
        }
        
        if key_id:
            key_count = await self._collection.count_documents({
                "key_id": key_id,
                "timestamp": {"$gt": cutoff},
            })
            stats["key_requests_this_hour"] = key_count
            stats["key_limit"] = settings.discovery_rate_limit_per_key
        
        return stats


# Singleton instance
discovery_rate_limit_service = DiscoveryRateLimitService()

