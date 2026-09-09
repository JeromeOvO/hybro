"""Single-attempt provider checks against the installed SDK and mock HTTPX."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APITimeoutError, AsyncOpenAI
from openai.types.responses import ResponseErrorEvent

from llm_gateway.error_classification import classify_gateway_error
from llm_gateway.providers import DeepSeekProvider, OpenAIProvider
from llm_gateway.turn_types import GatewayTurnRequest


def turn(api="chat_completions", provider="openai"):
    return GatewayTurnRequest(
        provider=provider,
        model_id="test-model",
        api=api,
        system_prompt="",
        messages=[],
        tools=[],
        tool_strategy="native" if provider == "openai" else "structured_action",
        max_output_tokens=100,
        timeout_seconds=1,
        turn_id="original-id",
    )


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("stage", ["create", "iterate"])
async def test_sdk_timeout_normalized_without_hidden_retry(api, stage):
    closed = []
    error = APITimeoutError(request=httpx.Request("POST", "https://provider.invalid"))

    async def stream():
        try:
            raise error
            yield
        finally:
            closed.append(True)

    create = (
        AsyncMock(side_effect=error)
        if stage == "create"
        else AsyncMock(return_value=stream())
    )
    provider = OpenAIProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
            responses=SimpleNamespace(create=create),
        )
    )
    with pytest.raises(TimeoutError) as raised:
        [event async for event in provider.stream_turn_once(turn(api))]
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert classify_gateway_error(raised.value).error_class == "timeout"
    assert create.await_count == 1
    assert create.await_args.kwargs["timeout"] == 1
    assert bool(closed) is (stage == "iterate")


async def test_installed_responses_error_event_keeps_retry_classification():
    calls = []
    error = ResponseErrorEvent(
        type="error",
        sequence_number=1,
        code="rate_limit_exceeded",
        message="private",
        param=None,
    )

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {error.model_dump_json()}\n\ndata: [DONE]\n\n",
        )

    async with AsyncOpenAI(
        api_key="fixture-only",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        with pytest.raises(RuntimeError) as raised:
            [
                event
                async for event in OpenAIProvider(client=client).stream_turn_once(
                    turn("responses")
                )
            ]
    assert classify_gateway_error(raised.value).error_class == "rate_limit"
    assert len(calls) == 1


@pytest.mark.parametrize("provider_id", ["openai", "deepseek"])
async def test_installed_sdk_original_text_structured_embedding_wire(provider_id):
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("embeddings"):
            return httpx.Response(
                200,
                json={
                    "data": [{"index": 0, "embedding": [0.25]}],
                    "model": "embed",
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "wire-id",
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
        )

    async with AsyncOpenAI(
        api_key="fixture-only",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        provider = (OpenAIProvider if provider_id == "openai" else DeepSeekProvider)(
            client=client
        )
        result = await provider.generate([], "test-model", top_p=0.8)
        assert result.usage.total_tokens == 5
        structured = await provider.generate_structured(
            [], model="test-model", json_mode=True
        )
        assert structured.data == {"ok": True}
        assert calls[0][1]["top_p"] == 0.8
        if provider_id == "openai":
            assert await provider.embed("one", "embed") == [0.25]
            assert await provider.embed_batch(["two"], "embed") == [[0.25]]
            assert await provider.embed_batch([], "embed") == []
            assert len(calls) == 4
        else:
            with pytest.raises(RuntimeError, match="embeddings"):
                await provider.embed("one", "embed")
            assert len(calls) == 2


async def test_deepseek_single_attempt_timeout_is_classifiable():
    create = AsyncMock(
        side_effect=APITimeoutError(
            request=httpx.Request("POST", "https://provider.invalid")
        )
    )
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )
    with pytest.raises(TimeoutError) as raised:
        [event async for event in provider.stream_turn_once(turn(provider="deepseek"))]
    assert create.await_count == 1
    assert classify_gateway_error(raised.value).error_class == "timeout"
    assert raised.value.__cause__ is None and raised.value.__context__ is None
