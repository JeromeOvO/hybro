"""
Unit tests for Orchestration Center API endpoints.

Tests cover:
- _get_task_request validation
- processRoomUserMessage returns HTTP 410 (deprecated)
- Task-based endpoints delegation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.orchestration_center import (
    _get_task_request,
    process_room_user_message,
    decompose_task,
)
from models.response import OrchestrationResponse
from tests.conftest import PATCH


# =============================================================================
# _get_task_request Tests
# =============================================================================


class TestGetTaskRequest:
    """Tests for _get_task_request helper."""

    @pytest.mark.asyncio
    async def test_parses_valid_request(self, mock_user):
        """Should parse task_id and attach user_id."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"task_id": "task-001"})

        result = await _get_task_request(mock_request, mock_user)

        assert result.task_id == "task-001"
        assert result.user_id == mock_user.user_id

    @pytest.mark.asyncio
    async def test_raises_400_when_task_id_missing(self, mock_user):
        """Should raise 400 when task_id is not provided."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={})

        with pytest.raises(HTTPException) as exc:
            await _get_task_request(mock_request, mock_user)

        assert exc.value.status_code == 400
        assert "task_id" in exc.value.detail


# =============================================================================
# processRoomUserMessage Tests (deprecated — returns HTTP 410)
# =============================================================================


class TestProcessRoomUserMessage:
    """Tests for process_room_user_message endpoint (deprecated, returns 410)."""

    @pytest.mark.asyncio
    async def test_returns_410_gone(self, mock_user):
        """Deprecated endpoint should return HTTP 410 Gone."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": "room-001",
            "room_user_message_id": "msg-001",
        })
        mock_bg = MagicMock()

        result = await process_room_user_message(mock_request, mock_bg, mock_user)

        assert result.status_code == 410
        body = result.body
        import json
        data = json.loads(body)
        assert data["success"] is False
        assert "deprecated" in data["error"].lower()
