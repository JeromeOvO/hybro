"""
Unit tests for AgentResolverService.

Tests cover:
- _HealthCache: get/set, TTL expiry
- _pick_first_healthy: cache hit, cache miss with probe, all unhealthy
- resolve: empty candidates, sanitization fail-fast, health-check bypass
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.resolver import (
    AgentResolverFacadeRepository,
    AgentResolverService,
    ResolveResult,
    _HealthCache,
)
from common.dto import AgentInfo
from llm_gateway.errors import LLMModelRoutingError
from models.agent import AgentStatus

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
    agent.agent_status = AgentStatus.active
    agent.is_public = True
    agent.provider_id = None
    agent.agent_card.name = name
    agent.agent_card.url = f"https://{name}.example.com"
    agent.agent_card.default_input_modes = ["*/*"]
    return agent


class TestPickFirstHealthy:
    @pytest.fixture
    def resolver(self):
        svc = object.__new__(AgentResolverService)
        svc._resolution_repository = MagicMock()
        svc.agent_selection_service = None
        svc.capability_issue_reader = None
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


@pytest.mark.asyncio
async def test_probe_agent_delegates_to_adapter_health_probe(monkeypatch):
    from agent import resolver as resolver_module

    captured = {}

    async def _probe(agent_url: str, *, timeout: float):
        captured["agent_url"] = agent_url
        captured["timeout"] = timeout
        return SimpleNamespace(
            is_healthy=True,
            card=None,
            status_code=200,
            error=None,
        )

    monkeypatch.setattr(resolver_module, "probe_agent_card_for_health", _probe)
    agent = _make_agent("a1", "Alpha")

    assert await AgentResolverService._probe_agent(agent) is True
    assert captured == {
        "agent_url": "https://Alpha.example.com",
        "timeout": 3.0,
    }


@pytest.mark.asyncio
async def test_reorder_by_llm_uses_bound_agent_selection_service():
    svc = object.__new__(AgentResolverService)
    svc._resolution_repository = MagicMock()
    svc.agent_selection_service = MagicMock()
    svc.capability_issue_reader = None
    svc._health_cache = _HealthCache(ttl=60.0)
    svc.agent_selection_service.select_best_agent_for_task = AsyncMock(
        return_value="a2"
    )
    svc.agent_selection_service.rank_agents_for_task = AsyncMock(
        return_value=["a2", "a1"]
    )
    a1 = _make_agent("a1", "Alpha")
    a2 = _make_agent("a2", "Beta")
    a1.agent_status = a2.agent_status = AgentStatus.active

    result = await svc._reorder_by_llm("query", [a1, a2])

    assert result == [a2, a1]
    svc.agent_selection_service.rank_agents_for_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_reorder_by_llm_falls_back_when_agent_selection_service_unbound():
    svc = object.__new__(AgentResolverService)
    svc._resolution_repository = MagicMock()
    svc.agent_selection_service = None
    svc.capability_issue_reader = None
    svc._health_cache = _HealthCache(ttl=60.0)
    a1 = _make_agent("a1", "Alpha")
    a2 = _make_agent("a2", "Beta")

    assert await svc._reorder_by_llm("query", [a1, a2]) == [a1, a2]


@pytest.mark.asyncio
async def test_reorder_by_llm_falls_back_on_routing_errors():
    svc = object.__new__(AgentResolverService)
    svc._resolution_repository = MagicMock()
    svc.agent_selection_service = MagicMock()
    svc.capability_issue_reader = None
    svc._health_cache = _HealthCache(ttl=60.0)
    svc.agent_selection_service.select_best_agent_for_task = AsyncMock(
        side_effect=LLMModelRoutingError("unregistered model")
    )
    a1 = _make_agent("a1", "Alpha")
    a2 = _make_agent("a2", "Beta")
    a1.agent_status = a2.agent_status = AgentStatus.active

    assert await svc._reorder_by_llm("query", [a1, a2]) == [a1, a2]


# =============================================================================
# resolve Tests
# =============================================================================


class TestResolve:
    @pytest.fixture
    def mock_capability_issue_reader(self):
        reader = MagicMock()
        reader.get_excluded_agent_ids = AsyncMock(return_value=set())
        return reader

    @pytest.fixture
    def resolver(self, mock_capability_issue_reader):
        return AgentResolverService(
            repository=MagicMock(),
            capability_issue_reader=mock_capability_issue_reader,
        )

    @pytest.mark.asyncio
    async def test_returns_failure_when_no_candidates(self, resolver):
        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver._resolution_repository.query_similar_agents = AsyncMock(
            return_value=[]
        )

        result = await resolver.resolve("test query")
        assert result.agent is None
        assert "No active agents" in result.failure_reason

    @pytest.mark.asyncio
    async def test_fail_fast_when_all_allowed_ids_sanitized_away(self, resolver):
        resolver._sanitize_allowed_ids = AsyncMock(return_value=[])

        result = await resolver.resolve("test", allowed_agent_ids=["a1"])
        assert result.agent is None
        assert "currently available" in result.failure_reason
        resolver._resolution_repository.query_similar_agents = AsyncMock()
        resolver._resolution_repository.query_similar_agents.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_top_candidate_when_health_disabled(self, resolver):
        a1 = _make_agent("a1", "Alpha")
        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver._resolution_repository.query_similar_agents = AsyncMock(
            return_value=[a1]
        )

        with patch("agent.resolver.settings") as mock_settings:
            mock_settings.agent_health_check_enabled = False
            result = await resolver.resolve("test query")

        assert result.agent is a1

    @pytest.mark.asyncio
    async def test_delegates_to_pick_first_healthy_when_enabled(self, resolver):
        a1 = _make_agent("a1", "Alpha")
        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver._resolution_repository.query_similar_agents = AsyncMock(
            return_value=[a1]
        )
        resolver._pick_first_healthy = AsyncMock(
            return_value=ResolveResult(agent=a1, tried_agents=["Alpha"])
        )

        with patch("agent.resolver.settings") as mock_settings:
            mock_settings.agent_health_check_enabled = True
            result = await resolver.resolve("test query")

        assert result.agent is a1
        resolver._pick_first_healthy.assert_called_once_with([a1])

    @pytest.mark.asyncio
    async def test_passes_excluded_ids_to_query(
        self, resolver, mock_capability_issue_reader
    ):
        mock_capability_issue_reader.get_excluded_agent_ids = AsyncMock(
            return_value={"bad-agent"}
        )
        a1 = _make_agent("a1", "Alpha")
        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver._resolution_repository.query_similar_agents = AsyncMock(
            return_value=[a1]
        )

        with patch("agent.resolver.settings") as mock_settings:
            mock_settings.agent_health_check_enabled = False
            result = await resolver.resolve("test query")

        assert result.agent is a1
        call_kwargs = resolver._resolution_repository.query_similar_agents.call_args
        assert call_kwargs.kwargs["excluded_agent_ids"] == {"bad-agent"}

    @pytest.mark.asyncio
    async def test_excluded_candidate_is_removed_before_llm_reranking(
        self, resolver, mock_capability_issue_reader
    ):
        safe = _make_agent("safe", "Safe")
        broken = _make_agent("broken", "Broken")
        mock_capability_issue_reader.get_excluded_agent_ids = AsyncMock(
            return_value={"broken"}
        )
        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver._resolution_repository.query_similar_agents = AsyncMock(
            return_value=[broken, safe]
        )
        resolver.agent_selection_service = MagicMock()
        resolver.agent_selection_service.rank_agents_for_task = AsyncMock()

        with patch("agent.resolver.settings") as mock_settings:
            mock_settings.agent_health_check_enabled = False
            result = await resolver.resolve("test query")

        assert result.agent is safe
        resolver.agent_selection_service.rank_agents_for_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_required_input_modes_to_lexical_query(self, resolver):
        image_agent = _make_agent("image", "Image")
        resolver._sanitize_allowed_ids = AsyncMock(return_value=["image"])
        resolver._resolution_repository.query_similar_agents = AsyncMock(
            return_value=[image_agent]
        )

        with patch("agent.resolver.settings") as mock_settings:
            mock_settings.agent_health_check_enabled = False
            result = await resolver.resolve(
                "inspect image",
                allowed_agent_ids=["image"],
                required_input_modes=["image/png"],
            )

        assert result.agent is image_agent
        assert resolver._resolution_repository.query_similar_agents.await_args.kwargs[
            "required_input_modes"
        ] == ["image/png"]

    @pytest.mark.asyncio
    async def test_singleton_fallback_rejects_unsupported_input_modes(self, resolver):
        text_agent = _make_agent("text", "Text")
        text_agent.agent_card.default_input_modes = ["text"]
        resolver._sanitize_allowed_ids = AsyncMock(return_value=["text"])
        resolver._resolution_repository.query_similar_agents = AsyncMock(
            return_value=[]
        )
        resolver._resolution_repository.get_agents_with_conditions_visible = AsyncMock(
            return_value=[text_agent]
        )

        result = await resolver.resolve(
            "inspect image",
            allowed_agent_ids=["text"],
            required_input_modes=["image/png"],
        )

        assert result.agent is None
        assert result.tried_agents == []

    @pytest.mark.asyncio
    async def test_llm_reranks_only_first_five_and_preserves_lexical_tail(
        self, resolver
    ):
        candidates = [
            _make_agent(f"a{index}", f"Agent {index}") for index in range(1, 8)
        ]
        for candidate in candidates:
            candidate.agent_status = AgentStatus.active
            candidate.agent_card.description = ""
            candidate.agent_card.capabilities = {}
            candidate.agent_card.skills = []

        resolver._sanitize_allowed_ids = AsyncMock(return_value=None)
        resolver._resolution_repository.query_similar_agents = AsyncMock(
            return_value=candidates
        )
        resolver.agent_selection_service = MagicMock()
        resolver.agent_selection_service.rank_agents_for_task = AsyncMock(
            return_value=["a5", "a4", "a3", "a2", "a1"]
        )
        resolver._pick_first_healthy = AsyncMock(
            return_value=ResolveResult(agent=candidates[4], tried_agents=["Agent 5"])
        )

        with patch("agent.resolver.settings") as mock_settings:
            mock_settings.agent_health_check_enabled = True
            result = await resolver.resolve("test query", count=7)

        assert result.agent is candidates[4]
        rerank_candidates = (
            resolver.agent_selection_service.rank_agents_for_task.await_args.args[1]
        )
        assert [candidate.agent_id for candidate in rerank_candidates] == [
            "a1",
            "a2",
            "a3",
            "a4",
            "a5",
        ]
        reordered = resolver._pick_first_healthy.await_args.args[0]
        assert [candidate.agent_id for candidate in reordered] == [
            "a5",
            "a4",
            "a3",
            "a2",
            "a1",
            "a6",
            "a7",
        ]


@pytest.mark.asyncio
async def test_facade_repository_uses_message_matcher_for_input_mode_filtering():
    facade = MagicMock()
    matched_agent = AgentInfo(
        agent_id="image",
        name="Image",
        url="https://image.example.com",
        raw_card={
            "name": "Image",
            "url": "https://image.example.com",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["image/*"],
            "defaultOutputModes": ["text"],
            "skills": [],
        },
    )
    facade.match_for_message = AsyncMock(
        return_value=[{"agent": matched_agent, "agent_id": "image"}]
    )
    repository = AgentResolverFacadeRepository(facade)

    result = await repository.query_similar_agents(
        "inspect image",
        count=5,
        allowed_agent_ids=["image", "text"],
        excluded_agent_ids=set(),
        active_only=True,
        user_id="u1",
        required_input_modes=["image/png"],
    )

    facade.match_for_message.assert_awaited_once_with(
        "inspect image",
        limit=5,
        filter_ids=["image", "text"],
        requesting_user_id="u1",
        required_input_modes=["image/png"],
    )
    assert [agent.agent_id for agent in result] == ["image"]
