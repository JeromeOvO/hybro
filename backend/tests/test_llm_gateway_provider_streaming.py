from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
