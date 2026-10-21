"""A2AService closes delegated agent streams when the caller stops early."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from a2a_adapter.runtime_service import A2AService
from common.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    MessageRole,
    TextPart,
)


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
        role=MessageRole.USER,
        message_id="msg-test-001",
        parts=[TextPart(text="hello")],
    )


@pytest.mark.asyncio
async def test_send_message_streaming_closes_adapter_stream_on_outer_close():
    closed = False

    async def fake_stream(*_args, **_kwargs):
        nonlocal closed
        try:
            yield {"kind": "message"}
            await asyncio.Event().wait()
        finally:
            closed = True

    service = A2AService()
    service._record_call = AsyncMock()
    with patch(
        "a2a_adapter.runtime_service.adapter_stream_message",
        fake_stream,
    ):
        stream = service.send_message_streaming(
            _make_agent_card(),
            _make_message(),
            agent_id="agent-1",
        )
        assert await anext(stream) == {"kind": "message"}
        await stream.aclose()

    assert closed is True
    service._record_call.assert_awaited_once_with("agent-1", success=True)


@pytest.mark.asyncio
async def test_send_message_closes_delegated_stream_on_outer_close():
    closed = False

    async def delegated_stream(*_args, **_kwargs):
        nonlocal closed
        try:
            yield {"kind": "message"}
            await asyncio.Event().wait()
        finally:
            closed = True

    card = _make_agent_card()
    card.capabilities.streaming = True
    service = A2AService()
    service.send_message_streaming = delegated_stream

    stream = service.send_message(card, _make_message(), agent_id="agent-1")
    assert await anext(stream) == {"kind": "message"}
    await stream.aclose()

    assert closed is True
