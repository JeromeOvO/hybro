"""
Unit tests for Agent Service.

Tests cover:
- Agent registration
- Agent retrieval (by ID, by provider, all agents)
- Agent updates
- Agent deletion
- URL normalization
- Duplicate detection
- Visibility filtering
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.service import AgentService, _agent_info_to_legacy_agent
from agent.url_utils import is_local_agent_url, normalize_agent_url
from common.dto.agent import AgentInfo
from models.agent import Agent, AgentStatus
from models.error import (
    AgentIdRequiredError,
)
from models.request import AgentCenterRequest
from models.response import AgentCenterResponse

# =============================================================================
# URL Normalization Tests
# =============================================================================


class TestNormalizeAgentUrl:
    """Tests for normalize_agent_url function."""

    def test_removes_well_known_path(self):
        """Should remove .well-known paths."""
        url = "https://agent.example.com/.well-known/agent.json"
        result = normalize_agent_url(url)
        assert "/.well-known" not in result
        assert result == "https://agent.example.com"

    def test_removes_agent_card_path(self):
        """Should remove agent-card.json path."""
        url = "https://agent.example.com/.well-known/agent-card.json"
        result = normalize_agent_url(url)
        assert "agent-card.json" not in result

    def test_normalizes_localhost_aliases(self):
        """Should normalize localhost aliases to 'localhost'."""
        urls = [
            "http://127.0.0.1:8000/agent",
            "http://0.0.0.0:8000/agent",
            "http://[::1]:8000/agent",  # IPv6 addresses need brackets
        ]
        for url in urls:
            result = normalize_agent_url(url)
            assert "localhost" in result

    def test_removes_default_ports(self):
        """Should remove default ports (80 for http, 443 for https)."""
        assert ":443" not in normalize_agent_url("https://agent.example.com:443/path")
        assert ":80" not in normalize_agent_url("http://agent.example.com:80/path")

    def test_preserves_non_default_ports(self):
        """Should preserve non-default ports."""
        result = normalize_agent_url("https://agent.example.com:8443/path")
        assert ":8443" in result

    def test_removes_trailing_slash(self):
        """Should remove trailing slashes."""
        result = normalize_agent_url("https://agent.example.com/path/")
        assert not result.endswith("/")

    def test_lowercases_hostname(self):
        """Should lowercase the hostname."""
        result = normalize_agent_url("https://AGENT.EXAMPLE.COM/path")
        assert "agent.example.com" in result

    def test_preserves_query_string(self):
        """Should preserve query strings."""
        result = normalize_agent_url("https://agent.example.com/path?key=value")
        assert "key=value" in result

    def test_handles_empty_url(self):
        """Should handle empty URL gracefully."""
        assert normalize_agent_url("") == ""
        assert normalize_agent_url(None) is None

    def test_handles_invalid_url(self):
        """Should return invalid URL as-is."""
        invalid_url = "not-a-valid-url"
        result = normalize_agent_url(invalid_url)
        assert result == invalid_url


# =============================================================================
# is_local_agent_url Tests
# =============================================================================


class TestIsLocalAgentUrl:
    """Tests for the is_local_agent_url helper."""

    @pytest.mark.parametrize("url", [
        "http://localhost:8000",
        "http://localhost:8000/agent",
        "http://127.0.0.1:9000",
        "http://0.0.0.0:10020",
        "http://[::1]:8080",
        "http://LOCALHOST:8000",          # case-insensitive
    ])
    def test_local_urls_return_true(self, url):
        assert is_local_agent_url(url) is True, f"Expected True for {url!r}"

    @pytest.mark.parametrize("url", [
        "http://agent.example.com:8000",
        "https://api.hybro.ai/v1/agent",
        "http://192.168.1.10:10020",      # private IP but not loopback
        "http://10.0.0.1:10020",          # RFC-1918, not loopback
        "http://172.16.0.1:10020",        # RFC-1918, not loopback
    ])
    def test_non_local_urls_return_false(self, url):
        assert is_local_agent_url(url) is False, f"Expected False for {url!r}"

    def test_empty_string_returns_false(self):
        assert is_local_agent_url("") is False

    def test_none_returns_false(self):
        assert is_local_agent_url(None) is False

    def test_invalid_url_returns_false(self):
        assert is_local_agent_url("not-a-valid-url") is False


# =============================================================================
# Agent Service Fixtures
# =============================================================================


@pytest.fixture
def agent_service():
    """Create an AgentService instance for testing."""
    service = AgentService()
    return service


@pytest.fixture
def mock_agent_db_service():
    """Create mock database service for agent operations."""
    mock = MagicMock()
    mock.add_agent = AsyncMock(return_value=True)
    mock.get_agent_by_agent_id = AsyncMock(return_value=None)
    mock.get_all_visible_agents = AsyncMock(return_value=[])
    mock.get_all_active_agents = AsyncMock(return_value=[])
    mock.get_agents_by_provider_id = AsyncMock(return_value=[])
    mock.update_agent_by_agent_id = AsyncMock(return_value=True)
    mock.update_agent_agent_card_by_agent_id = AsyncMock(return_value=True)
    mock.delete_agent_by_agent_id = AsyncMock(return_value=True)
    mock.query_similar_agents = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_a2a_service():
    """Create mock A2A service."""
    mock = MagicMock()
    mock.get_agent_card_from_url = AsyncMock()
    return mock


def _info_from_agent(agent: Agent) -> AgentInfo:
    return AgentInfo(
        agent_id=agent.agent_id,
        name=agent.agent_card.name,
        description=agent.agent_card.description,
        url=agent.agent_card.url,
        provider_id=agent.provider_id,
        status=agent.agent_status.value,
        is_public=agent.is_public,
        public_url=agent.public_url,
        source=agent.source,
        hub_id=agent.hub_id,
        is_hub_online=agent.is_hub_online,
        rate_limit_per_user_per_hour=agent.rate_limit_per_user_per_hour,
        rate_limit_system_per_hour=agent.rate_limit_system_per_hour,
        call_count=agent.call_count,
    )


# =============================================================================
# Agent Registration Tests
# =============================================================================


class TestRegisterAgent:
    """Tests for register_agent method."""

    @pytest.mark.asyncio
    async def test_agent_center_register_agent_delegates_without_a2a_prefetch(self):
        from app_shell.agent_runtime import AppShellAgentCenter as AgentCenter

        center = AgentCenter()
        center.agent_service = MagicMock()
        center.agent_service.register_agent = AsyncMock(
            return_value=AgentCenterResponse(success=True, agent_id="agent-123")
        )
        center.a2a_service = MagicMock()
        center.a2a_service.get_agent_card_from_url = AsyncMock()
        request = AgentCenterRequest(agent_url="https://agent.example")

        result = await center.register_agent(request)

        assert result.success is True
        assert result.agent_id == "agent-123"
        center.agent_service.register_agent.assert_awaited_once_with(request)
        center.a2a_service.get_agent_card_from_url.assert_not_awaited()
        assert request.agent_card is None

    @pytest.mark.asyncio
    async def test_registers_new_agent(
        self, agent_service, mock_agent_db_service, sample_agent_card, mock_user
    ):
        """Should delegate registration to the bound facade."""
        facade = MagicMock()
        facade.register_agent = AsyncMock(
            return_value=AgentInfo(
                agent_id="agent-123",
                provider_id=mock_user.user_id,
                name=sample_agent_card.name,
                description=sample_agent_card.description,
                url=sample_agent_card.url,
                public_url="https://public.hybro.ai/test-agent",
            )
        )
        agent_service.bind_facade(facade)
        
        request = AgentCenterRequest(
            agent_card=sample_agent_card,
            provider_id=mock_user.user_id,
        )
        result = await agent_service.register_agent(request)
        
        assert result.success is True
        assert result.agent_id == "agent-123"
        assert result.provider_id == mock_user.user_id
        facade.register_agent.assert_called_once_with(
            sample_agent_card.url,
            mock_user.user_id,
            preferred_subdomain=None,
            resolved_card=facade.register_agent.call_args.kwargs["resolved_card"],
        )
        resolved = facade.register_agent.call_args.kwargs["resolved_card"]
        assert resolved.raw_card["skills"][0]["id"] == "test-skill"

    def test_agent_info_conversion_preserves_raw_agent_card(self, sample_agent_card):
        from agent.translators import agent_info_from_doc

        info = agent_info_from_doc(
            {
                "agent_id": "agent-123",
                "provider_id": "user-123",
                "agent_status": "active",
                "agent_card": sample_agent_card.model_dump(mode="json"),
            }
        )

        agent = _agent_info_to_legacy_agent(info)

        assert agent.agent_card.skills[0].id == "test-skill"
        assert agent.agent_card.capabilities.streaming is True
        assert agent.agent_card.default_input_modes == ["text"]

    @pytest.mark.asyncio
    async def test_raises_error_when_agent_card_missing(self, agent_service):
        """Should fail fast before bind."""
        request = AgentCenterRequest(agent_card=None)
        
        with pytest.raises(RuntimeError, match="bind_facade"):
            await agent_service.register_agent(request)

    @pytest.mark.asyncio
    async def test_returns_error_for_duplicate_url(
        self, agent_service, sample_agent_card, sample_agent
    ):
        """Should map facade duplicate errors to a 400 response."""
        facade = MagicMock()
        facade.register_agent = AsyncMock(
            side_effect=ValueError("Agent with this URL is already registered")
        )
        agent_service.bind_facade(facade)
        request = AgentCenterRequest(agent_card=sample_agent_card)
        result = await agent_service.register_agent(request)
        
        assert result.success is False
        assert result.status_code == 400
        assert "already registered" in result.error.lower()

    @pytest.mark.asyncio
    async def test_handles_db_error_on_registration(
        self, agent_service, mock_agent_db_service, sample_agent_card
    ):
        """Should handle facade errors gracefully."""
        facade = MagicMock()
        facade.register_agent = AsyncMock(side_effect=Exception("DB connection failed"))
        agent_service.bind_facade(facade)
        request = AgentCenterRequest(agent_card=sample_agent_card)
        result = await agent_service.register_agent(request)
        
        assert result.success is False
        assert result.status_code == 500


# =============================================================================
# Agent Retrieval Tests
# =============================================================================


class TestQueryAgentByAgentId:
    """Tests for query_agent_by_agent_id method."""

    @pytest.mark.asyncio
    async def test_returns_public_agent(
        self, agent_service, mock_agent_db_service, sample_agent
    ):
        """Should return public agent from bound facade."""
        facade = MagicMock()
        facade.get_agent = AsyncMock(return_value=_info_from_agent(sample_agent))
        agent_service.bind_facade(facade)
        
        request = AgentCenterRequest(agent_id=sample_agent.agent_id)
        result = await agent_service.query_agent_by_agent_id(request)
        
        assert result.success is True
        assert result.agent.agent_id == sample_agent.agent_id

    @pytest.mark.asyncio
    async def test_raises_error_when_agent_id_missing(self, agent_service):
        """Should raise error when agent_id is not provided."""
        request = AgentCenterRequest(agent_id=None)
        
        with pytest.raises(AgentIdRequiredError):
            await agent_service.query_agent_by_agent_id(request)

    @pytest.mark.asyncio
    async def test_returns_404_for_private_agent_without_auth(
        self, agent_service, mock_agent_db_service, sample_private_agent
    ):
        """Should return 404 for private agent when user is not owner."""
        facade = MagicMock()
        facade.get_agent = AsyncMock(return_value=_info_from_agent(sample_private_agent))
        agent_service.bind_facade(facade)
        
        # Request without user_id (unauthenticated)
        request = AgentCenterRequest(
            agent_id=sample_private_agent.agent_id,
            user_id=None,
        )
        result = await agent_service.query_agent_by_agent_id(request)
        
        assert result.success is False
        assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_private_agent_for_owner(
        self, agent_service, mock_agent_db_service, sample_private_agent, mock_user
    ):
        """Should return private agent when user is the owner."""
        facade = MagicMock()
        facade.get_agent = AsyncMock(return_value=_info_from_agent(sample_private_agent))
        agent_service.bind_facade(facade)
        
        request = AgentCenterRequest(
            agent_id=sample_private_agent.agent_id,
            user_id=mock_user.user_id,  # Owner's user_id
        )
        result = await agent_service.query_agent_by_agent_id(request)
        
        assert result.success is True


class TestGetAgentsByProviderId:
    """Tests for get_agents_by_provider_id method."""

    @pytest.mark.asyncio
    async def test_returns_agents_for_provider(
        self, agent_service, mock_agent_db_service, sample_agent, mock_user
    ):
        """Should return agents owned by provider."""
        facade = MagicMock()
        facade.list_agents = AsyncMock(return_value=[_info_from_agent(sample_agent)])
        agent_service.bind_facade(facade)
        
        request = AgentCenterRequest(provider_id=mock_user.user_id)
        result = await agent_service.get_agents_by_provider_id(request)
        
        assert result.success is True
        assert len(result.agents) == 1

    @pytest.mark.asyncio
    async def test_returns_error_when_provider_id_missing(self, agent_service):
        """Should return error when provider_id is not provided."""
        request = AgentCenterRequest(provider_id=None)
        result = await agent_service.get_agents_by_provider_id(request)
        
        assert result.success is False
        assert result.status_code == 400


class TestGetAllActiveAgents:
    """Tests for get_all_active_agents method."""

    @pytest.mark.asyncio
    async def test_returns_active_agents(
        self, agent_service, mock_agent_db_service, sample_agent
    ):
        """Should return only active agents."""
        facade = MagicMock()
        facade.list_visible_agents = AsyncMock(return_value=[_info_from_agent(sample_agent)])
        agent_service.bind_facade(facade)
        
        request = AgentCenterRequest()
        result = await agent_service.get_all_active_agents(request)
        
        assert result.success is True
        assert len(result.agents) == 1

    @pytest.mark.asyncio
    async def test_passes_user_id_for_visibility(
        self, agent_service, mock_agent_db_service, mock_user
    ):
        """Should pass user_id for visibility filtering."""
        facade = MagicMock()
        facade.list_visible_agents = AsyncMock(return_value=[])
        agent_service.bind_facade(facade)
        
        request = AgentCenterRequest(user_id=mock_user.user_id)
        await agent_service.get_all_active_agents(request)
        
        facade.list_visible_agents.assert_called_once_with(
            user_id=mock_user.user_id,
            active_only=True,
        )

    @pytest.mark.asyncio
    async def test_get_agents_with_conditions_passes_query_and_limit(
        self, agent_service, mock_user
    ):
        """Should preserve legacy query filtering for conditional agent lists."""
        facade = MagicMock()
        facade.list_visible_agents = AsyncMock(return_value=[])
        agent_service.bind_facade(facade)
        query = {"agent_status": "active"}

        request = AgentCenterRequest(
            user_id=mock_user.user_id,
            query=query,
            limit=7,
        )
        result = await agent_service.get_agents_with_conditions(request)

        assert result.success is True
        facade.list_visible_agents.assert_called_once_with(
            user_id=mock_user.user_id,
            active_only=False,
            query=query,
            limit=7,
        )


# =============================================================================
# Agent Update Tests
# =============================================================================


class TestUpdateAgent:
    """Tests for update_agent method."""

    @pytest.mark.asyncio
    async def test_updates_whole_agent(
        self, agent_service, mock_agent_db_service, sample_agent
    ):
        """Should update through the bound facade."""
        facade = MagicMock()
        facade.update_agent = AsyncMock(return_value=_info_from_agent(sample_agent))
        agent_service.bind_facade(facade)
        
        updated_agent = sample_agent.model_copy(
            update={"agent_status": AgentStatus.inactive}
        )
        request = AgentCenterRequest(
            agent_id=sample_agent.agent_id,
            agent=updated_agent,
        )
        
        result = await agent_service.update_agent(request)
        
        assert result.success is True
        facade.update_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_agent_card_only(
        self, agent_service, mock_agent_db_service, sample_agent, sample_agent_card
    ):
        """Should update only the agent card through the bound facade."""
        facade = MagicMock()
        facade.update_agent = AsyncMock(return_value=_info_from_agent(sample_agent))
        agent_service.bind_facade(facade)
        
        request = AgentCenterRequest(
            agent_id=sample_agent.agent_id,
            agent_card=sample_agent_card,
        )
        
        result = await agent_service.update_agent(request)
        
        assert result.success is True
        facade.update_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_error_when_agent_id_missing(self, agent_service):
        """Should raise error when agent_id is not provided."""
        request = AgentCenterRequest(agent_id=None)
        
        with pytest.raises(AgentIdRequiredError):
            await agent_service.update_agent(request)


# =============================================================================
# Agent Deletion Tests
# =============================================================================


class TestRemoveAgent:
    """Tests for remove_agent method."""

    @pytest.mark.asyncio
    async def test_removes_agent(
        self, agent_service, mock_agent_db_service, sample_agent
    ):
        """Should remove agent successfully through facade."""
        facade = MagicMock()
        facade.get_agent = AsyncMock(return_value=_info_from_agent(sample_agent))
        facade.delete_agent = AsyncMock(return_value=True)
        agent_service.bind_facade(facade)
        
        request = AgentCenterRequest(agent_id=sample_agent.agent_id)
        result = await agent_service.remove_agent(request)
        
        assert result.success is True
        facade.delete_agent.assert_called_once_with(
            sample_agent.agent_id,
            sample_agent.provider_id,
        )

    @pytest.mark.asyncio
    async def test_raises_error_when_agent_id_missing(self, agent_service):
        """Should raise error when agent_id is not provided."""
        request = AgentCenterRequest(agent_id=None)
        
        with pytest.raises(AgentIdRequiredError):
            await agent_service.remove_agent(request)


# =============================================================================
# Agent Card Validation Tests
# =============================================================================


class TestValidateAgentCard:
    """Tests for validate_agent_card method."""

    @pytest.mark.asyncio
    async def test_validates_complete_card(self, agent_service):
        """Should return no errors for valid card."""
        valid_card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": "https://agent.example.com",
            "version": "1.0.0",
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [{"id": "skill-1", "name": "Test Skill"}],
        }
        
        errors = await agent_service.validate_agent_card(valid_card)
        assert errors == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing_field", [
        "name",
        "description",
        "url",
        "version",
        "capabilities",
        "defaultInputModes",
        "defaultOutputModes",
        "skills",
    ])
    async def test_detects_each_missing_required_field(self, agent_service, missing_field):
        """Should detect each individual missing required field."""
        complete_card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": "https://agent.example.com",
            "version": "1.0.0",
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [{"id": "skill-1", "name": "Test Skill"}],
        }
        del complete_card[missing_field]

        errors = await agent_service.validate_agent_card(complete_card)
        assert len(errors) >= 1
        assert any(missing_field in e for e in errors)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("invalid_url", [
        "not-a-valid-url",
        "ftp://agent.example.com",
        "agent.example.com",
        "",
    ])
    async def test_validates_url_format(self, agent_service, invalid_url):
        """Should reject URLs that don't start with http:// or https://."""
        card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": invalid_url,
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [{"id": "s1", "name": "Skill"}],
        }

        errors = await agent_service.validate_agent_card(card)
        assert any("url" in e.lower() for e in errors)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_capabilities", [
        "not-an-object",
        ["a", "list"],
        42,
        True,
    ])
    async def test_validates_capabilities_type(self, agent_service, bad_capabilities):
        """Should reject non-dict capabilities."""
        card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": "https://agent.example.com",
            "version": "1.0.0",
            "capabilities": bad_capabilities,
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [{"id": "s1", "name": "Skill"}],
        }

        errors = await agent_service.validate_agent_card(card)
        assert any("capabilities" in e.lower() for e in errors)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["defaultInputModes", "defaultOutputModes"])
    async def test_validates_mode_fields_must_be_list(self, agent_service, field):
        """Should reject non-list mode fields."""
        card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": "https://agent.example.com",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [{"id": "s1", "name": "Skill"}],
        }
        card[field] = "not-a-list"

        errors = await agent_service.validate_agent_card(card)
        assert any(field in e for e in errors)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["defaultInputModes", "defaultOutputModes"])
    async def test_validates_mode_fields_items_must_be_strings(self, agent_service, field):
        """Should reject mode field items that are not strings."""
        card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": "https://agent.example.com",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [{"id": "s1", "name": "Skill"}],
        }
        card[field] = [123, True]

        errors = await agent_service.validate_agent_card(card)
        assert any(field in e for e in errors)

    @pytest.mark.asyncio
    async def test_validates_skills_must_be_list(self, agent_service):
        """Should reject non-list skills."""
        card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": "https://agent.example.com",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": "not-a-list",
        }

        errors = await agent_service.validate_agent_card(card)
        assert any("skills" in e.lower() for e in errors)

    @pytest.mark.asyncio
    async def test_validates_empty_skills_array(self, agent_service):
        """Should warn when skills array is empty."""
        card = {
            "name": "Test Agent",
            "description": "A test agent",
            "url": "https://agent.example.com",
            "version": "1.0.0",
            "capabilities": {},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [],
        }

        errors = await agent_service.validate_agent_card(card)
        assert any("skills" in e.lower() and "empty" in e.lower() for e in errors)


# =============================================================================
# Sensitive Information Masking Tests
# =============================================================================


class TestMaskSensitiveInformation:
    """Tests for _mask_sensitive_information method."""

    def test_masks_top_level_field(self, agent_service):
        """Should mask top-level sensitive fields."""
        from models.response import AgentCenterResponse
        
        response = AgentCenterResponse(
            success=True,
            agent_url="https://secret-agent.example.com",
        )
        
        masked = agent_service._mask_sensitive_information(response, ["agent_url"])
        
        assert masked.agent_url is None or masked.agent_url == ""

    def test_masks_nested_field_in_agent(self, agent_service, sample_agent):
        """Should mask nested fields in agent object."""
        from models.response import AgentCenterResponse
        
        response = AgentCenterResponse(
            success=True,
            agent=sample_agent,
        )
        
        masked = agent_service._mask_sensitive_information(
            response, ["agent_card.url"]
        )
        
        # The URL should be masked
        assert masked.agent.agent_card.url == ""

    def test_masks_nested_field_in_agents_list(self, agent_service, sample_agent):
        """Should mask nested fields in agents list."""
        from models.response import AgentCenterResponse
        
        response = AgentCenterResponse(
            success=True,
            agents=[sample_agent, sample_agent],
        )
        
        masked = agent_service._mask_sensitive_information(
            response, ["agent_card.url"]
        )
        
        # All agent URLs should be masked
        for agent in masked.agents:
            assert agent.agent_card.url == ""
