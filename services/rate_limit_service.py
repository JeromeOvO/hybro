"""
Rate Limit Service for Agent Usage Control

This service manages rate limiting for agent requests using MongoDB storage
with sliding window counters. It supports both per-user and system-wide limits.

Records are automatically cleaned up via MongoDB TTL index.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorCollection

from common.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""
    allowed: bool
    reason: str | None = None
    user_requests_used: int = 0
    user_requests_limit: int | None = None
    system_requests_used: int = 0
    system_requests_limit: int | None = None
    retry_after_seconds: int | None = None


class RateLimitService:
    """
    Service for managing agent request rate limits.
    
    Implements sliding window rate limiting for both:
    - Per-user limits: Each user can only make X requests per hour to a specific agent
    - System-wide limits: Total requests to an agent from all users per hour
    
    Uses MongoDB for persistent storage with TTL index for automatic cleanup.
    """
    
    def __init__(self):
        self._collection: AsyncIOMotorCollection | None = None
        self._initialized = False
    
    async def initialize(self, collection: AsyncIOMotorCollection) -> None:
        """
        Initialize the service with a MongoDB collection.
        
        Args:
            collection: The MongoDB collection to use for storing request records
        """
        self._collection = collection
        
        # Create indexes for efficient queries
        # TTL index: automatically delete records older than 2 hours
        await collection.create_index(
            "timestamp",
            expireAfterSeconds=7200  # 2 hours
        )
        # Compound index for efficient lookups
        await collection.create_index([
            ("agent_id", 1),
            ("user_id", 1),
            ("timestamp", -1)
        ])
        # Index for system-wide queries
        await collection.create_index([
            ("agent_id", 1),
            ("timestamp", -1)
        ])
        
        self._initialized = True
        logger.info("RateLimitService: Initialized with MongoDB collection")
    
    def _ensure_initialized(self) -> None:
        """Ensure the service has been initialized."""
        if not self._initialized or self._collection is None:
            raise RuntimeError(
                "RateLimitService not initialized. Call initialize() first."
            )
    
    async def check_rate_limit(
        self,
        agent_id: str,
        user_id: str,
        rate_limit_per_user: int | None,
        rate_limit_system: int | None,
    ) -> RateLimitResult:
        """
        Check if a request is allowed under the rate limits.
        
        Note: This is a soft check - there's a potential race condition between
        check and record. For high-concurrency scenarios, use check_and_reserve()
        for atomic check-and-increment behavior.
        
        Args:
            agent_id: The ID of the agent being requested
            user_id: The ID of the user making the request (required)
            rate_limit_per_user: Max requests per user per hour (None = unlimited)
            rate_limit_system: Max total requests per hour (None = unlimited)
        
        Returns:
            RateLimitResult with allowed status and usage details
        
        Raises:
            ValueError: If user_id is not provided
        """
        self._ensure_initialized()
        
        if not user_id:
            raise ValueError("user_id is required to check rate limit")
        
        # Skip check entirely if no limits configured
        if rate_limit_per_user is None and rate_limit_system is None:
            return RateLimitResult(allowed=True)
        
        cutoff = datetime.utcnow() - timedelta(hours=1)
        
        # Get current counts
        user_count = 0
        system_count = 0
        
        if rate_limit_per_user is not None:
            user_count = await self._collection.count_documents({
                "agent_id": agent_id,
                "user_id": user_id,
                "timestamp": {"$gt": cutoff}
            })
        
        if rate_limit_system is not None:
            system_count = await self._collection.count_documents({
                "agent_id": agent_id,
                "timestamp": {"$gt": cutoff}
            })
        
        # Check user limit
        if rate_limit_per_user is not None and user_count >= rate_limit_per_user:
            logger.warning(
                f"RateLimitService: User rate limit exceeded for agent {agent_id}, "
                f"user {user_id}: {user_count}/{rate_limit_per_user}"
            )
            # Calculate accurate retry_after based on oldest request in window
            retry_after = await self._calculate_retry_after(
                agent_id, user_id, cutoff, is_user_limit=True
            )
            return RateLimitResult(
                allowed=False,
                reason=f"Rate limit exceeded: You can only make {rate_limit_per_user} requests per hour to this agent",
                user_requests_used=user_count,
                user_requests_limit=rate_limit_per_user,
                system_requests_used=system_count,
                system_requests_limit=rate_limit_system,
                retry_after_seconds=retry_after,
            )
        
        # Check system limit
        if rate_limit_system is not None and system_count >= rate_limit_system:
            logger.warning(
                f"RateLimitService: System rate limit exceeded for agent {agent_id}: "
                f"{system_count}/{rate_limit_system}"
            )
            # Calculate accurate retry_after based on oldest request in window
            retry_after = await self._calculate_retry_after(
                agent_id, None, cutoff, is_user_limit=False
            )
            return RateLimitResult(
                allowed=False,
                reason=f"Agent is currently busy. System limit of {rate_limit_system} requests per hour has been reached",
                user_requests_used=user_count,
                user_requests_limit=rate_limit_per_user,
                system_requests_used=system_count,
                system_requests_limit=rate_limit_system,
                retry_after_seconds=retry_after,
            )
        
        return RateLimitResult(
            allowed=True,
            user_requests_used=user_count,
            user_requests_limit=rate_limit_per_user,
            system_requests_used=system_count,
            system_requests_limit=rate_limit_system,
        )
    
    async def _calculate_retry_after(
        self,
        agent_id: str,
        user_id: str | None,
        cutoff: datetime,
        is_user_limit: bool,
    ) -> int:
        """
        Calculate accurate retry_after_seconds based on the oldest request in the window.
        
        Args:
            agent_id: The ID of the agent
            user_id: The ID of the user (only used for user limits)
            cutoff: The cutoff time for the sliding window
            is_user_limit: Whether this is a user limit or system limit
        
        Returns:
            Seconds until the oldest request expires from the window
        """
        query = {"agent_id": agent_id, "timestamp": {"$gt": cutoff}}
        if is_user_limit and user_id:
            query["user_id"] = user_id
        
        # Find the oldest request in the current window
        oldest = await self._collection.find_one(
            query,
            sort=[("timestamp", 1)]  # Ascending = oldest first
        )
        
        if oldest and "timestamp" in oldest:
            oldest_time = oldest["timestamp"]
            # Time until oldest request expires = 1 hour from when it was made
            expires_at = oldest_time + timedelta(hours=1)
            retry_after = (expires_at - datetime.utcnow()).total_seconds()
            return max(1, int(retry_after))  # At least 1 second
        
        # Fallback to 1 hour if we can't find the oldest request
        return 3600

    async def record_request(
        self,
        agent_id: str,
        user_id: str,
    ) -> None:
        """
        Record a request for rate limiting purposes.
        Call this after a request has been successfully made.
        
        Args:
            agent_id: The ID of the agent that was requested
            user_id: The ID of the user who made the request (required)
        
        Raises:
            ValueError: If user_id is not provided
        """
        self._ensure_initialized()
        
        if not user_id:
            raise ValueError("user_id is required to record a request")
        
        await self._collection.insert_one({
            "agent_id": agent_id,
            "user_id": user_id,
            "timestamp": datetime.utcnow(),
        })
        
        logger.debug(
            f"RateLimitService: Recorded request for agent {agent_id}, user {user_id}"
        )
    
    async def get_usage_stats(
        self,
        agent_id: str,
        user_id: str | None = None,
    ) -> dict:
        """
        Get current rate limit usage statistics for an agent.
        
        Args:
            agent_id: The ID of the agent
            user_id: Optional user ID to get user-specific stats
        
        Returns:
            Dictionary with usage statistics
        """
        self._ensure_initialized()
        
        cutoff = datetime.utcnow() - timedelta(hours=1)
        
        system_count = await self._collection.count_documents({
            "agent_id": agent_id,
            "timestamp": {"$gt": cutoff}
        })
        
        stats = {
            "agent_id": agent_id,
            "system_requests_this_hour": system_count,
        }
        
        if user_id:
            user_count = await self._collection.count_documents({
                "agent_id": agent_id,
                "user_id": user_id,
                "timestamp": {"$gt": cutoff}
            })
            stats["user_requests_this_hour"] = user_count
        
        return stats


# Singleton instance
rate_limit_service = RateLimitService()
