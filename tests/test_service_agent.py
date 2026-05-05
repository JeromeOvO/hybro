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

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.agent import Agent, AgentStatus
from models.request import AgentCenterRequest
from models.error import (
    AgentCardRequiredError,
    AgentIdRequiredError,
    AgentNotFoundError,
)
from services.agent_service import (
    AgentService,
    is_local_agent_url,
    normalize_agent_url,
)


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


# =============================================================================
# Agent Registration Tests
# =============================================================================


class TestRegisterAgent:
    """Tests for register_agent method."""

    @pytest.mark.asyncio
    async def test_registers_new_agent(
        self, agent_service, mock_agent_db_service, sample_agent_card, mock_user
    ):
        """Should register a new agent successfully."""
        agent_service.database_service = mock_agent_db_service
        
        request = AgentCenterRequest(
            agent_card=sample_agent_card,
            provider_id=mock_user.user_id,
        )
        
        # Mock no existing agent with same URL
        with patch.object(
            agent_service, "_find_agent_by_normalized_url", 
            AsyncMock(return_value=None)
        ):
            with patch(
                "services.agent_service.domain_alias_service.generate_public_url",
                AsyncMock(return_value="https://public.hybro.ai/test-agent"),
            ):
                result = await agent_service.register_agent(request)
        
        assert result.success is True
        assert result.agent_id is not None
        assert result.provider_id == mock_user.user_id
        mock_agent_db_service.add_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_error_when_agent_card_missing(self, agent_service):
        """Should raise error when agent_card is not provided."""
        request = AgentCenterRequest(agent_card=None)
        
        with pytest.raises(AgentCardRequiredError):
            await agent_service.register_agent(request)

    @pytest.mark.asyncio
    async def test_returns_error_for_duplicate_url(
        self, agent_service, sample_agent_card, sample_agent
    ):
        """Should return error when agent URL is already registered."""
        request = AgentCenterRequest(agent_card=sample_agent_card)
        
        with patch.object(
            agent_service, "_find_agent_by_normalized_url",
            AsyncMock(return_value=sample_agent),
        ):
            result = await agent_service.register_agent(request)
        
        assert result.success is False
        assert result.status_code == 400
        assert "already registered" in result.error.lower()

    @pytest.mark.asyncio
    async def test_handles_db_error_on_registration(
        self, agent_service, mock_agent_db_service, sample_agent_card
    ):
        """Should handle database errors gracefully."""
        agent_service.database_service = mock_agent_db_service
        mock_agent_db_service.add_agent.side_effect = Exception("DB connection failed")
        
        request = AgentCenterRequest(agent_card=sample_agent_card)
        
        with patch.object(
            agent_service, "_find_agent_by_normalized_url",
            AsyncMock(return_value=None),
        ):
            with patch(
                "services.agent_service.domain_alias_service.generate_public_url",
                AsyncMock(return_value=None),
            ):
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
        """Should return public agent."""
        agent_service.database_service = mock_agent_db_service
        mock_agent_db_service.get_agent_by_agent_id.return_value = sample_agent
        
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
        agent_service.database_service = mock_agent_db_service
        mock_agent_db_service.get_agent_by_agent_id.return_value = sample_private_agent
        
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
        agent_service.database_service = mock_agent_db_service
        mock_agent_db_service.get_agent_by_agent_id.return_value = sample_private_agent
        
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
        agent_service.database_service = mock_agent_db_service
        mock_agent_db_service.get_agents_by_provider_id.return_value = [sample_agent]
        
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
        agent_service.database_service = mock_agent_db_service
        mock_agent_db_service.get_all_active_agents.return_value = [sample_agent]
        
        request = AgentCenterRequest()
        result = await agent_service.get_all_active_agents(request)
        
        assert result.success is True
        assert len(result.agents) == 1

    @pytest.mark.asyncio
    async def test_passes_user_id_for_visibility(
        self, agent_service, mock_agent_db_service, mock_user
    ):
        """Should pass user_id for visibility filtering."""
        agent_service.database_service = mock_agent_db_service
        mock_agent_db_service.get_all_active_agents.return_value = []
        
        request = AgentCenterRequest(user_id=mock_user.user_id)
        await agent_service.get_all_active_agents(request)
        
        mock_agent_db_service.get_all_active_agents.assert_called_once_with(
            mock_user.user_id
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
        """Should update the entire agent."""
        agent_service.database_service = mock_agent_db_service
        
        updated_agent = sample_agent.model_copy(
            update={"agent_status": AgentStatus.inactive}
        )
        request = AgentCenterRequest(
            agent_id=sample_agent.agent_id,
            agent=updated_agent,
        )
        
        result = await agent_service.update_agent(request)
        
        assert result.success is True
        mock_agent_db_service.update_agent_by_agent_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_agent_card_only(
        self, agent_service, mock_agent_db_service, sample_agent, sample_agent_card
    ):
        """Should update only the agent card."""
        agent_service.database_service = mock_agent_db_service
        
        request = AgentCenterRequest(
            agent_id=sample_agent.agent_id,
            agent_card=sample_agent_card,
        )
        
        result = await agent_service.update_agent(request)
        
        assert result.success is True
        mock_agent_db_service.update_agent_agent_card_by_agent_id.assert_called_once()

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
        """Should remove agent successfully."""
        agent_service.database_service = mock_agent_db_service
        
        request = AgentCenterRequest(agent_id=sample_agent.agent_id)
        result = await agent_service.remove_agent(request)
        
        assert result.success is True
        mock_agent_db_service.delete_agent_by_agent_id.assert_called_once_with(
            sample_agent.agent_id
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
