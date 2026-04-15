import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_appender():
    appender = MagicMock()
    appender.append = AsyncMock(return_value=MagicMock())
    return appender


class TestSlotDelta:
    """Verify DirectTransport emits slot_delta during streaming."""

    @pytest.mark.asyncio
    async def test_slot_delta_emitted_during_streaming(self, mock_appender):
        """When _handle_stream_message_chunk processes text,
        it must call appender.append('slot_delta', persist=False)."""
        from modules.transports.direct import DirectTransport, MessageStreamingState
        from models.processing import ProcessingContext

        transport = DirectTransport.__new__(DirectTransport)
        transport._turn_appender = mock_appender
        transport.sse_manager = MagicMock(
            send_artifact_update=AsyncMock(),
        )
        transport.tsm = MagicMock(persist_message=AsyncMock())

        # Build a mock processing context
        current_msg = MagicMock()
        current_msg.message_id = "msg_123"
        current_msg.agent_id = "agent_1"
        current_msg.turn_id = "turn_1"

        ctx = MagicMock(spec=ProcessingContext)
        ctx.room_id = "room_1"
        ctx.current_message = current_msg
        ctx.send_sse = True

        streaming_state = MessageStreamingState()

        # Build a mock streaming result with text parts
        mock_part = MagicMock()
        mock_part.kind = "text"
        mock_part.text = "Hello "
        mock_result = MagicMock()
        mock_result.parts = [mock_part]
        mock_result.role = "agent"
        mock_result.message_id = "msg_123"

        mock_extracted = MagicMock()
        mock_extracted.text = "Hello "
        mock_extracted.has_non_text = False
        mock_extracted.file_parts = []
        mock_extracted.data_parts = []

        # extract_parts is imported inside the method body, so patch at the source module
        with patch("common.utils.a2a_helpers.extract_parts", return_value=mock_extracted):
            with patch("modules.transports.direct.get_task", return_value=None):
                await transport._handle_stream_message_chunk(
                    mock_result, ctx, streaming_state
                )

        # Assert appender was called with slot_delta
        mock_appender.append.assert_called_once()
        call_args = mock_appender.append.call_args
        assert call_args.args[0] == "room_1"
        assert call_args.args[1] == "turn_1"
        assert call_args.args[2] == "slot_delta"
        assert call_args.args[3]["slot_id"] == "msg_123"
        assert call_args.args[3]["text_delta"] == "Hello "
        assert call_args.kwargs.get("persist") is False

    @pytest.mark.asyncio
    async def test_slot_delta_skipped_without_turn_id(self, mock_appender):
        """slot_delta should not be emitted for messages without turn_id."""
        from modules.transports.direct import DirectTransport, MessageStreamingState
        from models.processing import ProcessingContext

        transport = DirectTransport.__new__(DirectTransport)
        transport._turn_appender = mock_appender
        transport.sse_manager = MagicMock(
            send_artifact_update=AsyncMock(),
        )
        transport.tsm = MagicMock(persist_message=AsyncMock())

        current_msg = MagicMock()
        current_msg.message_id = "msg_old"
        current_msg.agent_id = "agent_1"
        current_msg.turn_id = None  # no turn_id

        ctx = MagicMock(spec=ProcessingContext)
        ctx.room_id = "room_1"
        ctx.current_message = current_msg
        ctx.send_sse = True

        streaming_state = MessageStreamingState()

        mock_part = MagicMock()
        mock_part.kind = "text"
        mock_part.text = "Hello "
        mock_result = MagicMock()
        mock_result.parts = [mock_part]
        mock_result.role = "agent"
        mock_result.message_id = "msg_old"

        mock_extracted = MagicMock()
        mock_extracted.text = "Hello "
        mock_extracted.has_non_text = False
        mock_extracted.file_parts = []
        mock_extracted.data_parts = []

        with patch("common.utils.a2a_helpers.extract_parts", return_value=mock_extracted):
            with patch("modules.transports.direct.get_task", return_value=None):
                await transport._handle_stream_message_chunk(
                    mock_result, ctx, streaming_state
                )

        # Appender should NOT have been called
        mock_appender.append.assert_not_called()
