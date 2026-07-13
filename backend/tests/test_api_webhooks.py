"""
Unit tests for A2A Webhook API endpoints.

Tests cover:
- Token validation (missing, invalid, task not found)
- StreamResponse parsing (task, statusUpdate, message, artifactUpdate, raw fallback)
- Idempotency (already-terminal tasks)
- WebhookTransport event normalization
- Error handling
"""

from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from fastapi import HTTPException

from api import webhooks
from common.protocols import WebhookReceiver
from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.response_handler import AgentResponseHandler
from execution.dispatch.transports.webhook import (
    WebhookTransport,
    parse_stream_response,
)

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
# Proto/v1.x statusUpdate fallback Tests
# =============================================================================


class TestParseStreamResponseProtoFallback:
    """Tests for v1.x proto-format statusUpdate payloads that fail strict validation.

    The a2a-sdk v1.x push sender wraps events in proto-JSON (camelCase, TASK_STATE_*
    enums, embedded Message with ROLE_AGENT and content[] instead of parts[]). These
    must parse successfully instead of returning HTTP 400.
    """

    def test_v1x_completed_extracts_text_from_message(self):
        """Terminal completed statusUpdate with embedded agent message."""
        payload = {
            "statusUpdate": {
                "taskId": "task-proto-1",
                "contextId": "ctx-proto-1",
                "status": {
                    "state": "TASK_STATE_COMPLETED",
                    "message": {
                        "role": "ROLE_AGENT",
                        "messageId": "m1",
                        "content": [{"text": "Hello from OpenClaw!"}],
                    },
                    "timestamp": "2026-05-30T00:53:17Z",
                },
                "final": True,
            }
        }
        result = parse_stream_response(payload, "msg-proto-1")
        assert result.status.state == TaskState.completed
        assert result.id == "task-proto-1"
        assert result.context_id == "ctx-proto-1"
        assert result.artifacts is not None
        assert len(result.artifacts) == 1
        text = result.artifacts[0].parts[0].root.text
        assert text == "Hello from OpenClaw!"

    def test_v1x_working_without_final_field(self):
        """Non-terminal working update with final omitted (proto drops false)."""
        payload = {
            "statusUpdate": {
                "taskId": "task-proto-2",
                "contextId": "ctx-proto-2",
                "status": {"state": "TASK_STATE_WORKING"},
            }
        }
        result = parse_stream_response(payload, "msg-proto-2")
        assert result.status.state == TaskState.working
        assert result.id == "task-proto-2"
        assert result.artifacts is None

    def test_v1x_failed_extracts_error_text(self):
        """Terminal failed statusUpdate with error message."""
        payload = {
            "statusUpdate": {
                "taskId": "task-proto-3",
                "contextId": "ctx-proto-3",
                "status": {
                    "state": "TASK_STATE_FAILED",
                    "message": {
                        "role": "ROLE_AGENT",
                        "content": [{"text": "Command timed out after 600s"}],
                    },
                },
                "final": True,
            }
        }
        result = parse_stream_response(payload, "msg-proto-3")
        assert result.status.state == TaskState.failed
        assert result.artifacts is not None
        assert result.artifacts[0].parts[0].root.text == "Command timed out after 600s"

    def test_v1x_completed_no_message(self):
        """Terminal completed without embedded message (no artifacts created)."""
        payload = {
            "statusUpdate": {
                "taskId": "task-proto-4",
                "contextId": "ctx-proto-4",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "final": True,
            }
        }
        result = parse_stream_response(payload, "msg-proto-4")
        assert result.status.state == TaskState.completed
        assert result.artifacts is None

    def test_v1x_uses_v0x_parts_key_for_text(self):
        """Fallback handles v0.x-style parts key in status.message."""
        payload = {
            "statusUpdate": {
                "taskId": "task-proto-5",
                "contextId": "ctx-proto-5",
                "status": {
                    "state": "TASK_STATE_COMPLETED",
                    "message": {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": "via parts key"}],
                    },
                },
            }
        }
        result = parse_stream_response(payload, "msg-proto-5")
        assert result.status.state == TaskState.completed
        assert result.artifacts[0].parts[0].root.text == "via parts key"

    def test_v1x_malformed_status_returns_working(self):
        """Completely malformed statusUpdate still returns a Task, never 400s."""
        payload = {
            "statusUpdate": {
                "taskId": "task-proto-6",
            }
        }
        result = parse_stream_response(payload, "msg-proto-6")
        assert result.status.state == TaskState.working
        assert result.id == "task-proto-6"

    def test_v1x_unknown_state_enum_defaults_to_working(self):
        """Unknown TASK_STATE_* enum value falls back gracefully."""
        payload = {
            "statusUpdate": {
                "taskId": "task-proto-7",
                "contextId": "ctx-7",
                "status": {"state": "TASK_STATE_UNKNOWN_FUTURE"},
            }
        }
        result = parse_stream_response(payload, "msg-proto-7")
        assert result.status.state == TaskState.working

    def test_v0x_status_update_still_uses_strict_path(self):
        """Genuine v0.x statusUpdate payloads still parse via strict validation."""
        payload = {
            "statusUpdate": {
                "taskId": "task-v0",
                "contextId": "ctx-v0",
                "status": {"state": "completed"},
                "final": True,
                "kind": "status-update",
            }
        }
        result = parse_stream_response(payload, "msg-v0")
        assert result.status.state == TaskState.completed
        assert result.id == "task-v0"
        assert result.artifacts is None


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

    @pytest.mark.asyncio
    async def test_route_rejects_non_object_json_payload(self):
        class FakeRequest:
            async def json(self):
                return ["not", "an", "object"]

        transport = MagicMock()
        transport.handle_webhook = AsyncMock(return_value={"status": "accepted"})

        with pytest.raises(HTTPException) as exc:
            await webhooks.handle_a2a_webhook(
                request=FakeRequest(),
                message_id="msg-001",
                authorization="Bearer bearer-token",
                x_a2a_notification_token="",
                transport=transport,
            )

        assert exc.value.status_code == 400
        transport.handle_webhook.assert_not_awaited()

    def test_webhook_transport_signature_matches_route_protocol(self):
        protocol_hints = get_type_hints(WebhookReceiver.handle_webhook)
        transport_hints = get_type_hints(WebhookTransport.handle_webhook)

        assert transport_hints["payload"] == protocol_hints["payload"]
        assert transport_hints["return"] == protocol_hints["return"]


def _make_webhook_transport(*, db=None, handler=None):
    if handler is None:
        handler = MagicMock(spec=AgentResponseHandler)
        handler.handle = AsyncMock()
    if db is None:
        db = MagicMock()
        db.verify_webhook_token_for_task = AsyncMock(return_value=(True, None))
        db.get_room_agent_message_by_message_id = AsyncMock(return_value=None)
        db.is_message_cancelled = AsyncMock(return_value=False)
    return WebhookTransport(
        response_handler=handler,
        webhook_auth=db,
        message_reader=db,
        cancellation_reader=db,
    )


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

    def test_working_task_does_not_surface_remote_status_artifacts_or_metadata(self):
        private_text = "PRIVATE_SENTINEL_webhook_working_status"
        private_bytes = "PRIVATE_SENTINEL_webhook_working_bytes"
        private_metadata = "PRIVATE_SENTINEL_webhook_working_metadata"
        wt = _make_webhook_transport()
        msg = _make_tracked_message()
        task = Task(
            id="task-001",
            context_id="ctx-001",
            status=TaskStatus(
                state=TaskState.working,
                message=Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=private_text))],
                    message_id="remote-status-message",
                    metadata={"private": private_metadata},
                ),
            ),
            artifacts=[
                Artifact(
                    artifact_id="raw-streaming-artifact",
                    metadata={"private": private_metadata},
                    parts=[
                        {
                            "kind": "file",
                            "file": {
                                "bytes": private_bytes,
                                "mimeType": "text/plain",
                                "name": "private.txt",
                            },
                        }
                    ],
                )
            ],
            history=[
                Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=private_text))],
                    message_id="remote-history",
                )
            ],
            metadata={"private": private_metadata},
        )

        event = wt._task_to_event(task, msg)

        assert event.kind == "status_update"
        assert event.text == ""
        assert event.parts is None
        assert event.artifacts is None
        assert private_text not in repr(event)
        assert private_bytes not in repr(event)
        assert private_metadata not in repr(event)

    def test_failed_task_uses_generic_error_not_remote_failure_payload(self):
        private_text = "PRIVATE_SENTINEL_webhook_failed_status"
        private_metadata = "PRIVATE_SENTINEL_webhook_failed_metadata"
        wt = _make_webhook_transport()
        msg = _make_tracked_message()
        task = Task(
            id="task-001",
            context_id="ctx-001",
            status=TaskStatus(
                state=TaskState.failed,
                message=Message(
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=private_text))],
                    message_id="remote-failure",
                    metadata={"private": private_metadata},
                ),
            ),
            metadata={"private": private_metadata},
        )

        event = wt._task_to_event(task, msg)

        assert event.kind == "error"
        assert event.error_text == "Task failed"
        assert private_text not in repr(event)
        assert private_metadata not in repr(event)
