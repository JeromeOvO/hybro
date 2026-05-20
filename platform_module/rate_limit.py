from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from common.dto import RateLimitInfo, RateLimitResult
from common.errors import PlatformRouteError


class RateLimitCollection(Protocol):
    async def count_documents(self, query: dict) -> int: ...
    async def find_one(
        self, query: dict, sort: list[tuple[str, int]] | None = None
    ) -> dict | None: ...
    async def insert_one(self, doc: dict): ...


class NoopRateLimitCollection:
    async def count_documents(self, query: dict) -> int:
        return 0

    async def find_one(
        self, query: dict, sort: list[tuple[str, int]] | None = None
    ) -> dict | None:
        return None

    async def insert_one(self, doc: dict):
        return None


@dataclass(frozen=True)
class AgentRateLimitResult:
    allowed: bool
    reason: str | None = None
    user_requests_used: int = 0
    user_requests_limit: int | None = None
    system_requests_used: int = 0
    system_requests_limit: int | None = None
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class APIKeyRateLimitResult:
    allowed: bool
    message: str | None = None
    retry_after_seconds: int | None = None
    key_requests_used: int = 0
    key_requests_limit: int | None = None
    global_requests_used: int = 0
    global_requests_limit: int | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _cutoff(now: datetime, window_seconds: int) -> datetime:
    return _as_utc_naive(now) - timedelta(seconds=window_seconds)


def _retry_after(now: datetime, oldest: dict | None, window_seconds: int) -> int:
    if oldest and "timestamp" in oldest:
        expires_at = _as_utc_aware(oldest["timestamp"]) + timedelta(
            seconds=window_seconds
        )
        return max(1, int((expires_at - _as_utc_aware(now)).total_seconds()))
    return window_seconds


class PlatformAgentRateLimiter:
    def __init__(
        self,
        collection: RateLimitCollection | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
        window_seconds: int = 3600,
    ) -> None:
        self._collection = collection or NoopRateLimitCollection()
        self._clock = clock
        self._window_seconds = window_seconds

    async def check_agent_limit(
        self,
        agent_id: str,
        user_id: str,
        rate_limit_per_user: int | None,
        rate_limit_system: int | None,
    ) -> AgentRateLimitResult:
        if not user_id:
            raise ValueError("user_id is required to check rate limit")
        if rate_limit_per_user is None and rate_limit_system is None:
            return AgentRateLimitResult(allowed=True)

        now = self._clock()
        cutoff = _cutoff(now, self._window_seconds)
        user_count = 0
        system_count = 0

        if rate_limit_per_user is not None:
            user_count = await self._collection.count_documents(
                {"agent_id": agent_id, "user_id": user_id, "timestamp": {"$gt": cutoff}}
            )
        if rate_limit_system is not None:
            system_count = await self._collection.count_documents(
                {"agent_id": agent_id, "timestamp": {"$gt": cutoff}}
            )

        if rate_limit_per_user is not None and user_count >= rate_limit_per_user:
            oldest = await self._collection.find_one(
                {"agent_id": agent_id, "user_id": user_id, "timestamp": {"$gt": cutoff}},
                sort=[("timestamp", 1)],
            )
            return AgentRateLimitResult(
                allowed=False,
                reason=(
                    "Rate limit exceeded: You can only make "
                    f"{rate_limit_per_user} requests per hour to this agent"
                ),
                user_requests_used=user_count,
                user_requests_limit=rate_limit_per_user,
                system_requests_used=system_count,
                system_requests_limit=rate_limit_system,
                retry_after_seconds=_retry_after(now, oldest, self._window_seconds),
            )

        if rate_limit_system is not None and system_count >= rate_limit_system:
            oldest = await self._collection.find_one(
                {"agent_id": agent_id, "timestamp": {"$gt": cutoff}},
                sort=[("timestamp", 1)],
            )
            return AgentRateLimitResult(
                allowed=False,
                reason=(
                    "Agent is currently busy. System limit of "
                    f"{rate_limit_system} requests per hour has been reached"
                ),
                user_requests_used=user_count,
                user_requests_limit=rate_limit_per_user,
                system_requests_used=system_count,
                system_requests_limit=rate_limit_system,
                retry_after_seconds=_retry_after(now, oldest, self._window_seconds),
            )

        return AgentRateLimitResult(
            allowed=True,
            user_requests_used=user_count,
            user_requests_limit=rate_limit_per_user,
            system_requests_used=system_count,
            system_requests_limit=rate_limit_system,
        )

    async def record_agent_request(self, agent_id: str, user_id: str) -> None:
        if not user_id:
            raise ValueError("user_id is required to record a request")
        await self._collection.insert_one(
            {
                "agent_id": agent_id,
                "user_id": user_id,
                "timestamp": _as_utc_naive(self._clock()),
            }
        )


class PlatformAPIKeyRateLimiter:
    def __init__(
        self,
        collection: RateLimitCollection | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
        window_seconds: int = 3600,
        per_key_limit_message: Callable[[int], str] | None = None,
        global_limit_message: str = "Service temporarily unavailable due to high traffic",
    ) -> None:
        self._collection = collection or NoopRateLimitCollection()
        self._clock = clock
        self._window_seconds = window_seconds
        self._per_key_limit_message = per_key_limit_message or (
            lambda limit: f"Rate limit exceeded: {limit} requests per hour"
        )
        self._global_limit_message = global_limit_message

    async def check_api_key_limit(
        self,
        key_id: str,
        per_key_limit: int | None,
        global_limit: int | None,
    ) -> APIKeyRateLimitResult:
        if per_key_limit is None and global_limit is None:
            return APIKeyRateLimitResult(allowed=True)

        now = self._clock()
        cutoff = _cutoff(now, self._window_seconds)
        key_count = 0
        global_count = 0

        if per_key_limit is not None:
            key_count = await self._collection.count_documents(
                {"key_id": key_id, "timestamp": {"$gt": cutoff}}
            )
        if global_limit is not None:
            global_count = await self._collection.count_documents(
                {"timestamp": {"$gt": cutoff}}
            )

        if per_key_limit is not None and key_count >= per_key_limit:
            oldest = await self._collection.find_one(
                {"key_id": key_id, "timestamp": {"$gt": cutoff}},
                sort=[("timestamp", 1)],
            )
            return APIKeyRateLimitResult(
                allowed=False,
                message=self._per_key_limit_message(per_key_limit),
                retry_after_seconds=_retry_after(now, oldest, self._window_seconds),
                key_requests_used=key_count,
                key_requests_limit=per_key_limit,
                global_requests_used=global_count,
                global_requests_limit=global_limit,
            )

        if global_limit is not None and global_count >= global_limit:
            oldest = await self._collection.find_one(
                {"timestamp": {"$gt": cutoff}},
                sort=[("timestamp", 1)],
            )
            return APIKeyRateLimitResult(
                allowed=False,
                message=self._global_limit_message,
                retry_after_seconds=_retry_after(now, oldest, self._window_seconds),
                key_requests_used=key_count,
                key_requests_limit=per_key_limit,
                global_requests_used=global_count,
                global_requests_limit=global_limit,
            )

        return APIKeyRateLimitResult(
            allowed=True,
            key_requests_used=key_count,
            key_requests_limit=per_key_limit,
            global_requests_used=global_count,
            global_requests_limit=global_limit,
        )

    async def record_api_key_request(self, key_id: str) -> None:
        await self._collection.insert_one(
            {"key_id": key_id, "timestamp": _as_utc_naive(self._clock())}
        )


class PlatformProtocolRateLimiter:
    def __init__(
        self,
        collection: RateLimitCollection | None = None,
        *,
        scope: str,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._collection = collection or NoopRateLimitCollection()
        self._scope = scope
        self._clock = clock

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        now = self._clock()
        cutoff = _cutoff(now, window)
        query = {"scope": self._scope, "key": key, "timestamp": {"$gt": cutoff}}
        count = await self._collection.count_documents(query)
        remaining = max(0, limit - count)
        oldest = await self._collection.find_one(query, sort=[("timestamp", 1)])
        reset_at = (
            _as_utc_aware(oldest["timestamp"]) + timedelta(seconds=window)
            if oldest and "timestamp" in oldest
            else _as_utc_aware(now) + timedelta(seconds=window)
        )
        allowed = count < limit

        return RateLimitResult(
            allowed=allowed,
            info=RateLimitInfo(
                limit=limit,
                remaining=remaining if allowed else 0,
                reset_at=reset_at,
                scope=self._scope,
            ),
            reason=None if allowed else "rate_limit_exceeded",
        )

    async def check_global(self, limit: int, window: int) -> RateLimitResult:
        return await self.check("__global__", limit, window)

    async def record(self, key: str, **extra: Any) -> None:
        await self._collection.insert_one(
            {
                "scope": self._scope,
                "key": key,
                "timestamp": _as_utc_naive(self._clock()),
                **extra,
            }
        )


class PlatformRouteAPIKeyRateLimiter:
    def __init__(
        self,
        collection: RateLimitCollection | None = None,
        *,
        scope: str = "api_key",
        clock: Callable[[], datetime] = _utcnow,
        per_key_limit: int | None,
        global_limit: int | None,
        per_key_limit_message: Callable[[int], str] | None = None,
        global_limit_message: str = "Service temporarily unavailable due to high traffic",
    ) -> None:
        self._api_key_limiter = PlatformAPIKeyRateLimiter(
            collection=collection,
            clock=clock,
            per_key_limit_message=per_key_limit_message,
            global_limit_message=global_limit_message,
        )
        self._protocol_limiter = PlatformProtocolRateLimiter(
            collection=collection,
            scope=scope,
            clock=clock,
        )
        self._per_key_limit = per_key_limit
        self._global_limit = global_limit

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        return await self._protocol_limiter.check(key, limit, window)

    async def check_global(self, limit: int, window: int) -> RateLimitResult:
        return await self._protocol_limiter.check_global(limit, window)

    async def record(self, key: str, **extra: Any) -> None:
        await self._protocol_limiter.record(key, **extra)

    async def check_rate_limit(self, api_key) -> None:
        result = await self._api_key_limiter.check_api_key_limit(
            api_key.key_id,
            self._per_key_limit,
            self._global_limit,
        )
        if result.allowed:
            return
        raise PlatformRouteError(
            429,
            {
                "error": "rate_limit_exceeded",
                "message": result.message,
                "retry_after": result.retry_after_seconds or 3600,
            },
        )

    async def record_request(self, api_key) -> None:
        await self._api_key_limiter.record_api_key_request(api_key.key_id)


__all__ = [
    "APIKeyRateLimitResult",
    "AgentRateLimitResult",
    "NoopRateLimitCollection",
    "PlatformAPIKeyRateLimiter",
    "PlatformAgentRateLimiter",
    "PlatformProtocolRateLimiter",
    "PlatformRouteAPIKeyRateLimiter",
    "RateLimitCollection",
]
