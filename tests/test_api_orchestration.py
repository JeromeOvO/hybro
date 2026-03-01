"""
Unit tests for Orchestration Center API endpoints.

Tests cover:
- _get_task_request validation
- processRoomUserMessage validation and background task scheduling
- Task-based endpoints delegation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

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
# processRoomUserMessage Tests
# =============================================================================


class TestProcessRoomUserMessage:
    """Tests for process_room_user_message endpoint."""

    @pytest.mark.asyncio
    async def test_queues_background_task_on_valid_input(self, mock_user):
        """Should return 202 and queue background processing."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": "room-001",
            "room_user_message_id": "msg-001",
        })
        mock_bg = MagicMock()
        mock_rmc = MagicMock()

        with patch(PATCH["orchestration.room_message_center"], mock_rmc):
            result = await process_room_user_message(mock_request, mock_bg, mock_user)

        assert result.success is True
        assert result.status_code == 202
        assert result.room_id == "room-001"
        mock_bg.add_task.assert_called_once()
        call_args = mock_bg.add_task.call_args
        assert call_args[0][0] == mock_rmc.process_room_user_message

    @pytest.mark.asyncio
    async def test_returns_400_when_room_id_missing(self, mock_user):
        """Should return error when room_id is not provided."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_user_message_id": "msg-001",
        })
        mock_bg = MagicMock()

        result = await process_room_user_message(mock_request, mock_bg, mock_user)

        assert result.success is False
        assert result.status_code == 400
        assert "room" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_400_when_message_id_missing(self, mock_user):
        """Should return error when room_user_message_id is not provided."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": "room-001",
        })
        mock_bg = MagicMock()

        result = await process_room_user_message(mock_request, mock_bg, mock_user)

        assert result.success is False
        assert result.status_code == 400
        assert "message" in result.error.lower()

    @pytest.mark.asyncio
    async def test_passes_related_message_id(self, mock_user):
        """Should pass room_related_message_id to orchestration request."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "room_id": "room-001",
            "room_user_message_id": "msg-001",
            "room_related_message_id": "related-001",
        })
        mock_bg = MagicMock()
        mock_rmc = MagicMock()

        with patch(PATCH["orchestration.room_message_center"], mock_rmc):
            result = await process_room_user_message(mock_request, mock_bg, mock_user)

        assert result.success is True
        orch_req = mock_bg.add_task.call_args[0][1]
        assert orch_req.room_related_message_id == "related-001"
        assert orch_req.user_id == mock_user.user_id

    @pytest.mark.asyncio
    async def test_does_not_queue_on_validation_failure(self, mock_user):
        """Should not queue background task when validation fails."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={})
        mock_bg = MagicMock()

        result = await process_room_user_message(mock_request, mock_bg, mock_user)

        assert result.success is False
        mock_bg.add_task.assert_not_called()
