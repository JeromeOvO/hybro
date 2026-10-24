"""C8 step 4b-3: preflight lives in conversation.preflight."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from conversation.preflight import MessagePreflight, persisted_execution_mode
from conversation.runtime import ConversationRuntime


def test_runtime_keeps_only_the_port_methods_for_preflight():
    import conversation.runtime as runtime_module

    for name in ("_prepare_orchestration_envelope", "_run_message_preflight_to_room"):
        assert not hasattr(ConversationRuntime, name), name
    assert not hasattr(runtime_module, "_persisted_execution_mode")
    assert hasattr(ConversationRuntime, "run_message_preflight_to_room")
    assert hasattr(ConversationRuntime, "discard_message_preflight")


@pytest.mark.parametrize(
    ("requested", "stored"),
    [("supervisor", "supervisor"), ("passthrough", "passthrough"), ("x", "direct")],
)
def test_persisted_execution_mode_defaults_to_direct(requested, stored):
    assert persisted_execution_mode({"execution_mode": requested}) == stored


@pytest.mark.asyncio
async def test_runtime_delegates_preflight_to_message_preflight(monkeypatch):
    sentinel = object()
    run = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(MessagePreflight, "run", run)

    assert await ConversationRuntime().run_message_preflight_to_room("ctx") is sentinel
    run.assert_awaited_once_with("ctx")
