"""Tests for summary SSE streaming helpers."""

from unittest.mock import AsyncMock

import pytest

from common.utils.summary_streaming import stream_summary_to_sse


@pytest.mark.asyncio
async def test_stream_summary_to_sse_emits_chunks_and_last_chunk():
    sse = AsyncMock()

    async def tokens():
        yield "Hello"
        yield " world"

    full = await stream_summary_to_sse(
        sse,
        room_id="room-1",
        message_id="summary-msg-1",
        agent_id="summary",
        token_stream=tokens(),
        client_request_id="req-1",
    )

    assert full == "Hello world"
    assert sse.send_artifact_update.await_count == 3
    last_call = sse.send_artifact_update.call_args_list[-1]
    assert last_call.kwargs["last_chunk"] is True
    assert last_call.kwargs["append"] is True
