"""Offline SDK-to-proxy contract checks; no application startup or Provider calls."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from openai import APIError, AsyncOpenAI
from PIL import Image

from api_gateway.routes.llm_proxy_routes import router
from common.middleware.request_size import RequestBodyLimitMiddleware
from llm_gateway.agent_proxy import AgentLLMProxy
from llm_gateway.image_types import GatewayImage, GatewayImageResult
from llm_gateway.turn_types import GatewayTurnEvent, GatewayUsage

TOKEN = "fixture-inference-only-token-000000000000"
BASE = "http://fixture/api/v1/internal/llm"


def make_app(events):
    requests = []
    closed = []

    async def stream(request):
        requests.append(request)
        try:
            for event in events:
                if isinstance(event, Exception):
                    raise event
                yield event
        finally:
            closed.append(True)

    gateway = SimpleNamespace(
        _setup=SimpleNamespace(
            config=SimpleNamespace(models=SimpleNamespace(text="selected-model"))
        ),
        stream_turn_once=stream,
        generate_image=AsyncMock(),
    )
    app = FastAPI()
    app.state.agent_llm_proxy = AgentLLMProxy(gateway, TOKEN)
    app.include_router(router, prefix="/api/v1")
    for path, limit in (
        ("chat/completions", 1024 * 1024),
        ("images/generations", 128 * 1024),
        ("images/edits", 10 * 1024 * 1024),
    ):
        app.add_middleware(
            RequestBodyLimitMiddleware,
            path="/api/v1/internal/llm/" + path,
            max_bytes=limit,
        )
    return app, gateway, requests, closed


def tool_events():
    return [
        GatewayTurnEvent(kind="text_delta", delta="Checking."),
        GatewayTurnEvent(
            kind="tool_call_start",
            tool_index=0,
            call_id="call_fixture",
            tool_name="get_weather",
        ),
        GatewayTurnEvent(
            kind="tool_call_arguments_delta",
            tool_index=0,
            call_id="call_fixture",
            delta='{"city":',
        ),
        GatewayTurnEvent(
            kind="tool_call_arguments_delta",
            tool_index=0,
            call_id="call_fixture",
            delta='"Paris"}',
        ),
        GatewayTurnEvent(kind="tool_call_end", tool_index=0, call_id="call_fixture"),
        GatewayTurnEvent(
            kind="usage", usage=GatewayUsage(input_tokens=10, output_tokens=5)
        ),
        GatewayTurnEvent(kind="finish", finish_reason="tool_calls"),
    ]


@pytest.mark.asyncio
async def test_sdk_nonstream_tools_and_tool_result_history():
    app, _, requests, closed = make_app(tool_events())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        sdk = AsyncOpenAI(api_key=TOKEN, base_url=BASE, http_client=http, max_retries=0)
        messages = [
            {"role": "system", "content": "Weather assistant"},
            {"role": "user", "content": "Paris"},
        ]
        response = await sdk.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            extra_body={"client_request_id": "fixture-request"},
        )
        assert response.model == "selected-model"
        assert response.usage.total_tokens == 15
        message = response.choices[0].message
        assert message.content == "Checking."
        assert message.tool_calls[0].function.arguments == '{"city":"Paris"}'
        assert message.tool_calls[0].id == "call_fixture"
        messages += [
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [call.model_dump() for call in message.tool_calls],
            },
            {"role": "tool", "content": "Sunny", "tool_call_id": "call_fixture"},
        ]
        await sdk.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            extra_body={"client_request_id": "fixture-request"},
        )
    result = requests[1].messages[-1].parts[0]
    assert (result.call_id, result.tool_name, result.content) == (
        "call_fixture",
        "get_weather",
        "Sunny",
    )
    assert requests[0].turn_id.startswith("fixture-request:")
    assert requests[0].turn_id != requests[1].turn_id
    assert len(closed) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("include_usage", [False, True])
async def test_sdk_stream_preserves_text_tool_deltas_and_usage(include_usage):
    app, _, requests, closed = make_app(tool_events())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        sdk = AsyncOpenAI(api_key=TOKEN, base_url=BASE, http_client=http, max_retries=0)
        stream = await sdk.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Paris"}],
            stream=True,
            stream_options={"include_usage": include_usage},
        )
        chunks = [chunk async for chunk in stream]
    assert (
        "".join(
            choice.delta.content or "" for chunk in chunks for choice in chunk.choices
        )
        == "Checking."
    )
    calls = [
        call
        for chunk in chunks
        for choice in chunk.choices
        for call in choice.delta.tool_calls or []
    ]
    assert calls[0].id == "call_fixture"
    assert (
        "".join(call.function.arguments or "" for call in calls) == '{"city":"Paris"}'
    )
    assert any(chunk.usage for chunk in chunks) == include_usage
    assert chunks[-1].choices[0].finish_reason == "tool_calls"
    assert len(requests) == len(closed) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("private-provider-marker"), None])
async def test_stream_failure_or_missing_finish_is_not_success(failure):
    events = [GatewayTurnEvent(kind="text_delta", delta="Partial")]
    if failure:
        events.append(failure)
    app, _, _, closed = make_app(events)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        sdk = AsyncOpenAI(api_key=TOKEN, base_url=BASE, http_client=http, max_retries=0)
        stream = await sdk.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "Hi"}], stream=True
        )
        with pytest.raises(APIError) as error:
            _ = [chunk async for chunk in stream]
        assert "private-provider-marker" not in str(error.value)
    assert closed == [True]
    assert app.state.agent_llm_proxy.slots._value == 4


@pytest.mark.asyncio
async def test_auth_validation_body_limits_and_missing_setup_fail_closed():
    app, gateway, requests, _ = make_app([])
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "private-input-marker"}],
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        assert (
            await http.post(BASE + "/chat/completions", json=body)
        ).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        bad = await http.post(
            BASE + "/chat/completions",
            json={**body, "base_url": "private-input-marker"},
            headers=headers,
        )
        assert bad.status_code == 400 and "private-input-marker" not in bad.text
        oversized = await http.post(
            BASE + "/chat/completions",
            content=b"x" * (1024 * 1024 + 1),
            headers=headers,
        )
        assert oversized.status_code == 413
        gateway._setup = None
        assert (
            await http.post(BASE + "/chat/completions", json=body, headers=headers)
        ).status_code == 503
        app.state.agent_llm_proxy.token = type(app.state.agent_llm_proxy.token)("")
        assert (
            await http.post(BASE + "/chat/completions", json=body, headers=headers)
        ).status_code == 503
    assert requests == []


@pytest.mark.asyncio
async def test_sdk_image_generation_and_single_reference_edit():
    import base64

    app, gateway, _, _ = make_app([])
    raw = io.BytesIO()
    image = Image.new("RGB", (2, 2))
    image.save(raw, format="PNG")
    image.close()
    encoded = base64.b64encode(raw.getvalue()).decode()

    async def generate(request):
        return GatewayImageResult(
            image=GatewayImage(mime_type="image/png", data_base64=encoded),
            client_request_id=request.client_request_id,
        )

    gateway.generate_image.side_effect = generate
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        sdk = AsyncOpenAI(api_key=TOKEN, base_url=BASE, http_client=http, max_retries=0)
        result = await sdk.images.generate(model="gpt-image-1", prompt="Fixture")
        assert result.data[0].b64_json == encoded
        reference = io.BytesIO(raw.getvalue())
        reference.name = "reference.png"
        result = await sdk.images.edit(
            model="gpt-image-1",
            prompt="Edit",
            image=reference,
            size="1024x1024",
        )
        assert result.data[0].b64_json == encoded
    calls = gateway.generate_image.await_args_list
    assert calls[0].args[0].reference_images == ()
    assert calls[1].args[0].reference_images[0].decoded_bytes() == raw.getvalue()


@pytest.mark.asyncio
async def test_closing_stream_closes_gateway_and_releases_slot():
    from llm_gateway.agent_proxy import ChatRequest, turn_request

    app, _, _, closed = make_app(tool_events())
    proxy = app.state.agent_llm_proxy
    body = ChatRequest(model="gpt-4o", messages=[{"role": "user", "content": "Hi"}])
    chunks = proxy.chat_chunks(turn_request(body, "fixture"))
    await anext(chunks)  # Role frame, no Provider I/O yet.
    await anext(chunks)  # First Provider text.
    await chunks.aclose()
    assert closed == [True]
    assert proxy.slots._value == 4


def test_openapi_documents_only_supported_proxy_endpoints():
    app, _, _, _ = make_app([])
    schema = app.openapi()
    assert set(schema["paths"]) == {
        "/api/v1/internal/llm/" + path
        for path in ("chat/completions", "images/generations", "images/edits")
    }
    for path in schema["paths"].values():
        assert path["post"]["security"] == [{"HTTPBearer": []}]
    assert (
        schema["components"]["schemas"]["ChatRequest"]["additionalProperties"] is False
    )
