"""Tests for A2AService webhook_base_url / blocking fallback logic.

Covers:
1. webhook_base_url set → push_config built, blocking=False, short timeout
2. webhook_base_url empty → no push_config, blocking=True, long timeout
3. webhook_base_url with trailing slash → URL normalised correctly
4. Agent missing push capability → no push_config even when URL is set, blocking=True
5. HITL path: webhook_base_url empty → push_config=None, blocking=True, long timeout
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    Message,
    MessageSendConfiguration,
    Role,
    TextPart,
)


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
    from a2a.types import Task, TaskState, TaskStatus

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSendMessageTrackedAgentWebhookFallback:
    """Tests for the webhook / blocking decision in send_message_to_tracked_agent."""

    @pytest.mark.asyncio
    async def test_webhook_url_set_uses_push_notification(self):
        """When webhook_base_url is configured, push config is built and blocking=False."""
        from services.a2a_service import A2AService
        import services.database_service as db_module

        captured_payload = {}

        @asynccontextmanager
        async def fake_client_ctx(*args, **kwargs):
            fake_client = AsyncMock()

            async def fake_send(request):
                captured_payload["params"] = request.params
                return _build_mock_response()

            fake_client.send_message = fake_send
            yield fake_client

        service = A2AService.__new__(A2AService)

        with (
            patch.object(service, "has_push_notification_capability", return_value=True),
            patch.object(service, "create_a2a_client", fake_client_ctx),
            patch.object(service, "_resolve_accepted_modes", return_value=["text/plain"]),
            patch.object(service, "_record_call", new_callable=AsyncMock),
            patch("services.a2a_service.settings") as mock_settings,
            patch.object(db_module, "db_service") as mock_db,
        ):
            mock_settings.webhook_base_url = "https://api.example.com"
            mock_db.update_task_on_message = AsyncMock()

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=True),
                message=_make_message(),
                message_id="msg-001",
                webhook_token="tok-abc",
                context_id="ctx-001",
            )

        cfg: MessageSendConfiguration = captured_payload["params"].configuration
        assert cfg.push_notification_config is not None
        assert "https://api.example.com/api/v1/webhooks/a2a/msg-001" == cfg.push_notification_config.url
        assert cfg.blocking is False

    @pytest.mark.asyncio
    async def test_no_webhook_url_uses_blocking_true(self):
        """When webhook_base_url is empty, blocking=True and no push_config."""
        from services.a2a_service import A2AService
        import services.database_service as db_module

        captured_payload = {}

        @asynccontextmanager
        async def fake_client_ctx(*args, **kwargs):
            fake_client = AsyncMock()

            async def fake_send(request):
                captured_payload["params"] = request.params
                return _build_mock_response()

            fake_client.send_message = fake_send
            yield fake_client

        service = A2AService.__new__(A2AService)

        with (
            patch.object(service, "has_push_notification_capability", return_value=True),
            patch.object(service, "create_a2a_client", fake_client_ctx),
            patch.object(service, "_resolve_accepted_modes", return_value=["text/plain"]),
            patch.object(service, "_record_call", new_callable=AsyncMock),
            patch("services.a2a_service.settings") as mock_settings,
            patch.object(db_module, "db_service") as mock_db,
        ):
            mock_settings.webhook_base_url = ""
            mock_db.update_task_on_message = AsyncMock()

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=True),
                message=_make_message(),
                message_id="msg-002",
                webhook_token="tok-xyz",
                context_id="ctx-002",
            )

        cfg: MessageSendConfiguration = captured_payload["params"].configuration
        assert cfg.push_notification_config is None
        assert cfg.blocking is True

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped_from_webhook_url(self):
        """Trailing slash is stripped from webhook_base_url to avoid double-slash URLs."""
        from services.a2a_service import A2AService
        import services.database_service as db_module

        captured_payload = {}

        @asynccontextmanager
        async def fake_client_ctx(*args, **kwargs):
            fake_client = AsyncMock()

            async def fake_send(request):
                captured_payload["params"] = request.params
                return _build_mock_response()

            fake_client.send_message = fake_send
            yield fake_client

        service = A2AService.__new__(A2AService)

        with (
            patch.object(service, "has_push_notification_capability", return_value=True),
            patch.object(service, "create_a2a_client", fake_client_ctx),
            patch.object(service, "_resolve_accepted_modes", return_value=["text/plain"]),
            patch.object(service, "_record_call", new_callable=AsyncMock),
            patch("services.a2a_service.settings") as mock_settings,
            patch.object(db_module, "db_service") as mock_db,
        ):
            mock_settings.webhook_base_url = "https://api.example.com/"
            mock_db.update_task_on_message = AsyncMock()

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=True),
                message=_make_message(),
                message_id="msg-003",
                webhook_token="tok-def",
                context_id="ctx-003",
            )

        cfg: MessageSendConfiguration = captured_payload["params"].configuration
        assert cfg.push_notification_config is not None
        assert "//api/v1" not in cfg.push_notification_config.url
        assert cfg.push_notification_config.url == "https://api.example.com/api/v1/webhooks/a2a/msg-003"

    @pytest.mark.asyncio
    async def test_agent_without_push_capability_uses_blocking_true(self):
        """Agent without push-notification capability → blocking=True even if webhook URL is set."""
        from services.a2a_service import A2AService
        import services.database_service as db_module

        captured_payload = {}

        @asynccontextmanager
        async def fake_client_ctx(*args, **kwargs):
            fake_client = AsyncMock()

            async def fake_send(request):
                captured_payload["params"] = request.params
                return _build_mock_response()

            fake_client.send_message = fake_send
            yield fake_client

        service = A2AService.__new__(A2AService)

        with (
            patch.object(service, "has_push_notification_capability", return_value=False),
            patch.object(service, "create_a2a_client", fake_client_ctx),
            patch.object(service, "_resolve_accepted_modes", return_value=["text/plain"]),
            patch.object(service, "_record_call", new_callable=AsyncMock),
            patch("services.a2a_service.settings") as mock_settings,
            patch.object(db_module, "db_service") as mock_db,
        ):
            mock_settings.webhook_base_url = "https://api.example.com"
            mock_db.update_task_on_message = AsyncMock()

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=False),
                message=_make_message(),
                message_id="msg-004",
                webhook_token="tok-ghi",
                context_id="ctx-004",
            )

        cfg: MessageSendConfiguration = captured_payload["params"].configuration
        assert cfg.push_notification_config is None
        assert cfg.blocking is True

    @pytest.mark.asyncio
    async def test_timeout_is_long_when_blocking(self):
        """With blocking=True, the a2a_client is created with DEFAULT_REQUEST_TIMEOUT."""
        from services.a2a_service import A2AService
        import services.database_service as db_module

        captured_timeout = {}

        @asynccontextmanager
        async def fake_client_ctx(agent_card, timeout=None):
            captured_timeout["value"] = timeout
            fake_client = AsyncMock()
            fake_client.send_message = AsyncMock(return_value=_build_mock_response())
            yield fake_client

        service = A2AService.__new__(A2AService)

        with (
            patch.object(service, "has_push_notification_capability", return_value=True),
            patch.object(service, "create_a2a_client", fake_client_ctx),
            patch.object(service, "_resolve_accepted_modes", return_value=["text/plain"]),
            patch.object(service, "_record_call", new_callable=AsyncMock),
            patch("services.a2a_service.settings") as mock_settings,
            patch.object(db_module, "db_service") as mock_db,
        ):
            mock_settings.webhook_base_url = ""
            mock_db.update_task_on_message = AsyncMock()

            await service.send_message_to_tracked_agent(
                agent_card=_make_agent_card(push_capable=True),
                message=_make_message(),
                message_id="msg-005",
                webhook_token="tok-jkl",
                context_id="ctx-005",
            )

        assert captured_timeout["value"] == A2AService.DEFAULT_REQUEST_TIMEOUT

