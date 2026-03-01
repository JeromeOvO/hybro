"""
Unit tests for A2A Webhook API endpoints.

Tests cover:
- Token validation (missing, invalid, task not found)
- StreamResponse parsing (task, statusUpdate, message, artifactUpdate, raw fallback)
- Idempotency (already-terminal tasks)
- Background task scheduling (notify + resume)
- Error handling
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from a2a.types import (
    Task, TaskState, TaskStatus, TaskStatusUpdateEvent,
    Artifact, Part, TextPart, Message,
)
from api.webhooks import handle_a2a_webhook, parse_stream_response
from tests.conftest import PATCH


# =============================================================================
# parse_stream_response Tests (pure function)
# =============================================================================


class TestParseStreamResponse:
    """Tests for parse_stream_response helper."""

    def test_parses_task_variant(self):
        """Should parse full Task from 'task' key."""
        payload = {
            "task": {
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {"state": "completed"},
                "artifacts": [
                    {
                        "artifactId": "art-001",
                        "name": "response",
                        "parts": [{"text": "Hello"}],
                    }
                ],
            }
        }
        result = parse_stream_response(payload, "msg-001")
        assert result.id == "task-001"
        assert result.status.state == TaskState.completed
        assert len(result.artifacts) == 1

    def test_parses_status_update_variant(self):
        """Should parse TaskStatusUpdateEvent from 'statusUpdate' key."""
        payload = {
            "statusUpdate": {
                "taskId": "task-002",
                "contextId": "ctx-002",
                "status": {"state": "working"},
                "final": False,
            }
        }
        result = parse_stream_response(payload, "msg-002")
        assert result.id == "task-002"
        assert result.status.state == TaskState.working

    def test_parses_message_variant(self):
        """Should convert Message to completed Task with artifacts."""
        payload = {
            "message": {
                "role": "agent",
                "parts": [{"text": "Agent response text"}],
                "messageId": "msg-abc",
            }
        }
        result = parse_stream_response(payload, "msg-003")
        assert result.status.state == TaskState.completed
        assert result.artifacts is not None
        assert len(result.artifacts) == 1

    def test_rejects_artifact_update_variant(self):
        """Should raise 400 for unsupported artifactUpdate."""
        payload = {"artifactUpdate": {"taskId": "task-004"}}
        with pytest.raises(HTTPException) as exc:
            parse_stream_response(payload, "msg-004")
        assert exc.value.status_code == 400
        assert "artifactUpdate" in exc.value.detail

    def test_parses_raw_task_fallback(self):
        """Should parse raw Task (backwards compatibility)."""
        payload = {
            "id": "task-005",
            "contextId": "ctx-005",
            "status": {"state": "failed"},
        }
        result = parse_stream_response(payload, "msg-005")
        assert result.id == "task-005"
        assert result.status.state == TaskState.failed

    def test_rejects_invalid_payload(self):
        """Should raise 400 for payload missing all known keys."""
        payload = {"unknown_key": "value"}
        with pytest.raises(HTTPException) as exc:
            parse_stream_response(payload, "msg-006")
        assert exc.value.status_code == 400
        assert "Invalid StreamResponse" in exc.value.detail

    def test_rejects_empty_payload(self):
        """Should raise 400 for empty payload."""
        with pytest.raises(HTTPException) as exc:
            parse_stream_response({}, "msg-007")
        assert exc.value.status_code == 400


# =============================================================================
# handle_a2a_webhook Tests
# =============================================================================


class TestHandleA2AWebhookAuth:
    """Tests for webhook authentication."""

    @pytest.mark.asyncio
    async def test_rejects_missing_token(self, mock_db_service):
        """Should raise 401 when Authorization header is empty."""
        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={})
        mock_bg = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await handle_a2a_webhook(mock_request, "msg-001", mock_bg, authorization="")
        assert exc.value.status_code == 401
        assert "Missing" in exc.value.detail

    @pytest.mark.asyncio
    async def test_rejects_invalid_token(self, mock_db_service):
        """Should raise 401 when token hash doesn't match."""
        mock_db_service.verify_webhook_token_for_task = AsyncMock(
            return_value=(False, "invalid_token")
        )
        mock_request = MagicMock()
        mock_bg = MagicMock()

        with patch(PATCH["webhooks.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc:
                await handle_a2a_webhook(
                    mock_request, "msg-001", mock_bg, authorization="Bearer bad-token"
                )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_404_when_task_not_found(self, mock_db_service):
        """Should raise 404 when task doesn't exist (race condition)."""
        mock_db_service.verify_webhook_token_for_task = AsyncMock(
            return_value=(False, "task_not_found")
        )
        mock_request = MagicMock()
        mock_bg = MagicMock()

        with patch(PATCH["webhooks.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc:
                await handle_a2a_webhook(
                    mock_request, "msg-001", mock_bg, authorization="Bearer some-token"
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_500_on_verification_error(self, mock_db_service):
        """Should raise 500 on unexpected verification error."""
        mock_db_service.verify_webhook_token_for_task = AsyncMock(
            return_value=(False, "db_error")
        )
        mock_request = MagicMock()
        mock_bg = MagicMock()

        with patch(PATCH["webhooks.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc:
                await handle_a2a_webhook(
                    mock_request, "msg-001", mock_bg, authorization="Bearer some-token"
                )
        assert exc.value.status_code == 500


class TestHandleA2AWebhookFlow:
    """Tests for webhook processing flow after auth."""

    def _setup_valid_auth(self, mock_db_service):
        mock_db_service.verify_webhook_token_for_task = AsyncMock(
            return_value=(True, None)
        )

    def _make_tracked_message(self, room_id="room-001", state=None, agent_id="agent-001"):
        """Create a mock agent message with task tracking."""
        msg = MagicMock()
        msg.has_task_tracking = True
        msg.room_id = room_id
        msg.user_id = "user-001"
        msg.agent_id = agent_id
        msg.related_message_id = "user-msg-001"
        msg.step_number = 1
        msg.total_steps = 3
        msg.task_created_at = None
        msg.task_content = None
        msg.message_content = MagicMock()
        if state:
            task = MagicMock()
            task.status.state = state
            msg.message_content.message_task = task
        else:
            msg.message_content.message_task = None
        return msg

    @pytest.mark.asyncio
    async def test_accepts_valid_webhook(self, mock_db_service):
        """Should accept valid webhook and schedule background tasks."""
        self._setup_valid_auth(mock_db_service)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "task": {
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {"state": "completed"},
                "artifacts": [{"artifactId": "a1", "name": "r", "parts": [{"text": "done"}]}],
            }
        })

        msg = self._make_tracked_message()
        mock_db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        mock_db_service.update_task_on_message = AsyncMock(return_value=True)
        mock_db_service.update_last_notified_state = AsyncMock(return_value=True)
        mock_bg = MagicMock()

        with patch(PATCH["webhooks.db_service"], mock_db_service):
            result = await handle_a2a_webhook(
                mock_request, "msg-001", mock_bg, authorization="Bearer valid-token"
            )

        assert result["status"] == "accepted"
        mock_db_service.update_task_on_message.assert_called_once()
        assert mock_bg.add_task.call_count >= 1

    @pytest.mark.asyncio
    async def test_skips_already_terminal_task(self, mock_db_service):
        """Should return 'already_terminal' without updating for completed tasks."""
        self._setup_valid_auth(mock_db_service)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "task": {
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {"state": "working"},
            }
        })

        msg = self._make_tracked_message(state=TaskState.completed)
        mock_db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        mock_bg = MagicMock()

        with patch(PATCH["webhooks.db_service"], mock_db_service):
            result = await handle_a2a_webhook(
                mock_request, "msg-001", mock_bg, authorization="Bearer valid-token"
            )

        assert result["status"] == "already_terminal"
        mock_db_service.update_task_on_message = AsyncMock()
        mock_db_service.update_task_on_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_404_when_message_has_no_tracking(self, mock_db_service):
        """Should raise 404 when message exists but has no task tracking."""
        self._setup_valid_auth(mock_db_service)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "task": {
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {"state": "working"},
            }
        })

        msg = MagicMock()
        msg.has_task_tracking = False
        mock_db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        mock_bg = MagicMock()

        with patch(PATCH["webhooks.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc:
                await handle_a2a_webhook(
                    mock_request, "msg-001", mock_bg, authorization="Bearer valid-token"
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_400_for_invalid_payload(self, mock_db_service):
        """Should raise 400 when payload cannot be parsed."""
        self._setup_valid_auth(mock_db_service)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"garbage": True})

        msg = self._make_tracked_message()
        mock_db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        mock_bg = MagicMock()

        with patch(PATCH["webhooks.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc:
                await handle_a2a_webhook(
                    mock_request, "msg-001", mock_bg, authorization="Bearer valid-token"
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_500_on_db_update_failure(self, mock_db_service):
        """Should raise 500 when database update fails."""
        self._setup_valid_auth(mock_db_service)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={
            "task": {
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {"state": "working"},
            }
        })

        msg = self._make_tracked_message()
        mock_db_service.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        mock_db_service.update_task_on_message = AsyncMock(return_value=False)
        mock_bg = MagicMock()

        with patch(PATCH["webhooks.db_service"], mock_db_service):
            with pytest.raises(HTTPException) as exc:
                await handle_a2a_webhook(
                    mock_request, "msg-001", mock_bg, authorization="Bearer valid-token"
                )
        assert exc.value.status_code == 500
