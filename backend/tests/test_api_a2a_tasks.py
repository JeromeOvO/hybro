"""
Unit tests for A2A Tasks API endpoints.

Tests cover:
- Getting task status by message ID
- Listing tasks for a room
- Listing pending tasks for a user
- Authorization checks
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from api_gateway.routes.a2a_task_routes import (
    get_task_status,
    list_room_tasks,
    list_user_pending_tasks,
)
from common.types import (
    Artifact,
    FileContent,
    FilePart,
    Message,
    MessageRole,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from models.room import MessageContent, RoomAgentMessage

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

        result = await get_task_status(
            sample_agent_message_with_task.message_id, mock_user, db=mock_db_service
        )

        assert result["message_id"] == sample_agent_message_with_task.message_id
        assert "status" in result
        assert "task" in result

    @pytest.mark.asyncio
    async def test_get_task_status_projects_public_task_before_serialization(
        self, mock_user, mock_db_service
    ):
        """Should not serialize raw remote task fields through the status endpoint."""
        private_sentinel = "PRIVATE_SENTINEL_get_task_status_raw_task"
        raw_task = Task(
            id="remote-task-1",
            context_id="remote-context-1",
            status=TaskStatus(
                state=TaskState.input_required,
                message=Message(
                    role=MessageRole.AGENT,
                    message_id="private-status",
                    parts=[Part(root=TextPart(text=private_sentinel))],
                    metadata={"private": private_sentinel},
                ),
            ),
            history=[
                Message(
                    role=MessageRole.USER,
                    message_id="private-user-history",
                    parts=[Part(root=TextPart(text=private_sentinel))],
                ),
                Message(
                    role=MessageRole.AGENT,
                    message_id="private-agent-history",
                    parts=[Part(root=TextPart(text=private_sentinel))],
                ),
            ],
            artifacts=[
                Artifact(
                    artifact_id="private-artifact",
                    parts=[Part(root=TextPart(text=private_sentinel))],
                    metadata={"private": private_sentinel},
                )
            ],
            metadata={
                "prompt": private_sentinel,
                "hitl_prompt": private_sentinel,
                "hitl_request_id": private_sentinel,
            },
        )
        message = RoomAgentMessage(
            room_id="room-1",
            message_id="agent-message-1",
            user_id=mock_user.user_id,
            agent_id="agent-1",
            message_content=MessageContent(message_task=raw_task),
            has_task_tracking=True,
        )
        mock_db_service.get_room_agent_message_by_message_id.return_value = message

        result = await get_task_status(
            message.message_id, mock_user, db=mock_db_service
        )

        task = result["task"]
        assert task["status"]["state"] == "input-required"
        assert task["status"]["message"] is None
        assert task.get("history") in (None, [])
        assert task.get("artifacts") in (None, [])
        assert task.get("metadata") is None
        assert private_sentinel not in str(result)

    @pytest.mark.asyncio
    async def test_get_task_status_drops_completed_inline_file_bytes(
        self, mock_user, mock_db_service
    ):
        private_bytes = "PRIVATE_SENTINEL_api_inline_file_bytes"
        task = Task(
            id="remote-task-1",
            context_id="remote-context-1",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id="artifact-1",
                    name="file-result",
                    parts=[
                        Part(
                            root=FilePart(
                                file=FileContent(
                                    bytes=private_bytes,
                                    mimeType="text/plain",
                                    name="result.txt",
                                )
                            )
                        )
                    ],
                )
            ],
        )
        message = RoomAgentMessage(
            room_id="room-1",
            message_id="agent-message-1",
            user_id=mock_user.user_id,
            agent_id="agent-1",
            message_content=MessageContent(message_task=task),
            has_task_tracking=True,
        )
        mock_db_service.get_room_agent_message_by_message_id.return_value = message

        result = await get_task_status(
            message.message_id, mock_user, db=mock_db_service
        )

        assert result["task"]["artifacts"][0]["parts"] == []
        assert private_bytes not in str(result)

    @pytest.mark.asyncio
    async def test_raises_404_when_message_not_found(self, mock_user, mock_db_service):
        """Should raise 404 when message doesn't exist."""
        mock_db_service.get_room_agent_message_by_message_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_task_status("nonexistent-message", mock_user, db=mock_db_service)

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

        with pytest.raises(HTTPException) as exc_info:
            await get_task_status(
                sample_agent_message.message_id, mock_user, db=mock_db_service
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_when_not_owner(
        self, mock_user_2, mock_db_service, sample_agent_message_with_task
    ):
        """Should raise 403 when user doesn't own the task."""
        mock_db_service.get_room_agent_message_by_message_id.return_value = (
            sample_agent_message_with_task
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_task_status(
                sample_agent_message_with_task.message_id,
                mock_user_2,
                db=mock_db_service,
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

        result = await list_room_tasks(
            sample_room.room_id, limit=50, current_user=mock_user, db=mock_db_service
        )

        assert "tasks" in result
        assert len(result["tasks"]) == 1
        assert (
            result["tasks"][0]["message_id"]
            == sample_agent_message_with_task.message_id
        )

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

        result = await list_room_tasks(
            sample_room.room_id, limit=50, current_user=mock_user, db=mock_db_service
        )

        # Should be empty since the task belongs to another user
        assert result["tasks"] == []

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(
        self, mock_user, mock_db_service, sample_room
    ):
        """Should pass limit parameter to database query."""
        mock_db_service.get_task_messages_for_room.return_value = []

        await list_room_tasks(
            sample_room.room_id, limit=25, current_user=mock_user, db=mock_db_service
        )

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

        result = await list_user_pending_tasks(mock_user, db=mock_db_service)

        assert "tasks" in result
        assert len(result["tasks"]) == 1

    @pytest.mark.asyncio
    async def test_queries_with_non_terminal_states(self, mock_user, mock_db_service):
        """Should query for non-terminal task states."""
        mock_db_service.get_pending_task_messages_for_user.return_value = []

        await list_user_pending_tasks(mock_user, db=mock_db_service)

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

        result = await list_user_pending_tasks(mock_user, db=mock_db_service)

        assert result["tasks"] == []
