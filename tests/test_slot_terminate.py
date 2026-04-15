import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_slot_lifecycle():
    lifecycle = MagicMock()
    lifecycle.terminate_slot = AsyncMock()
    return lifecycle


class TestSlotTermination:
    """Verify AgentResponseHandler._terminate_slot emits slot_terminated at terminal points."""

    @pytest.mark.asyncio
    async def test_terminate_on_response(self, mock_slot_lifecycle):
        """slot_terminated(completed) should be emitted on successful response."""
        from modules.agent_response_handler import AgentResponseHandler
        from modules.agent_event import AgentEvent

        handler = AgentResponseHandler.__new__(AgentResponseHandler)
        handler._slot_lifecycle = mock_slot_lifecycle

        event = AgentEvent(
            kind="response",
            message_id="msg_123",
            room_id="room_1",
            agent_id="agent_1",
            turn_id="turn_1",
            text="Hello world",
            artifacts=[{"artifactId": "a1"}],
        )

        await handler._terminate_slot(
            event, "completed",
            content=event.text,
            artifacts=event.artifacts,
        )

        mock_slot_lifecycle.terminate_slot.assert_called_once()
        call_kw = mock_slot_lifecycle.terminate_slot.call_args.kwargs
        assert call_kw["room_id"] == "room_1"
        assert call_kw["turn_id"] == "turn_1"
        assert call_kw["slot_id"] == "msg_123"
        assert call_kw["status"] == "completed"
        assert call_kw["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_terminate_on_error(self, mock_slot_lifecycle):
        """slot_terminated(failed) should be emitted on error."""
        from modules.agent_response_handler import AgentResponseHandler
        from modules.agent_event import AgentEvent

        handler = AgentResponseHandler.__new__(AgentResponseHandler)
        handler._slot_lifecycle = mock_slot_lifecycle

        event = AgentEvent(
            kind="error",
            message_id="msg_123",
            room_id="room_1",
            agent_id="agent_1",
            turn_id="turn_1",
            text="Partial output",
        )

        await handler._terminate_slot(
            event, "failed",
            content=event.text,
            error="Agent timed out",
            has_partial_content=True,
        )

        mock_slot_lifecycle.terminate_slot.assert_called_once()
        call_kw = mock_slot_lifecycle.terminate_slot.call_args.kwargs
        assert call_kw["status"] == "failed"
        assert call_kw["error"] == "Agent timed out"
        assert call_kw["has_partial_content"] is True

    @pytest.mark.asyncio
    async def test_terminate_on_canceled(self, mock_slot_lifecycle):
        """slot_terminated(canceled) should be emitted on cancellation."""
        from modules.agent_response_handler import AgentResponseHandler
        from modules.agent_event import AgentEvent

        handler = AgentResponseHandler.__new__(AgentResponseHandler)
        handler._slot_lifecycle = mock_slot_lifecycle

        event = AgentEvent(
            kind="canceled",
            message_id="msg_123",
            room_id="room_1",
            agent_id="agent_1",
            turn_id="turn_1",
        )

        await handler._terminate_slot(event, "canceled")

        mock_slot_lifecycle.terminate_slot.assert_called_once()
        call_kw = mock_slot_lifecycle.terminate_slot.call_args.kwargs
        assert call_kw["status"] == "canceled"
        assert call_kw["slot_id"] == "msg_123"

    @pytest.mark.asyncio
    async def test_terminate_skipped_without_turn_id(self, mock_slot_lifecycle):
        """slot_terminated should be skipped when turn_id is missing."""
        from modules.agent_response_handler import AgentResponseHandler
        from modules.agent_event import AgentEvent

        handler = AgentResponseHandler.__new__(AgentResponseHandler)
        handler._slot_lifecycle = mock_slot_lifecycle

        event = AgentEvent(
            kind="response",
            message_id="msg_123",
            room_id="room_1",
            agent_id="agent_1",
            turn_id=None,  # no turn_id
        )

        await handler._terminate_slot(event, "completed")

        mock_slot_lifecycle.terminate_slot.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminate_skipped_without_slot_lifecycle(self):
        """slot_terminated should be skipped when slot_lifecycle is not set."""
        from modules.agent_response_handler import AgentResponseHandler
        from modules.agent_event import AgentEvent

        handler = AgentResponseHandler.__new__(AgentResponseHandler)
        handler._slot_lifecycle = None

        event = AgentEvent(
            kind="response",
            message_id="msg_123",
            room_id="room_1",
            agent_id="agent_1",
            turn_id="turn_1",
        )

        # Should not raise
        await handler._terminate_slot(event, "completed")
