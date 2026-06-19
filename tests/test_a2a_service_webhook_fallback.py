"""Tests for A2AService webhook_base_url / blocking fallback logic.

Covers send_message_to_tracked_agent:
1. webhook_base_url set → push_config built, blocking=False, short timeout
2. webhook_base_url empty → no push_config, blocking=True, long timeout
3. webhook_base_url with trailing slash → URL normalised correctly
4. Agent missing push capability → no push_config even when URL is set, blocking=True
5. blocking=True → DEFAULT_REQUEST_TIMEOUT used

Covers reply_to_task (HITL path):
6. webhook_base_url set + agent push-capable → push_config built, blocking=False
7. webhook_base_url empty → push_config=None, blocking=True
8. Agent missing push capability → push_config=None, blocking=True
9. Agent card not found in DB → push_config=None, blocking=True
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    Role,
    TextPart,
)

from app_shell.a2a_runtime import A2ARuntimeConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_card(push_capable: bool = True) -> AgentCard:
    """Minimal AgentCard for testing."""
    return AgentCard(
        name="Test Agent",
        description="A test agent",
        url="http://remote-agent:8080/",
        version="1.0",
        skills=[
            AgentSkill(
                id="test-skill",
                name="Test Skill",
                description="A test skill",
                tags=["test"],
            )
        ],
        capabilities=AgentCapabilities(
            streaming=False,
            pushNotifications=push_capable,
        ),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
    )


def _make_message() -> Message:
    return Message(
        role=Role.user,
        message_id="msg-test-001",
        parts=[TextPart(text="hello")],
    )


def _build_mock_response() -> MagicMock:
    from a2a.types import TaskState, TaskStatus

    mock_result = MagicMock()
    mock_result.kind = "task"
    mock_result.id = "task-001"
    mock_result.status = TaskStatus(state=TaskState.completed)
    mock_result.artifacts = []

    inner = MagicMock()
    inner.result = mock_result

    outer = MagicMock()
    outer.root = inner
    return outer


def _bind_webhook_base_url(service, value: str) -> None:
    service.bind_runtime_config(A2ARuntimeConfig(webhook_base_url=value))


def _task_facade_response() -> dict:
    return {
        "kind": "task",
        "result": {
            "kind": "task",
            "id": "task-001",
            "status": {"state": "completed"},
            "artifacts": [],
        },
        "error": None,
    }


def _message_facade_response() -> dict:
    return {
        "kind": "message",
        "result": {
            "kind": "message",
            "role": "agent",
            "messageId": "agent-msg-001",
            "parts": [{"kind": "text", "text": "Hello from agent"}],
        },
        "error": None,
    }


def _message_with_file_facade_response() -> dict:
    return {
        "kind": "message",
        "result": {
            "kind": "message",
            "role": "agent",
            "messageId": "agent-msg-file-001",
            "parts": [
                {
                    "kind": "file",
                    "file": {
                        "bytes": "aGVsbG8=",
                        "mimeType": "text/plain",
                        "name": "hello.txt",
                    },
                }
            ],
        },
        "error": None,
    }


def _terminal_task_with_file_facade_response() -> dict:
    return {
        "kind": "task",
        "result": {
            "kind": "task",
            "id": "task-file-001",
            "contextId": "ctx-task-file-001",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": "artifact-file-001",
                    "name": "file-result",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "bytes": "aGVsbG8=",
                                "mimeType": "text/plain",
                                "name": "hello.txt",
                            },
                        }
                    ],
                }
            ],
        },
        "error": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSendMessageTrackedAgentWebhookFallback:
    """Tests for the webhook / blocking decision in send_message_to_tracked_agent."""

    @pytest.mark.asyncio
    async def test_webhook_url_set_uses_push_notification(self):
        """When webhook_base_url is configured, push config is built and blocking=False."""
        from app_shell.a2a_runtime import A2AService

        captured_payload = {}

        async def fake_send_message(*args, **kwargs):
            captured_payload.update(kwargs)
            return _task_facade_response()

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)

        with (
            patch.object(
                service, "has_push_notification_capability", return_value=True
            ),
            patch("app_shell.a2a_runtime.adapter_send_message", fake_send_message),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            _bind_webhook_base_url(service, "https://api.example.com")

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=True),
                message=_make_message(),
                message_id="msg-001",
                webhook_token="tok-abc",
                context_id="ctx-001",
            )

        cfg = captured_payload
        assert cfg["push_notification_config"] is not None
        assert (
            "https://api.example.com/api/v1/webhooks/a2a/msg-001"
            == cfg["push_notification_config"]["url"]
        )
        assert cfg["blocking"] is False

    @pytest.mark.asyncio
    async def test_no_webhook_url_uses_blocking_true(self):
        """When webhook_base_url is empty, blocking=True and no push_config."""
        from app_shell.a2a_runtime import A2AService

        captured_payload = {}

        async def fake_send_message(*args, **kwargs):
            captured_payload.update(kwargs)
            return _task_facade_response()

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)

        with (
            patch.object(
                service, "has_push_notification_capability", return_value=True
            ),
            patch("app_shell.a2a_runtime.adapter_send_message", fake_send_message),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            _bind_webhook_base_url(service, "")

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=True),
                message=_make_message(),
                message_id="msg-002",
                webhook_token="tok-xyz",
                context_id="ctx-002",
            )

        cfg = captured_payload
        assert cfg["push_notification_config"] is None
        assert cfg["blocking"] is True

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped_from_webhook_url(self):
        """Trailing slash is stripped from webhook_base_url to avoid double-slash URLs."""
        from app_shell.a2a_runtime import A2AService

        captured_payload = {}

        async def fake_send_message(*args, **kwargs):
            captured_payload.update(kwargs)
            return _task_facade_response()

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)

        with (
            patch.object(
                service, "has_push_notification_capability", return_value=True
            ),
            patch("app_shell.a2a_runtime.adapter_send_message", fake_send_message),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            _bind_webhook_base_url(service, "https://api.example.com/")

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=True),
                message=_make_message(),
                message_id="msg-003",
                webhook_token="tok-def",
                context_id="ctx-003",
            )

        cfg = captured_payload
        assert cfg["push_notification_config"] is not None
        assert "//api/v1" not in cfg["push_notification_config"]["url"]
        assert (
            cfg["push_notification_config"]["url"]
            == "https://api.example.com/api/v1/webhooks/a2a/msg-003"
        )

    @pytest.mark.asyncio
    async def test_agent_without_push_capability_uses_blocking_true(self):
        """Agent without push-notification capability → blocking=True even if webhook URL is set."""
        from app_shell.a2a_runtime import A2AService

        captured_payload = {}

        async def fake_send_message(*args, **kwargs):
            captured_payload.update(kwargs)
            return _task_facade_response()

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)

        with (
            patch.object(
                service, "has_push_notification_capability", return_value=False
            ),
            patch("app_shell.a2a_runtime.adapter_send_message", fake_send_message),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            _bind_webhook_base_url(service, "https://api.example.com")

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=False),
                message=_make_message(),
                message_id="msg-004",
                webhook_token="tok-ghi",
                context_id="ctx-004",
            )

        cfg = captured_payload
        assert cfg["push_notification_config"] is None
        assert cfg["blocking"] is True

    @pytest.mark.asyncio
    async def test_timeout_is_long_when_blocking(self):
        """With blocking=True, the a2a_client is created with DEFAULT_REQUEST_TIMEOUT."""
        from app_shell.a2a_runtime import A2AService

        captured_timeout = {}

        async def fake_send_message(*args, **kwargs):
            captured_timeout["value"] = kwargs["timeout"]
            return _task_facade_response()

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)

        with (
            patch.object(
                service, "has_push_notification_capability", return_value=True
            ),
            patch("app_shell.a2a_runtime.adapter_send_message", fake_send_message),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            _bind_webhook_base_url(service, "")

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=True),
                message=_make_message(),
                message_id="msg-005",
                webhook_token="tok-jkl",
                context_id="ctx-005",
            )

        assert captured_timeout["value"] == A2ARuntimeConfig().default_request_timeout


# ---------------------------------------------------------------------------
# Helpers for reply_to_task (HITL) tests
# ---------------------------------------------------------------------------


def _make_room_agent_message(
    agent_url: str = "http://remote-agent:8080/", agent_id: str = "agent-001"
):
    """Minimal RoomAgentMessage-like object returned by db_service."""
    msg = MagicMock()
    msg.agent_url = agent_url
    msg.agent_id = agent_id
    return msg


def _make_agent_record(push_capable: bool = True):
    """Minimal Agent-like object with an agent_card."""
    record = MagicMock()
    record.agent_card = _make_agent_card(push_capable=push_capable)
    return record


class TestReplyToTaskWebhookFallback:
    """Tests for the webhook / blocking decision in reply_to_task (HITL path)."""

    @pytest.mark.asyncio
    async def test_hitl_webhook_url_and_capability_uses_push_notification(self):
        """HITL: webhook_base_url set + agent push-capable → push_config built, blocking=False."""
        from app_shell.a2a_runtime import A2AService

        captured_request = {}

        async def fake_send_hitl_reply(agent_url, message_data, **kwargs):
            captured_request["agent_url"] = agent_url
            captured_request["message_data"] = message_data
            captured_request.update(kwargs)
            return _task_facade_response()

        service = A2AService.__new__(A2AService)

        mock_db = MagicMock()
        mock_db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=_make_room_agent_message()
        )
        mock_db.generate_webhook_token = MagicMock(return_value="tok-hitl-001")
        mock_db.hash_webhook_token = MagicMock(return_value="hashed-tok")
        mock_db.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
        mock_db.get_agent_by_agent_id = AsyncMock(
            return_value=_make_agent_record(push_capable=True)
        )
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)
        _bind_webhook_base_url(service, "https://api.example.com")

        with (
            patch(
                "app_shell.a2a_runtime.adapter_send_hitl_reply",
                fake_send_hitl_reply,
            ),
            patch.dict("sys.modules", {}),
        ):
            await service.reply_to_task(
                message_id="msg-hitl-001",
                task_id="task-hitl-001",
                context_id="ctx-hitl-001",
                user_input="yes, proceed",
            )

        cfg = captured_request
        assert cfg["push_notification_config"] is not None
        assert (
            cfg["push_notification_config"]["url"]
            == "https://api.example.com/api/v1/webhooks/a2a/msg-hitl-001"
        )
        assert cfg["push_notification_config"]["token"] == "tok-hitl-001"
        assert cfg["blocking"] is False
        assert cfg["message_data"]["taskId"] == "task-hitl-001"
        assert cfg["message_data"]["referenceTaskIds"] == ["task-hitl-001"]

    @pytest.mark.asyncio
    async def test_hitl_no_webhook_url_uses_blocking_true(self):
        """HITL: webhook_base_url empty → push_config=None, blocking=True."""
        from app_shell.a2a_runtime import A2AService

        captured_request = {}

        service = A2AService.__new__(A2AService)

        mock_db = MagicMock()
        mock_db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=_make_room_agent_message()
        )
        mock_db.generate_webhook_token = MagicMock(return_value="tok-hitl-002")
        mock_db.hash_webhook_token = MagicMock(return_value="hashed-tok")
        mock_db.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
        mock_db.get_agent_by_agent_id = AsyncMock(
            return_value=_make_agent_record(push_capable=True)
        )
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)
        _bind_webhook_base_url(service, "")

        async def fake_send_hitl_reply(agent_url, message_data, **kwargs):
            captured_request["agent_url"] = agent_url
            captured_request["message_data"] = message_data
            captured_request.update(kwargs)
            return _task_facade_response()

        with (
            patch(
                "app_shell.a2a_runtime.adapter_send_hitl_reply",
                fake_send_hitl_reply,
            ),
        ):
            await service.reply_to_task(
                message_id="msg-hitl-002",
                task_id="task-hitl-002",
                context_id="ctx-hitl-002",
                user_input="no thanks",
            )

        cfg = captured_request
        assert cfg["push_notification_config"] is None
        assert cfg["blocking"] is True

    @pytest.mark.asyncio
    async def test_hitl_agent_without_push_capability_uses_blocking_true(self):
        """HITL: agent lacks push-notification capability → blocking=True even if webhook URL is set."""
        from app_shell.a2a_runtime import A2AService

        captured_request = {}

        service = A2AService.__new__(A2AService)

        mock_db = MagicMock()
        mock_db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=_make_room_agent_message()
        )
        mock_db.generate_webhook_token = MagicMock(return_value="tok-hitl-003")
        mock_db.hash_webhook_token = MagicMock(return_value="hashed-tok")
        mock_db.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
        mock_db.get_agent_by_agent_id = AsyncMock(
            return_value=_make_agent_record(push_capable=False)
        )
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)
        _bind_webhook_base_url(service, "https://api.example.com")

        async def fake_send_hitl_reply(agent_url, message_data, **kwargs):
            captured_request["agent_url"] = agent_url
            captured_request["message_data"] = message_data
            captured_request.update(kwargs)
            return _task_facade_response()

        with (
            patch(
                "app_shell.a2a_runtime.adapter_send_hitl_reply",
                fake_send_hitl_reply,
            ),
        ):
            await service.reply_to_task(
                message_id="msg-hitl-003",
                task_id="task-hitl-003",
                context_id="ctx-hitl-003",
                user_input="try again",
            )

        cfg = captured_request
        assert cfg["push_notification_config"] is None
        assert cfg["blocking"] is True

    @pytest.mark.asyncio
    async def test_hitl_agent_card_not_found_uses_blocking_true(self):
        """HITL: agent record not in DB → push_config=None, blocking=True."""
        from app_shell.a2a_runtime import A2AService

        captured_request = {}

        service = A2AService.__new__(A2AService)

        mock_db = MagicMock()
        mock_db.get_room_agent_message_by_message_id = AsyncMock(
            return_value=_make_room_agent_message()
        )
        mock_db.generate_webhook_token = MagicMock(return_value="tok-hitl-004")
        mock_db.hash_webhook_token = MagicMock(return_value="hashed-tok")
        mock_db.update_webhook_token_hash_on_message = AsyncMock(return_value=True)
        mock_db.get_agent_by_agent_id = AsyncMock(return_value=None)
        mock_db.update_task_on_message = AsyncMock()
        service.bind_task_db(mock_db)
        _bind_webhook_base_url(service, "https://api.example.com")

        async def fake_send_hitl_reply(agent_url, message_data, **kwargs):
            captured_request["agent_url"] = agent_url
            captured_request["message_data"] = message_data
            captured_request.update(kwargs)
            return _task_facade_response()

        with (
            patch(
                "app_shell.a2a_runtime.adapter_send_hitl_reply",
                fake_send_hitl_reply,
            ),
        ):
            await service.reply_to_task(
                message_id="msg-hitl-004",
                task_id="task-hitl-004",
                context_id="ctx-hitl-004",
                user_input="hello",
            )

        cfg = captured_request
        assert cfg["push_notification_config"] is None
        assert cfg["blocking"] is True


# ---------------------------------------------------------------------------
# Tests for persisted flag propagation
# ---------------------------------------------------------------------------


def _build_message_response() -> MagicMock:
    """Build a mock a2a response where result.kind == 'message'."""
    mock_result = MagicMock()
    mock_result.kind = "message"
    mock_result.parts = [TextPart(text="Hello from agent")]

    inner = MagicMock()
    inner.result = mock_result

    outer = MagicMock()
    outer.root = inner
    return outer


class TestSendMessageTrackedAgentPersistedFlag:
    """Tests that send_message_to_tracked_agent propagates the DB write result
    as a 'persisted' key in the response dict."""

    @pytest.mark.asyncio
    async def test_persisted_true_on_successful_db_write(self):
        """update_task_on_message returns True → response has persisted=True."""
        from app_shell.a2a_runtime import A2AService

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock(return_value=True)
        service.bind_task_db(mock_db)
        _bind_webhook_base_url(service, "")

        with (
            patch.object(
                service, "has_push_notification_capability", return_value=False
            ),
            patch(
                "app_shell.a2a_runtime.adapter_send_message",
                AsyncMock(return_value=_message_facade_response()),
            ),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            result = await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=False),
                message=_make_message(),
                message_id="msg-persist-001",
                webhook_token="tok-p1",
                context_id="ctx-p1",
            )

        assert result["type"] == "message"
        assert result["persisted"] is True

    @pytest.mark.asyncio
    async def test_persisted_false_on_failed_db_write(self):
        """update_task_on_message returns False → response has persisted=False."""
        from app_shell.a2a_runtime import A2AService

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock(return_value=False)
        service.bind_task_db(mock_db)
        _bind_webhook_base_url(service, "")

        with (
            patch.object(
                service, "has_push_notification_capability", return_value=False
            ),
            patch(
                "app_shell.a2a_runtime.adapter_send_message",
                AsyncMock(return_value=_message_facade_response()),
            ),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            result = await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=False),
                message=_make_message(),
                message_id="msg-persist-002",
                webhook_token="tok-p2",
                context_id="ctx-p2",
            )

        assert result["type"] == "message"
        assert result["persisted"] is False

    @pytest.mark.asyncio
    async def test_message_artifact_conversion_failure_still_persists_task(self):
        from app_shell.a2a_runtime import A2AService

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock(return_value=True)
        service.bind_task_db(mock_db)
        _bind_webhook_base_url(service, "")

        converter = AsyncMock(side_effect=RuntimeError("conversion failed"))
        with (
            patch.object(
                service, "has_push_notification_capability", return_value=False
            ),
            patch(
                "app_shell.a2a_runtime.adapter_send_message",
                AsyncMock(return_value=_message_with_file_facade_response()),
            ),
            patch(
                "execution.task_tracking.convert_pydantic_artifacts_to_s3",
                converter,
            ),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            result = await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=False),
                message=_make_message(),
                message_id="msg-convert-message-001",
                webhook_token="tok-convert-message",
                context_id="ctx-convert-message",
                room_id="room-convert",
            )

        converter.assert_awaited_once()
        mock_db.update_task_on_message.assert_awaited_once()
        assert result["type"] == "message"
        assert result["persisted"] is True

    @pytest.mark.asyncio
    async def test_terminal_task_artifact_conversion_failure_still_persists_task(self):
        from app_shell.a2a_runtime import A2AService

        service = A2AService.__new__(A2AService)
        mock_db = MagicMock()
        mock_db.update_task_on_message = AsyncMock(return_value=True)
        service.bind_task_db(mock_db)
        _bind_webhook_base_url(service, "")

        converter = AsyncMock(side_effect=RuntimeError("conversion failed"))
        with (
            patch.object(
                service, "has_push_notification_capability", return_value=False
            ),
            patch(
                "app_shell.a2a_runtime.adapter_send_message",
                AsyncMock(return_value=_terminal_task_with_file_facade_response()),
            ),
            patch(
                "execution.task_tracking.convert_pydantic_artifacts_to_s3",
                converter,
            ),
            patch.object(service, "_record_call", new_callable=AsyncMock),
        ):
            result = await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=False),
                message=_make_message(),
                message_id="msg-convert-task-001",
                webhook_token="tok-convert-task",
                context_id="ctx-convert-task",
                room_id="room-convert",
            )

        converter.assert_awaited_once()
        mock_db.update_task_on_message.assert_awaited_once()
        assert result["type"] == "message"
        assert result["persisted"] is True
