from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from common.config.settings import Settings
from common.dto import LLMResponse, LLMStructuredResponse, LLMUsage
from llm_gateway.errors import LLMModelRoutingError
from llm_gateway.gateway import LLMGatewayImpl
from llm_gateway.model_registry import ModelRegistryImpl
from llm_gateway.providers.deepseek_provider import DeepSeekProvider


def _completion(content: str = '{"ok": true}') -> SimpleNamespace:
    return SimpleNamespace(
        model="deepseek-v4-flash",
        usage=SimpleNamespace(
            prompt_tokens=3,
            completion_tokens=4,
            total_tokens=7,
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model_dump=lambda mode="json": {"id": "deepseek-completion"},
    )


def test_deepseek_provider_builds_openai_compatible_client():
    client = SimpleNamespace()
    with patch(
        "llm_gateway.providers.deepseek_provider.AsyncOpenAI",
        return_value=client,
    ) as client_factory:
        provider = DeepSeekProvider(
            api_key="deepseek-key",
            base_url="https://deepseek.example",
        )

    client_factory.assert_called_once_with(
        api_key="deepseek-key",
        base_url="https://deepseek.example",
    )
    assert provider._client is client


@pytest.mark.asyncio
async def test_deepseek_provider_generates_text_and_preserves_tool_arguments():
    create = AsyncMock(return_value=_completion("answer"))
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )
    tools = [
        {
            "type": "function",
            "function": {"name": "lookup", "parameters": {"type": "object"}},
        }
    ]

    response = await provider.generate(
        [{"role": "user", "content": "hello"}],
        model="deepseek-v4-flash",
        tools=tools,
        tool_choice="auto",
    )

    assert response.content == "answer"
    assert response.usage == LLMUsage(
        prompt_tokens=3,
        completion_tokens=4,
        total_tokens=7,
    )
    create.assert_awaited_once_with(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "hello"}],
        tools=tools,
        tool_choice="auto",
    )


@pytest.mark.asyncio
async def test_deepseek_structured_generation_uses_json_object_and_schema_prompt():
    create = AsyncMock(return_value=_completion())
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )
    messages = [
        {"role": "system", "content": "Classify the request."},
        {"role": "user", "content": "hello"},
    ]
    original = [dict(message) for message in messages]
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }

    response = await provider.generate_structured(
        messages,
        model="deepseek-v4-flash",
        schema=schema,
    )

    assert response == LLMStructuredResponse(
        data={"ok": True},
        model="deepseek-v4-flash",
        usage=LLMUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7),
        raw_response={"id": "deepseek-completion"},
    )
    call = create.await_args.kwargs
    assert call["response_format"] == {"type": "json_object"}
    assert (
        "The JSON object must conform to this schema" in call["messages"][0]["content"]
    )
    assert '"required":["ok"]' in call["messages"][0]["content"]
    assert messages == original


@pytest.mark.asyncio
async def test_deepseek_json_mode_adds_json_instruction():
    create = AsyncMock(return_value=_completion())
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )

    await provider.generate_structured(
        [{"role": "user", "content": "hello"}],
        model="deepseek-v4-flash",
        json_mode=True,
    )

    call = create.await_args.kwargs
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0]["role"] == "system"
    assert "Return only valid JSON" in call["messages"][0]["content"]


@pytest.mark.asyncio
async def test_deepseek_provider_streams_content_deltas():
    async def stream():
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))]
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=" world"))]
        )

    create = AsyncMock(return_value=stream())
    provider = DeepSeekProvider(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )

    chunks = [
        chunk
        async for chunk in provider.generate_stream(
            [{"role": "user", "content": "hello"}],
            model="deepseek-v4-flash",
        )
    ]

    assert chunks == ["hello", " world"]
    assert create.await_args.kwargs["stream"] is True


@pytest.mark.asyncio
async def test_deepseek_provider_rejects_embeddings():
    provider = DeepSeekProvider(client=SimpleNamespace())

    with pytest.raises(LLMModelRoutingError, match="does not provide an embeddings"):
        await provider.embed("hello", "embedding-model")
    with pytest.raises(LLMModelRoutingError, match="does not provide an embeddings"):
        await provider.embed_batch(["hello"], "embedding-model")


@pytest.mark.asyncio
async def test_gateway_routes_all_generation_to_deepseek_and_embeddings_to_openai():
    settings = Settings(
        _env_file=None,
        deepseek_api_key="test-deepseek-key",
        deepseek_model_name="deepseek-v4-pro",
    )
    registry = ModelRegistryImpl(settings)
    deepseek = SimpleNamespace(
        generate=AsyncMock(
            return_value=LLMResponse(content="ok", model="deepseek-v4-pro")
        ),
        generate_structured=AsyncMock(
            return_value=LLMStructuredResponse(
                data={"ok": True}, model="deepseek-v4-pro"
            )
        ),
        generate_stream=lambda *_args, **_kwargs: _empty_stream(),
    )
    openai = SimpleNamespace(embed=AsyncMock(return_value=[0.1]))
    gateway = LLMGatewayImpl(
        model_registry=registry,
        providers={"deepseek": deepseek, "openai": openai},
    )

    for logical_name in (
        "lead_ai_model",
        "classifier_ai_model",
        "context_memory_json_model",
        "supervisor_model",
    ):
        await gateway.generate([{"role": "user", "content": "hi"}], logical_name)
    await gateway.embed("hello", "embedding_model")

    assert deepseek.generate.await_count == 4
    assert all(
        call.kwargs["model"] == "deepseek-v4-pro"
        for call in deepseek.generate.await_args_list
    )
    openai.embed.assert_awaited_once_with("hello", model="text-embedding-3-small")


async def _empty_stream():
    if False:
        yield ""
