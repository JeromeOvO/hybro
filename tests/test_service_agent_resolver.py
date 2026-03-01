"""
Unit tests for AgentResolverService.

Tests cover:
- _HealthCache: get/set, TTL expiry
- _pick_first_healthy: cache hit, cache miss with probe, all unhealthy
- resolve: empty candidates, sanitization fail-fast, health-check bypass
"""

import pytest
from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

from services.agent_resolver_service import (
    _HealthCache,
    AgentResolverService,
    ResolveResult,
)


# =============================================================================
# _HealthCache Tests
# =============================================================================


class TestHealthCache:
    def test_get_returns_none_for_unknown_key(self):
        cache = _HealthCache(ttl=60.0)
        assert cache.get("unknown") is None

    def test_set_and_get(self):
        cache = _HealthCache(ttl=60.0)
        cache.set("a1", True)
        assert cache.get("a1") is True

    def test_set_unhealthy(self):
        cache = _HealthCache(ttl=60.0)
        cache.set("a1", False)
        assert cache.get("a1") is False

    def test_expiry(self):
        cache = _HealthCache(ttl=0.0)
        cache.set("a1", True)
        assert cache.get("a1") is None

    def test_overwrite(self):
        cache = _HealthCache(ttl=60.0)
        cache.set("a1", True)
        cache.set("a1", False)
        assert cache.get("a1") is False


# =============================================================================
# _pick_first_healthy Tests
# =============================================================================


def _make_agent(agent_id: str, name: str):
    agent = MagicMock()
    agent.agent_id = agent_id
    agent.agent_card.name = name
    agent.agent_card.url = f"https://{name}.example.com"
    return agent


class TestPickFirstHealthy:
    @pytest.fixture
    def resolver(self):
        svc = object.__new__(AgentResolverService)
        svc.database_service = MagicMock()
        svc.openai_service = MagicMock()
        svc._health_cache = _HealthCache(ttl=60.0)
        return svc

    @pytest.mark.asyncio
    async def test_returns_first_healthy_from_cache(self, resolver):
        a1 = _make_agent("a1", "Alpha")
        resolver._health_cache.set("a1", True)

        result = await resolver._pick_first_healthy([a1])
        assert result.agent is a1
        assert result.failure_reason is None

    @pytest.mark.asyncio
    async def test_skips_cached_unhealthy(self, resolver):
        a1 = _make_agent("a1", "Alpha")
        a2 = _make_agent("a2", "Beta")
        resolver._health_cache.set("a1", False)
        resolver._health_cache.set("a2", True)

        result = await resolver._pick_first_healthy([a1, a2])
        assert result.agent is a2
        assert "Alpha" in result.tried_agents
        assert "Beta" in result.tried_agents

    @pytest.mark.asyncio
    async def test_probes_on_cache_miss(self, resolver):
        a1 = _make_agent("a1", "Alpha")

        with patch.object(
            AgentResolverService, "_probe_agent", new_callable=AsyncMock
        ) as mock_probe:
            mock_probe.return_value = True
            result = await resolver._pick_first_healthy([a1])

        assert result.agent is a1
        mock_probe.assert_called_once_with(a1)
        assert resolver._health_cache.get("a1") is True

    @pytest.mark.asyncio
    async def test_all_unhealthy_returns_failure(self, resolver):
        a1 = _make_agent("a1", "Alpha")
        a2 = _make_agent("a2", "Beta")

        with patch.object(
            AgentResolverService, "_probe_agent", new_callable=AsyncMock
        ) as mock_probe:
            mock_probe.return_value = False
            result = await resolver._pick_first_healthy([a1, a2])

        assert result.agent is None
        assert "unreachable" in result.failure_reason
        assert len(result.tried_agents) == 2


# =============================================================================
# resolve Tests
# =============================================================================


class TestResolve:
    @pytest.fixture
    def resolver(self):
        svc = object.__new__(AgentResolverService)
        svc.database_service = MagicMock()
        svc.openai_service = MagicMock()
        svc._health_cache = _HealthCache(ttl=60.0)
        return svc

    @pytest.mark.asyncio
    async def test_returns_failure_when_no_candidates(self, resolver):
        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver.database_service.query_similar_agents = AsyncMock(return_value=[])

        result = await resolver.resolve("test query")
        assert result.agent is None
        assert "No active agents" in result.failure_reason

    @pytest.mark.asyncio
    async def test_fail_fast_when_all_allowed_ids_sanitized_away(self, resolver):
        resolver._sanitize_allowed_ids = AsyncMock(return_value=[])

        result = await resolver.resolve("test", allowed_agent_ids=["a1"])
        assert result.agent is None
        assert "currently available" in result.failure_reason
        resolver.database_service.query_similar_agents = AsyncMock()
        resolver.database_service.query_similar_agents.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_top_candidate_when_health_disabled(self, resolver):
        a1 = _make_agent("a1", "Alpha")
        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver.database_service.query_similar_agents = AsyncMock(return_value=[a1])

        with patch("services.agent_resolver_service.settings") as mock_settings:
            mock_settings.agent_health_check_enabled = False
            result = await resolver.resolve("test query")

        assert result.agent is a1

    @pytest.mark.asyncio
    async def test_delegates_to_pick_first_healthy_when_enabled(self, resolver):
        a1 = _make_agent("a1", "Alpha")
        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver.database_service.query_similar_agents = AsyncMock(return_value=[a1])
        resolver._pick_first_healthy = AsyncMock(
            return_value=ResolveResult(agent=a1, tried_agents=["Alpha"])
        )

        with patch("services.agent_resolver_service.settings") as mock_settings:
            mock_settings.agent_health_check_enabled = True
            result = await resolver.resolve("test query")

        assert result.agent is a1
        resolver._pick_first_healthy.assert_called_once_with([a1])
