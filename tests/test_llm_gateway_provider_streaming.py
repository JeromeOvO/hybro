import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from llm_gateway.providers.bedrock_provider import BedrockProvider
from llm_gateway.providers.gemini_provider import GeminiProvider
from llm_gateway.providers.openai_provider import OpenAIProvider


class _AsyncStream:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


@pytest.mark.asyncio
async def test_openai_provider_streams_non_empty_chat_deltas():
    stream = _AsyncStream(
        [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="a"))]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="b"))]
            ),
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=stream))
        )
    )
    provider = OpenAIProvider(client=client)

    chunks = [
        chunk
        async for chunk in provider.generate_stream(
            [{"role": "user", "content": "hello"}],
            "gpt-test",
        )
    ]

    assert chunks == ["a", "b"]
    client.chat.completions.create.assert_awaited_once_with(
        model="gpt-test",
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
    )


@pytest.mark.asyncio
async def test_gemini_provider_streaming_falls_back_to_single_generated_chunk():
    client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=AsyncMock(return_value=SimpleNamespace(text="full text"))
        )
    )
    provider = GeminiProvider(client=client)

    chunks = [
        chunk
        async for chunk in provider.generate_stream(
            [{"role": "user", "content": "hello"}],
            "gemini-test",
        )
    ]

    assert chunks == ["full text"]
    client.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_bedrock_provider_streams_content_block_text_deltas():
    session = _FakeBedrockSession(
        stream_events=[
            {
                "chunk": {
                    "bytes": json.dumps(
                        {
                            "type": "content_block_delta",
                            "delta": {"text": "a"},
                        }
                    ).encode()
                }
            },
            {
                "chunk": {
                    "bytes": json.dumps(
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                        }
                    ).encode()
                }
            },
            {
                "chunk": {
                    "bytes": json.dumps(
                        {
                            "type": "content_block_delta",
                            "delta": {"text": "b"},
                        }
                    ).encode()
                }
            },
        ]
    )
    provider = BedrockProvider(session=session, region="us-west-2")

    chunks = [
        chunk
        async for chunk in provider.generate_stream(
            [{"role": "user", "content": "hello"}],
            "anthropic.claude-test",
        )
    ]

    assert chunks == ["a", "b"]
    call = session.client_instance.calls[0]
    assert call["modelId"] == "anthropic.claude-test"
    assert call["contentType"] == "application/json"
    assert call["accept"] == "application/json"
    body = json.loads(call["body"])
    assert body["messages"][0]["content"][0]["text"] == "hello"


class _FakeBedrockClient:
    def __init__(self, stream_events):
        self.stream_events = list(stream_events)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def invoke_model_with_response_stream(self, **kwargs):
        self.calls.append(kwargs)
        return {"body": _AsyncStream(self.stream_events)}


class _FakeBedrockSession:
    def __init__(self, stream_events):
        self.client_instance = _FakeBedrockClient(stream_events)

    def client(self, service_name, region_name=None):
        assert service_name == "bedrock-runtime"
        assert region_name == "us-west-2"
        return self.client_instance
