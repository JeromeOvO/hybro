"""Tests for private summary token collection helpers."""

from unittest.mock import AsyncMock

import pytest

from common.utils.summary_streaming import stream_summary_to_sse


@pytest.mark.asyncio
async def test_stream_summary_to_sse_collects_tokens_without_public_delivery():
    sse = AsyncMock()
    private_token_sentinel = "__PRIVATE_SUMMARY_STREAM_TOKEN__"

    async def tokens():
        yield "Hello"
        yield private_token_sentinel
        yield " world"

    full = await stream_summary_to_sse(
        sse,
        room_id="room-1",
        message_id="summary-msg-1",
        agent_id="summary",
        token_stream=tokens(),
        client_request_id="req-1",
    )

    assert full == f"Hello{private_token_sentinel} world"
    sse.send_artifact_update.assert_not_awaited()
    assert private_token_sentinel not in repr(sse.mock_calls)
