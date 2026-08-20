from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_gateway.providers.deepseek_provider import DeepSeekProvider
from llm_gateway.turn_types import (
    GatewayTextPart,
    GatewayToolDefinition,
    GatewayTurnMessage,
    GatewayTurnRequest,
)


def request() -> GatewayTurnRequest:
    return GatewayTurnRequest(
        provider="deepseek",
        model_id="deepseek-test",
        api="chat_completions",
        system_prompt="system",
        messages=[GatewayTurnMessage(role="user", parts=[GatewayTextPart(text="hi")])],
        tools=[
            GatewayToolDefinition(
                name="echo",
                description="echo",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        ],
        tool_strategy="structured_action",
        max_output_tokens=100,
        timeout_seconds=10,
        turn_id="test_deepseek_structured_action",
    )


def test_deepseek_client_uses_only_official_endpoint(monkeypatch):
    from llm_gateway.providers import deepseek_provider as module

    constructor = MagicMock()
    monkeypatch.setattr(module, "AsyncOpenAI", constructor)
    module.DeepSeekProvider(api_key="deepseek-key")
    constructor.assert_called_once_with(
        api_key="deepseek-key", base_url="https://api.deepseek.com"
    )


def response(payload, *, finish_reason="stop"):
    return SimpleNamespace(
        id="deepseek-request",
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
                finish_reason=finish_reason,
            )
        ],
    )


@pytest.mark.asyncio
async def test_named_deepseek_fixture_emits_locally_validated_tool_call():
    create = AsyncMock(
        return_value=response(
            {
                "action": "tool_calls",
                "calls": [{"tool_name": "echo", "arguments": {"value": "ok"}}],
            }
        )
    )
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )

    events = [event async for event in provider.stream_turn_once(request())]

    assert [event.kind for event in events] == [
        "usage",
        "tool_call_start",
        "tool_call_arguments_delta",
        "tool_call_end",
        "finish",
    ]
    assert events[-1].finish_reason == "tool_calls"
    assert events[1].call_id.startswith("call_")
    kwargs = create.await_args.kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_deepseek_turn_propagates_selected_thinking_level():
    create = AsyncMock(return_value=response({"action": "final", "content": "done"}))
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )

    [
        event
        async for event in provider.stream_turn_once(
            request().model_copy(update={"thinking_level": "enabled"})
        )
    ]

    assert create.await_args.kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_reason", "normalized"),
    [("content_filter", "content_filter"), ("length", "length")],
)
async def test_deepseek_terminal_finish_reason_precedes_structured_parsing(
    raw_reason, normalized
):
    create = AsyncMock(
        return_value=response({"not": "valid action"}, finish_reason=raw_reason)
    )
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )

    events = [event async for event in provider.stream_turn_once(request())]

    assert events[-1].kind == "finish"
    assert events[-1].finish_reason == normalized


@pytest.mark.asyncio
async def test_deepseek_structured_final_action_and_thinking_override():
    create = AsyncMock(return_value=response({"action": "final", "content": "done"}))
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )
    events = [event async for event in provider.stream_turn_once(request())]
    assert [(event.kind, event.delta, event.finish_reason) for event in events[1:]] == [
        ("text_delta", "done", None),
        ("finish", None, "stop"),
    ]

    await provider.generate(
        [], "deepseek-test", extra_body={"thinking": {"type": "enabled"}}
    )
    assert create.await_args.kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "tool_calls",
            "calls": [{"tool_name": "unknown", "arguments": {}}],
        },
        {"action": "tool_calls", "calls": [{"tool_name": "echo", "arguments": {}}]},
    ],
)
async def test_deepseek_normalizes_complete_invalid_calls_for_kernel_rejection(payload):
    create = AsyncMock(return_value=response(payload))
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )
    events = [event async for event in provider.stream_turn_once(request())]
    assert [event.kind for event in events[1:]] == [
        "tool_call_start",
        "tool_call_arguments_delta",
        "tool_call_end",
        "finish",
    ]
    assert events[1].tool_name == payload["calls"][0]["tool_name"]
