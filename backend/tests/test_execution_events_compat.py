from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from common.dto import ProcessingStatusEvent
from execution.events import emit_processing_status


@pytest.mark.asyncio
async def test_nonterminal_preflight_emits_only_processing_compatibility_status() -> (
    None
):
    publisher = SimpleNamespace(emit=AsyncMock())
    resolver = SimpleNamespace(
        resolve_client_request_id=AsyncMock(return_value="request-1")
    )

    await emit_processing_status(
        conversation_id="room-1",
        status="processing",
        message_id="message-1",
        event_publisher=publisher,
        client_request_id_resolver=resolver,
        client_request_id="request-1",
    )

    publisher.emit.assert_awaited_once()
    emitted = publisher.emit.await_args.args[0]
    assert isinstance(emitted, ProcessingStatusEvent)
