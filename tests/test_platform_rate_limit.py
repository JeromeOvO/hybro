from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from common.dto import RateLimitResult


NOW = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)


class InMemoryRateLimitCollection:
    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs = list(docs or [])

    async def count_documents(self, query: dict) -> int:
        return sum(1 for doc in self.docs if self._matches(doc, query))

    async def find_one(self, query: dict, sort: list[tuple[str, int]] | None = None):
        matches = [doc for doc in self.docs if self._matches(doc, query)]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda doc: doc[key], reverse=direction < 0)
        return matches[0] if matches else None

    async def insert_one(self, doc: dict):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=len(self.docs))

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$gt" in expected and not actual > expected["$gt"]:
                    return False
            elif actual != expected:
                return False
        return True


@pytest.mark.asyncio
async def test_agent_rate_limiter_allows_disabled_limits():
    from platform_module.rate_limit import PlatformAgentRateLimiter

    limiter = PlatformAgentRateLimiter(
        collection=InMemoryRateLimitCollection(), clock=lambda: NOW
    )

    result = await limiter.check_agent_limit("agent-1", "user-1", None, None)

    assert result.allowed is True
    assert result.user_requests_used == 0
    assert result.system_requests_used == 0


@pytest.mark.asyncio
async def test_agent_rate_limiter_blocks_per_user_limit_with_retry_after():
    from platform_module.rate_limit import PlatformAgentRateLimiter

    collection = InMemoryRateLimitCollection(
        [
            {"agent_id": "agent-1", "user_id": "user-1", "timestamp": NOW - timedelta(minutes=30)},
            {"agent_id": "agent-1", "user_id": "user-1", "timestamp": NOW - timedelta(minutes=10)},
            {"agent_id": "agent-1", "user_id": "other", "timestamp": NOW - timedelta(minutes=5)},
        ]
    )
    limiter = PlatformAgentRateLimiter(collection=collection, clock=lambda: NOW)

    result = await limiter.check_agent_limit("agent-1", "user-1", 2, 10)

    assert result.allowed is False
    assert result.user_requests_used == 2
    assert result.user_requests_limit == 2
    assert result.system_requests_used == 3
    assert result.retry_after_seconds == 1800
    assert "2 requests per hour" in result.reason


@pytest.mark.asyncio
async def test_agent_rate_limiter_blocks_system_limit():
    from platform_module.rate_limit import PlatformAgentRateLimiter

    collection = InMemoryRateLimitCollection(
        [
            {"agent_id": "agent-1", "user_id": "u1", "timestamp": NOW - timedelta(minutes=20)},
            {"agent_id": "agent-1", "user_id": "u2", "timestamp": NOW - timedelta(minutes=15)},
        ]
    )
    limiter = PlatformAgentRateLimiter(collection=collection, clock=lambda: NOW)

    result = await limiter.check_agent_limit("agent-1", "user-1", 5, 2)

    assert result.allowed is False
    assert result.system_requests_used == 2
    assert result.system_requests_limit == 2
    assert result.retry_after_seconds == 2400
    assert "System limit of 2 requests per hour" in result.reason


@pytest.mark.asyncio
async def test_agent_rate_limiter_records_requests():
    from platform_module.rate_limit import PlatformAgentRateLimiter

    collection = InMemoryRateLimitCollection()
    limiter = PlatformAgentRateLimiter(collection=collection, clock=lambda: NOW)

    await limiter.record_agent_request("agent-1", "user-1")

    assert collection.docs == [
        {"agent_id": "agent-1", "user_id": "user-1", "timestamp": NOW}
    ]


@pytest.mark.asyncio
async def test_api_key_rate_limiter_blocks_per_key_and_global_limits():
    from platform_module.rate_limit import PlatformAPIKeyRateLimiter

    collection = InMemoryRateLimitCollection(
        [
            {"key_id": "key-1", "timestamp": NOW - timedelta(minutes=45)},
            {"key_id": "key-2", "timestamp": NOW - timedelta(minutes=30)},
            {"key_id": "key-3", "timestamp": NOW - timedelta(minutes=15)},
        ]
    )
    limiter = PlatformAPIKeyRateLimiter(
        collection=collection,
        clock=lambda: NOW,
        per_key_limit_message=lambda limit: f"Rate limit exceeded: {limit} requests per hour",
        global_limit_message="Service temporarily unavailable due to high traffic",
    )

    per_key = await limiter.check_api_key_limit("key-1", 1, 10)
    global_limit = await limiter.check_api_key_limit("new-key", 10, 3)

    assert per_key.allowed is False
    assert per_key.message == "Rate limit exceeded: 1 requests per hour"
    assert per_key.retry_after_seconds == 900
    assert global_limit.allowed is False
    assert global_limit.message == "Service temporarily unavailable due to high traffic"
    assert global_limit.retry_after_seconds == 900


@pytest.mark.asyncio
async def test_api_key_rate_limiter_disabled_limits_allow_requests():
    from platform_module.rate_limit import PlatformAPIKeyRateLimiter

    limiter = PlatformAPIKeyRateLimiter(
        collection=InMemoryRateLimitCollection(), clock=lambda: NOW
    )

    result = await limiter.check_api_key_limit("key-1", None, None)

    assert result.allowed is True


@pytest.mark.asyncio
async def test_protocol_rate_limiter_returns_common_dto():
    from platform_module.rate_limit import PlatformProtocolRateLimiter

    limiter = PlatformProtocolRateLimiter(
        collection=InMemoryRateLimitCollection(
            [{"scope": "gateway", "key": "k1", "timestamp": NOW}]
        ),
        scope="gateway",
        clock=lambda: NOW,
    )

    result = await limiter.check("k1", 1, 3600)

    assert isinstance(result, RateLimitResult)
    assert result.allowed is False
    assert result.reason == "rate_limit_exceeded"
    assert result.info is not None
    assert result.info.limit == 1
    assert result.info.remaining == 0
