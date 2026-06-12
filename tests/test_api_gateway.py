
"""
Unit tests for Gateway API endpoints.

Tests cover:
- API key authentication
- Rate limit enforcement
- Discover, send, stream, and card endpoints
- Access control (public/private agents)
- URL masking
- Error handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    Message,
    Role,
    TextPart,
)
from fastapi import HTTPException
from starlette.concurrency import iterate_in_threadpool

from api.gateway import (
    gateway_discover,
    gateway_get_card,
    gateway_send,
    gateway_stream,
)
from common.dto import GatewayResponse, InternalAgentMessage
from common.errors import GatewayPlatformError, PlatformRouteError
from common.types import MessageRole
from models.api_key import APIKey
from models.gateway import (
    GatewayCardResponse,
    GatewayDiscoverRequest,
    GatewayDiscoveryResponse,
    GatewaySendRequest,
)
from tests.conftest import FROZEN_TIME, PATCH

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_api_key() -> APIKey:
    return APIKey(
        key_id="key-001",
        key_hash="abc123hash",
        user_id="user-001",
        name="Test Key",
        created_at=FROZEN_TIME,
    )


@pytest.fixture
def sample_message() -> Message:
    return Message(
        role=Role.user,
        parts=[TextPart(text="Hello agent")],
        messageId="msg-001",
    )


def _mock_rate_limit():
    rl = MagicMock()
    rl.check_rate_limit = AsyncMock()
    rl.record_request = AsyncMock()
    return rl


def _mock_gateway_service():
    svc = MagicMock()
    svc.discover_agents = AsyncMock()
    svc.send_message = AsyncMock()
    svc.prepare_stream = AsyncMock()
    svc.get_agent_card = AsyncMock()
    return svc


# =============================================================================
# Endpoint Tests
# =============================================================================


class TestGatewayDiscover:
    @pytest.mark.asyncio
    async def test_returns_discovery_results(self, sample_api_key):
        expected = GatewayDiscoveryResponse(
            query="data analysis", agents=[], count=0
        )
        mock_svc = _mock_gateway_service()
        mock_svc.discover_agents = AsyncMock(return_value=expected)
        mock_rl = _mock_rate_limit()

        body = GatewayDiscoverRequest(query="data analysis", limit=5)

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            result = await gateway_discover(body, sample_api_key, mock_svc)

        assert result.query == "data analysis"
        assert result.count == 0
        mock_rl.check_rate_limit.assert_called_once_with(sample_api_key)
        mock_rl.record_request.assert_called_once_with(sample_api_key)

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_agents_found(self, sample_api_key):
        expected = GatewayDiscoveryResponse(
            query="obscure topic", agents=[], count=0
        )
        mock_svc = _mock_gateway_service()
        mock_svc.discover_agents = AsyncMock(return_value=expected)
        mock_rl = _mock_rate_limit()
        body = GatewayDiscoverRequest(query="obscure topic")

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            result = await gateway_discover(body, sample_api_key, mock_svc)

        assert result.query == "obscure topic"
        assert result.agents == []
        assert result.count == 0

    @pytest.mark.asyncio
    async def test_returns_502_on_infra_error(self, sample_api_key):
        mock_svc = _mock_gateway_service()
        mock_svc.discover_agents = AsyncMock(
            side_effect=RuntimeError("Pinecone connection failed")
        )
        mock_rl = _mock_rate_limit()
        body = GatewayDiscoverRequest(query="test")

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            with pytest.raises(HTTPException) as exc:
                await gateway_discover(body, sample_api_key, mock_svc)
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_rate_limit_retry_after_stays_in_header(self, sample_api_key):
        mock_svc = _mock_gateway_service()
        mock_rl = _mock_rate_limit()
        mock_rl.check_rate_limit = AsyncMock(
            side_effect=PlatformRouteError(
                429,
                {
                    "error": "rate_limit_exceeded",
                    "message": "Rate limit exceeded",
                    "retry_after": 60,
                },
            )
        )
        body = GatewayDiscoverRequest(query="test")

        with pytest.raises(HTTPException) as exc:
            await gateway_discover(body, sample_api_key, mock_svc, mock_rl)

        assert exc.value.status_code == 429
        assert exc.value.headers == {"Retry-After": "60"}
        assert exc.value.detail == {
            "error": "rate_limit_exceeded",
            "message": "Rate limit exceeded",
        }


class TestGatewaySend:
    @pytest.mark.asyncio
    async def test_sends_message(self, sample_api_key, sample_message):
        mock_response = GatewayResponse(status_code=200, payload={"ok": True})
        mock_svc = _mock_gateway_service()
        mock_svc.send_message = AsyncMock(return_value=mock_response)
        mock_rl = _mock_rate_limit()

        body = GatewaySendRequest(message=sample_message)

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            result = await gateway_send("agent-001", body, sample_api_key, mock_svc)

        assert result == {"ok": True}
        mock_svc.send_message.assert_called_once_with(
            agent_id="agent-001",
            message=InternalAgentMessage(
                agent_id="agent-001",
                role=MessageRole.USER,
                parts=[{"kind": "text", "text": "Hello agent"}],
                metadata={},
            ),
            user_id="user-001",
        )

    @pytest.mark.asyncio
    async def test_send_agent_not_found(self, sample_api_key, sample_message):
        mock_svc = _mock_gateway_service()
        mock_svc.send_message = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Not found")
        )
        mock_rl = _mock_rate_limit()
        body = GatewaySendRequest(message=sample_message)

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            with pytest.raises(HTTPException) as exc:
                await gateway_send("bad-agent", body, sample_api_key, mock_svc)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_agent_rate_limit_retry_after_stays_in_header(
        self, sample_api_key, sample_message
    ):
        mock_svc = _mock_gateway_service()
        mock_svc.send_message = AsyncMock(
            side_effect=GatewayPlatformError(
                429,
                {
                    "error": "rate_limit_exceeded",
                    "message": "Rate limit exceeded",
                    "retry_after": 60,
                },
            )
        )
        mock_rl = _mock_rate_limit()
        body = GatewaySendRequest(message=sample_message)

        with pytest.raises(HTTPException) as exc:
            await gateway_send("agent-001", body, sample_api_key, mock_svc, mock_rl)

        assert exc.value.status_code == 429
        assert exc.value.headers == {"Retry-After": "60"}
        assert exc.value.detail == {
            "error": "rate_limit_exceeded",
            "message": "Rate limit exceeded",
        }


class TestGatewayGetCard:
    @pytest.mark.asyncio
    async def test_returns_masked_card(self, sample_api_key):
        masked_card = {"name": "Test", "url": "https://gateway/agents/agent-001/message/send"}
        mock_svc = _mock_gateway_service()
        mock_svc.get_agent_card = AsyncMock(
            return_value=GatewayResponse(status_code=200, payload=masked_card)
        )
        mock_rl = _mock_rate_limit()

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            result = await gateway_get_card("agent-001", sample_api_key, mock_svc)

        assert isinstance(result, GatewayCardResponse)
        assert result.agent_id == "agent-001"
        assert result.agent_card["url"].endswith("/message/send")


class TestGatewayStream:
    @pytest.mark.asyncio
    async def test_returns_streaming_response(self, sample_api_key, sample_message):
        mock_event = MagicMock()
        mock_event.model_dump_json.return_value = '{"result": "ok"}'

        async def _fake_gen():
            yield mock_event

        mock_svc = _mock_gateway_service()
        mock_svc.prepare_stream = AsyncMock(return_value=_fake_gen())
        mock_rl = _mock_rate_limit()

        body = GatewaySendRequest(message=sample_message)

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            result = await gateway_stream("agent-001", body, sample_api_key, mock_svc)

        assert result.media_type == "text/event-stream"
        mock_svc.prepare_stream.assert_called_once_with(
            agent_id="agent-001",
            message=InternalAgentMessage(
                agent_id="agent-001",
                role=MessageRole.USER,
                parts=[{"kind": "text", "text": "Hello agent"}],
                metadata={},
            ),
            user_id="user-001",
        )

    @pytest.mark.asyncio
    async def test_stream_agent_not_found_returns_http_error(self, sample_api_key, sample_message):
        mock_svc = _mock_gateway_service()
        mock_svc.prepare_stream = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Not found")
        )
        mock_rl = _mock_rate_limit()
        body = GatewaySendRequest(message=sample_message)

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            with pytest.raises(HTTPException) as exc:
                await gateway_stream("bad-agent", body, sample_api_key, mock_svc)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_access_denied_returns_http_error(self, sample_api_key, sample_message):
        mock_svc = _mock_gateway_service()
        mock_svc.prepare_stream = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="Access denied")
        )
        mock_rl = _mock_rate_limit()
        body = GatewaySendRequest(message=sample_message)

        with patch(PATCH["gateway.gateway_rate_limit_service"], mock_rl):
            with pytest.raises(HTTPException) as exc:
                await gateway_stream("private-agent", body, sample_api_key, mock_svc)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_stream_preflight_gateway_error_returns_http_error(
        self, sample_api_key, sample_message
    ):
        mock_svc = _mock_gateway_service()
        mock_svc.prepare_stream = AsyncMock(
            side_effect=GatewayPlatformError(
                502,
                {
                    "error": "hub_agent_not_directly_callable",
                    "message": "Hub agent cannot be streamed directly",
                },
            )
        )
        mock_rl = _mock_rate_limit()
        body = GatewaySendRequest(message=sample_message)

        with pytest.raises(HTTPException) as exc:
            await gateway_stream(
                "hub-agent",
                body,
                sample_api_key,
                mock_svc,
                mock_rl,
            )

        assert exc.value.status_code == 502
        assert exc.value.detail["error"] == "hub_agent_not_directly_callable"

    @pytest.mark.asyncio
    async def test_stream_records_request_after_sync_iterable(
        self, sample_api_key, sample_message
    ):
        mock_svc = _mock_gateway_service()
        mock_svc.prepare_stream = AsyncMock(return_value=iterate_in_threadpool([{"ok": True}]))
        mock_rl = _mock_rate_limit()
        body = GatewaySendRequest(message=sample_message)

        result = await gateway_stream("agent-001", body, sample_api_key, mock_svc, mock_rl)
        chunks = [chunk async for chunk in result.body_iterator]

        assert chunks == ['data: {"ok": true}\n\n']
        mock_rl.record_request.assert_awaited_once_with(sample_api_key)

    @pytest.mark.asyncio
    async def test_stream_late_gateway_error_yields_jsonrpc_error(
        self, sample_api_key, sample_message
    ):
        async def _failing_stream():
            raise GatewayPlatformError(
                502,
                {
                    "error": "agent_error",
                    "message": "boom",
                },
            )
            yield

        mock_svc = _mock_gateway_service()
        mock_svc.prepare_stream = AsyncMock(return_value=_failing_stream())
        mock_rl = _mock_rate_limit()
        body = GatewaySendRequest(message=sample_message)

        result = await gateway_stream("agent-001", body, sample_api_key, mock_svc, mock_rl)
        chunks = [chunk async for chunk in result.body_iterator]

        assert chunks == [
            'data: {"jsonrpc": "2.0", "id": "", "error": {"code": 502, '
            '"message": "boom", "data": {"error": "agent_error", "message": "boom"}}}\n\n'
        ]
        mock_rl.record_request.assert_awaited_once_with(sample_api_key)

    @pytest.mark.asyncio
    async def test_stream_late_gateway_error_uses_error_fallback_message(
        self, sample_api_key, sample_message
    ):
        async def _failing_stream():
            raise GatewayPlatformError(502, {"error": "agent_error"})
            yield

        mock_svc = _mock_gateway_service()
        mock_svc.prepare_stream = AsyncMock(return_value=_failing_stream())
        mock_rl = _mock_rate_limit()
        body = GatewaySendRequest(message=sample_message)

        result = await gateway_stream("agent-001", body, sample_api_key, mock_svc, mock_rl)
        chunks = [chunk async for chunk in result.body_iterator]

        assert '"message": "agent_error"' in chunks[0]
        assert '"message": "None"' not in chunks[0]
