"""
Unit tests for Discovery API endpoints.

Tests cover:
- API key authentication
- Rate limit enforcement
- Successful agent discovery
- No agents found (404)
- Internal errors (500)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.discovery import discover_agents, DiscoveryRequest
from common.errors import PlatformRouteError
from models.api_key import APIKey
from models.response import DiscoveryResponse
from tests.conftest import PATCH, FROZEN_TIME


# =============================================================================
# Discovery Endpoint Tests
# =============================================================================


class TestDiscoverAgents:
    """Tests for discover_agents endpoint."""

    @pytest.fixture
    def sample_api_key(self) -> APIKey:
        return APIKey(
            key_id="key-001",
            key_hash="abc123hash",
            user_id="user-001",
            name="Test Key",
            created_at=FROZEN_TIME,
        )

    @pytest.mark.asyncio
    async def test_returns_matching_agents(self, sample_api_key):
        """Should return agents matching the query."""
        expected = DiscoveryResponse(
            query="data analysis",
            agents=[],
            count=0,
        )
        mock_discovery = MagicMock()
        mock_discovery.discover_agents = AsyncMock(return_value=expected)
        mock_rate_limit = MagicMock()
        mock_rate_limit.check_rate_limit = AsyncMock()
        mock_rate_limit.record_request = AsyncMock()

        request_body = DiscoveryRequest(query="data analysis", limit=5)

        result = await discover_agents(
            request_body,
            sample_api_key,
            svc=mock_discovery,
            rate_limiter=mock_rate_limit,
            default_limit=10,
        )

        assert result.query == "data analysis"
        mock_rate_limit.check_rate_limit.assert_called_once_with(sample_api_key)
        mock_rate_limit.record_request.assert_called_once_with(sample_api_key)

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_agents_found(self, sample_api_key):
        """Should return 200 with empty agents list when no agents match."""
        expected = DiscoveryResponse(
            query="obscure topic",
            agents=[],
            count=0,
        )
        mock_discovery = MagicMock()
        mock_discovery.discover_agents = AsyncMock(return_value=expected)
        mock_rate_limit = MagicMock()
        mock_rate_limit.check_rate_limit = AsyncMock()
        mock_rate_limit.record_request = AsyncMock()

        request_body = DiscoveryRequest(query="obscure topic")

        result = await discover_agents(
            request_body,
            sample_api_key,
            svc=mock_discovery,
            rate_limiter=mock_rate_limit,
            default_limit=10,
        )

        assert result.query == "obscure topic"
        assert result.agents == []
        assert result.count == 0

    @pytest.mark.asyncio
    async def test_returns_500_on_internal_error(self, sample_api_key):
        """Should raise 500 on unexpected errors."""
        mock_discovery = MagicMock()
        mock_discovery.discover_agents = AsyncMock(
            side_effect=RuntimeError("Pinecone connection failed")
        )
        mock_rate_limit = MagicMock()
        mock_rate_limit.check_rate_limit = AsyncMock()

        request_body = DiscoveryRequest(query="test")

        with pytest.raises(HTTPException) as exc:
            await discover_agents(
                request_body,
                sample_api_key,
                svc=mock_discovery,
                rate_limiter=mock_rate_limit,
                default_limit=10,
            )

        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_propagates_rate_limit_error(self, sample_api_key):
        """Should propagate HTTPException from rate limiter."""
        mock_rate_limit = MagicMock()
        mock_rate_limit.check_rate_limit = AsyncMock(
            side_effect=HTTPException(status_code=429, detail="Rate limit exceeded")
        )

        request_body = DiscoveryRequest(query="test")

        with pytest.raises(HTTPException) as exc:
            await discover_agents(
                request_body,
                sample_api_key,
                rate_limiter=mock_rate_limit,
                default_limit=10,
            )

        assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_maps_platform_rate_limit_error(self, sample_api_key):
        """Should map common Platform rate-limit errors to HTTPException."""
        mock_rate_limit = MagicMock()
        mock_rate_limit.check_rate_limit = AsyncMock(
            side_effect=PlatformRouteError(
                429,
                {
                    "error": "rate_limit_exceeded",
                    "message": "Rate limit exceeded",
                    "retry_after": 60,
                },
            )
        )

        request_body = DiscoveryRequest(query="test")

        with pytest.raises(HTTPException) as exc:
            await discover_agents(
                request_body,
                sample_api_key,
                rate_limiter=mock_rate_limit,
                default_limit=10,
            )

        assert exc.value.status_code == 429
        assert exc.value.headers == {"Retry-After": "60"}
        assert exc.value.detail == {
            "error": "rate_limit_exceeded",
            "message": "Rate limit exceeded",
        }
