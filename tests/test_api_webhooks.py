"""
Unit tests for A2A Webhook API endpoints.

Tests cover:
- Token validation (missing, invalid, task not found)
- StreamResponse parsing (task, statusUpdate, message, artifactUpdate, raw fallback)
- Idempotency (already-terminal tasks)
- WebhookTransport event normalization
- Error handling
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from a2a.types import (
    Task, TaskState, TaskStatus, TaskStatusUpdateEvent,
    Artifact, Part, TextPart, Message,
)
from modules.agent_event import AgentEvent
from modules.agent_response_handler import AgentResponseHandler
from modules.transports.webhook import WebhookTransport, parse_stream_response
from api import webhooks


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

    def test_parses_artifact_update_variant(self):
        """Should parse artifactUpdate as working Task with artifact."""
        payload = {
            "artifactUpdate": {
                "taskId": "task-004",
                "contextId": "ctx-004",
                "artifact": {
                    "artifactId": "art-004",
                    "name": "streamed",
                    "parts": [{"text": "chunk"}],
                },
            }
        }
        result = parse_stream_response(payload, "msg-004")
        assert result.status.state == TaskState.working
        assert len(result.artifacts) == 1

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
# WebhookTransport Tests
# =============================================================================


class TestWebhookRouteAdapter:
    """Tests for the thin FastAPI webhook route wrapper."""

    @pytest.mark.asyncio
    async def test_route_uses_injected_transport_and_notification_token(self):
        class FakeRequest:
            async def json(self):
                return {"task": {"id": "task-001"}}

        transport = MagicMock()
        transport.handle_webhook = AsyncMock(return_value={"status": "accepted"})

        result = await webhooks.handle_a2a_webhook(
            request=FakeRequest(),
            message_id="msg-001",
            authorization="Bearer bearer-token",
            x_a2a_notification_token="header-token",
            transport=transport,
        )

        assert result == {"status": "accepted"}
        transport.handle_webhook.assert_awaited_once_with(
            "msg-001",
            {"task": {"id": "task-001"}},
            "header-token",
        )


def _make_webhook_transport(*, db=None, handler=None):
    if handler is None:
        handler = MagicMock(spec=AgentResponseHandler)
        handler.handle = AsyncMock()
    if db is None:
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(True, None))
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
    return WebhookTransport(response_handler=handler, db=db)


def _make_tracked_message(room_id="room-001", state=None, agent_id="agent-001"):
    msg = MagicMock()
    msg.has_task_tracking = True
    msg.room_id = room_id
    msg.user_id = "user-001"
    msg.agent_id = agent_id
    msg.message_id = "msg-001"
    msg.related_message_id = "user-msg-001"
    msg.message_content = MagicMock()
    if state:
        task = MagicMock()
        task.status.state = state
        msg.message_content.message_task = task
    else:
        msg.message_content.message_task = None
    return msg


class TestWebhookTransportAuth:
    """Tests for webhook authentication via WebhookTransport."""

    @pytest.mark.asyncio
    async def test_rejects_missing_token(self):
        wt = _make_webhook_transport()
        with pytest.raises(HTTPException) as exc:
            await wt.handle_webhook("msg-001", {}, "")
        assert exc.value.status_code == 401
        assert "Missing" in exc.value.detail

    @pytest.mark.asyncio
    async def test_rejects_invalid_token(self):
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(False, "invalid_token"))
        wt = _make_webhook_transport(db=db)
        with pytest.raises(HTTPException) as exc:
            await wt.handle_webhook("msg-001", {}, "bad-token")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_404_when_task_not_found(self):
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(False, "task_not_found"))
        wt = _make_webhook_transport(db=db)
        with pytest.raises(HTTPException) as exc:
            await wt.handle_webhook("msg-001", {}, "some-token")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_500_on_verification_error(self):
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(False, "db_error"))
        wt = _make_webhook_transport(db=db)
        with pytest.raises(HTTPException) as exc:
            await wt.handle_webhook("msg-001", {}, "some-token")
        assert exc.value.status_code == 500


class TestWebhookTransportFlow:
    """Tests for webhook processing flow after auth."""

    @pytest.mark.asyncio
    async def test_accepts_valid_webhook(self):
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(True, None))
        msg = _make_tracked_message()
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        db.is_message_cancelled = AsyncMock(return_value=False)

        wt = _make_webhook_transport(db=db)
        payload = {
            "task": {
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {"state": "completed"},
                "artifacts": [{"artifactId": "a1", "name": "r", "parts": [{"text": "done"}]}],
            }
        }
        result = await wt.handle_webhook("msg-001", payload, "valid-token")
        assert result["status"] == "accepted"
        wt.response_handler.handle.assert_awaited_once()
        event = wt.response_handler.handle.call_args[0][0]
        assert isinstance(event, AgentEvent)
        assert event.kind == "response"

    @pytest.mark.asyncio
    async def test_skips_already_terminal_task(self):
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(True, None))
        msg = _make_tracked_message(state=TaskState.completed)
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)
        db.is_message_cancelled = AsyncMock(return_value=False)

        wt = _make_webhook_transport(db=db)
        payload = {
            "task": {
                "id": "task-001",
                "contextId": "ctx-001",
                "status": {"state": "working"},
            }
        }
        result = await wt.handle_webhook("msg-001", payload, "valid-token")
        assert result["status"] == "already_terminal"
        wt.response_handler.handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_404_when_no_tracking(self):
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(True, None))
        msg = MagicMock()
        msg.has_task_tracking = False
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)

        wt = _make_webhook_transport(db=db)
        payload = {
            "task": {"id": "t-1", "contextId": "c-1", "status": {"state": "working"}}
        }
        with pytest.raises(HTTPException) as exc:
            await wt.handle_webhook("msg-001", payload, "valid-token")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_400_for_invalid_payload(self):
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(True, None))
        msg = _make_tracked_message()
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=msg)

        wt = _make_webhook_transport(db=db)
        with pytest.raises(HTTPException) as exc:
            await wt.handle_webhook("msg-001", {"garbage": True}, "valid-token")
        assert exc.value.status_code == 400


class TestWebhookTransportNormalize:
    """Tests for _task_to_event normalization."""

    def _make_task(self, state="completed", artifacts=None):
        return Task(
            id="task-001",
            context_id="ctx-001",
            status=TaskStatus(state=TaskState(state)),
            artifacts=artifacts,
        )

    def test_completed_task(self):
        wt = _make_webhook_transport()
        msg = _make_tracked_message()
        task = self._make_task("completed", [
            Artifact(artifact_id="a1", name="r", parts=[TextPart(text="done")])
        ])
        event = wt._task_to_event(task, msg)
        assert event.kind == "response"
        assert event.text == "done"

    def test_failed_task(self):
        wt = _make_webhook_transport()
        msg = _make_tracked_message()
        task = self._make_task("failed")
        event = wt._task_to_event(task, msg)
        assert event.kind == "error"
        assert event.state == "failed"

    def test_canceled_task(self):
        wt = _make_webhook_transport()
        msg = _make_tracked_message()
        task = self._make_task("canceled")
        event = wt._task_to_event(task, msg)
        assert event.kind == "canceled"

    def test_interactive_task(self):
        wt = _make_webhook_transport()
        msg = _make_tracked_message()
        task = self._make_task("input-required")
        event = wt._task_to_event(task, msg)
        assert event.kind == "interactive"
        assert event.state == "input-required"

    def test_working_task(self):
        wt = _make_webhook_transport()
        msg = _make_tracked_message()
        task = self._make_task("working")
        event = wt._task_to_event(task, msg)
        assert event.kind == "status_update"
