"""
Unit tests for api/inspection_center.py endpoints.

Tests cover:
- inspect_agent: validation, delegation to InspectionCenter
- inspect_a2a_connection: validation, delegation to InspectionCenter
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from api.inspection_center import inspect_agent, inspect_a2a_connection


class TestInspectAgent:
    @pytest.mark.asyncio
    async def test_rejects_missing_agent_url(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={})

        with pytest.raises(HTTPException) as exc:
            await inspect_agent(request)
        assert exc.value.status_code == 400
        assert "agent_url" in exc.value.detail

    @pytest.mark.asyncio
    async def test_delegates_to_inspection_center(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={"agent_url": "https://agent.example.com"})
        expected = {"name": "TestAgent", "status": "ok"}

        mock_ic = MagicMock()
        mock_ic.inspect_agent_card = AsyncMock(return_value=expected)
        result = await inspect_agent(request, center=mock_ic)

        assert result == expected
        mock_ic.inspect_agent_card.assert_called_once()
        call_arg = mock_ic.inspect_agent_card.call_args[0][0]
        assert call_arg.agent_url == "https://agent.example.com"


class TestInspectA2AConnection:
    @pytest.mark.asyncio
    async def test_rejects_missing_agent_url(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={})

        with pytest.raises(HTTPException) as exc:
            await inspect_a2a_connection(request)
        assert exc.value.status_code == 400
        assert "agent_url" in exc.value.detail

    @pytest.mark.asyncio
    async def test_delegates_to_inspection_center(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={"agent_url": "https://agent.example.com"})
        expected = {"connected": True}

        mock_ic = MagicMock()
        mock_ic.inspect_a2a_connection = AsyncMock(return_value=expected)
        result = await inspect_a2a_connection(request, center=mock_ic)

        assert result == expected
        mock_ic.inspect_a2a_connection.assert_called_once()
