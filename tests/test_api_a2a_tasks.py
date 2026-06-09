"""
Unit tests for A2A Tasks API endpoints.

Tests cover:
- Getting task status by message ID
- Listing tasks for a room
- Listing pending tasks for a user
- Authorization checks
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from api.a2a_tasks import (
    get_task_status,
    list_room_tasks,
    list_user_pending_tasks,
)
from tests.conftest import PATCH

# =============================================================================
# Get Task Status Tests
# =============================================================================


class TestGetTaskStatus:
    """Tests for get_task_status endpoint."""

    @pytest.mark.asyncio
    async def test_returns_task_status(
        self, mock_user, mock_db_service, sample_agent_message_with_task
    ):
        """Should return task status for valid message."""
        mock_db_service.get_room_agent_message_by_message_id.return_value = (
            sample_agent_message_with_task
        )
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            result = await get_task_status(
                sample_agent_message_with_task.message_id, mock_user
            )
        
        assert result["message_id"] == sample_agent_message_with_task.message_id
        assert "status" in result
        assert "task" in result

    @pytest.mark.asyncio
    async def test_raises_404_when_message_not_found(
        self, mock_user, mock_db_service
    ):
        """Should raise 404 when message doesn't exist."""
        mock_db_service.get_room_agent_message_by_message_id.return_value = None
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await get_task_status("nonexistent-message", mock_user)
        
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_no_task_tracking(
        self, mock_user, mock_db_service, sample_agent_message
    ):
        """Should raise 404 when message has no task tracking."""
        # sample_agent_message has has_task_tracking=False by default
        mock_db_service.get_room_agent_message_by_message_id.return_value = (
            sample_agent_message
        )
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await get_task_status(sample_agent_message.message_id, mock_user)
        
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_when_not_owner(
        self, mock_user_2, mock_db_service, sample_agent_message_with_task
    ):
        """Should raise 403 when user doesn't own the task."""
        mock_db_service.get_room_agent_message_by_message_id.return_value = (
            sample_agent_message_with_task
        )
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            with pytest.raises(HTTPException) as exc_info:
                await get_task_status(
                    sample_agent_message_with_task.message_id, mock_user_2
                )
        
        assert exc_info.value.status_code == 403


# =============================================================================
# List Room Tasks Tests
# =============================================================================


class TestListRoomTasks:
    """Tests for list_room_tasks endpoint."""

    @pytest.mark.asyncio
    async def test_returns_tasks_for_room(
        self, mock_user, mock_db_service, sample_room, sample_agent_message_with_task
    ):
        """Should return tasks for the specified room."""
        mock_db_service.get_task_messages_for_room.return_value = [
            sample_agent_message_with_task
        ]
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            result = await list_room_tasks(sample_room.room_id, limit=50, current_user=mock_user)
        
        assert "tasks" in result
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["message_id"] == sample_agent_message_with_task.message_id

    @pytest.mark.asyncio
    async def test_filters_tasks_by_user(
        self, mock_user, mock_user_2, mock_db_service, sample_room
    ):
        """Should only return tasks owned by the current user."""
        # Create a task owned by mock_user_2
        other_user_task = MagicMock()
        other_user_task.user_id = mock_user_2.user_id
        other_user_task.message_id = "other-user-task"
        
        mock_db_service.get_task_messages_for_room.return_value = [other_user_task]
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            result = await list_room_tasks(sample_room.room_id, limit=50, current_user=mock_user)
        
        # Should be empty since the task belongs to another user
        assert result["tasks"] == []

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(
        self, mock_user, mock_db_service, sample_room
    ):
        """Should pass limit parameter to database query."""
        mock_db_service.get_task_messages_for_room.return_value = []
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            await list_room_tasks(sample_room.room_id, limit=25, current_user=mock_user)
        
        mock_db_service.get_task_messages_for_room.assert_called_once_with(
            sample_room.room_id, limit=25
        )


# =============================================================================
# List User Pending Tasks Tests
# =============================================================================


class TestListUserPendingTasks:
    """Tests for list_user_pending_tasks endpoint."""

    @pytest.mark.asyncio
    async def test_returns_pending_tasks_for_user(
        self, mock_user, mock_db_service, sample_agent_message_with_task
    ):
        """Should return pending tasks for the current user."""
        mock_db_service.get_pending_task_messages_for_user.return_value = [
            sample_agent_message_with_task
        ]
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            result = await list_user_pending_tasks(mock_user)
        
        assert "tasks" in result
        assert len(result["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_queries_with_non_terminal_states(
        self, mock_user, mock_db_service
    ):
        """Should query for non-terminal task states."""
        mock_db_service.get_pending_task_messages_for_user.return_value = []
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            await list_user_pending_tasks(mock_user)
        
        # Verify the call was made with user_id and state values
        call_args = mock_db_service.get_pending_task_messages_for_user.call_args
        assert call_args[0][0] == mock_user.user_id
        # Second argument should be list of non-terminal state values
        assert isinstance(call_args[0][1], list)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_pending_tasks(
        self, mock_user, mock_db_service
    ):
        """Should return empty list when user has no pending tasks."""
        mock_db_service.get_pending_task_messages_for_user.return_value = []
        
        with patch(PATCH["a2a_tasks.task_store"], mock_db_service):
            result = await list_user_pending_tasks(mock_user)
        
        assert result["tasks"] == []
