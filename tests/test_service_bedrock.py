from unittest.mock import AsyncMock, MagicMock

import pytest

from app_shell.bedrock_service import BedrockService
from common.dto import LLMResponse, LLMStructuredResponse
from llm_gateway.config import LLMGatewayConfig
from llm_gateway.errors import LLMServiceNotBoundError
from llm_gateway.gateway import LLMGatewayImpl
from llm_gateway.model_registry import ModelRegistryImpl
from llm_gateway.services import SupervisorLLMService


class FakeGatewayProvider:
    def __init__(self) -> None:
        self.structured_calls = []

    async def generate(self, messages, model: str, **kwargs):
        return LLMResponse(content="text", model=model)

    async def generate_structured(
        self,
        messages,
        model: str,
        schema=None,
        json_mode: bool = False,
        **kwargs,
    ):
        self.structured_calls.append(
            {
                "messages": messages,
                "model": model,
                "schema": schema,
                "json_mode": json_mode,
                **kwargs,
            }
        )
        return LLMStructuredResponse(data={"action": "done"}, model=model)

    async def generate_stream(self, messages, model: str, **kwargs):
        yield "chunk"

    async def embed(self, text: str, model: str):
        return [1.0]

    async def embed_batch(self, texts, model: str):
        return [[1.0] for _ in texts]


@pytest.mark.asyncio
async def test_unbound_bedrock_service_raises_clear_binding_error():
    service = BedrockService()

    with pytest.raises(LLMServiceNotBoundError):
        await service.call_claude_json("system", "user")


@pytest.mark.asyncio
async def test_bedrock_json_delegates_to_focused_supervisor_service():
    supervisor = AsyncMock()
    supervisor.call_json = AsyncMock(return_value={"action": "done"})
    service = BedrockService(
        supervisor_service=supervisor,
        llm_gateway_config=LLMGatewayConfig(bedrock_request_timeout_seconds=12.5),
    )

    result = await service.call_claude_json("system", "user")

    assert result == {"action": "done"}
    supervisor.call_json.assert_awaited_once_with(
        "system",
        "user",
        model="bedrock_supervisor_model",
        timeout_seconds=12.5,
    )


@pytest.mark.asyncio
async def test_explicit_model_uses_bedrock_hinted_gateway_helper():
    openai_provider = FakeGatewayProvider()
    bedrock_provider = FakeGatewayProvider()
    gateway = LLMGatewayImpl(
        model_registry=ModelRegistryImpl(),
        providers={
            "openai": openai_provider,
            "bedrock": bedrock_provider,
            "gemini": FakeGatewayProvider(),
        },
        config=LLMGatewayConfig(bedrock_request_timeout_seconds=22.0),
    )
    supervisor = SupervisorLLMService(gateway)
    service = BedrockService(supervisor_service=supervisor)
    service.bind_llm_services(
        supervisor_service=supervisor,
        llm_provider=gateway,
        llm_gateway_config=LLMGatewayConfig(bedrock_request_timeout_seconds=22.0),
    )

    result = await service.call_claude_json(
        "system",
        "user",
        model="anthropic.claude-opus",
    )

    assert result == {"action": "done"}
    assert bedrock_provider.structured_calls[0]["model"] == "anthropic.claude-opus"
    assert bedrock_provider.structured_calls[0]["json_mode"] is True
    assert openai_provider.structured_calls == []


@pytest.mark.asyncio
async def test_bedrock_text_delegates_to_focused_supervisor_service():
    supervisor = AsyncMock()
    supervisor.call_text = AsyncMock(return_value="summary")
    service = BedrockService(supervisor_service=supervisor)

    result = await service.call_claude_text("system", "user")

    assert result == "summary"
    supervisor.call_text.assert_awaited_once_with(
        "system",
        "user",
        model="bedrock_supervisor_model",
        timeout_seconds=45.0,
    )


@pytest.mark.asyncio
async def test_explicit_model_text_uses_public_bedrock_provider_override():
    supervisor = AsyncMock()
    gateway = AsyncMock()
    gateway.generate_with_provider = AsyncMock(
        return_value=LLMResponse(content="summary", model="anthropic.claude-opus")
    )
    service = BedrockService()
    service.bind_llm_services(
        supervisor_service=supervisor,
        llm_provider=gateway,
        llm_gateway_config=LLMGatewayConfig(bedrock_request_timeout_seconds=8.0),
    )

    result = await service.call_claude_text(
        "system",
        "user",
        model="anthropic.claude-opus",
    )

    assert result == "summary"
    supervisor.call_text.assert_not_called()
    gateway.generate_with_provider.assert_awaited_once_with(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        model="anthropic.claude-opus",
        provider="bedrock",
        timeout_seconds=8.0,
    )


@pytest.mark.asyncio
async def test_bedrock_text_stream_delegates_to_focused_supervisor_service():
    async def stream(system_prompt, user_prompt, model=None, timeout_seconds=None):
        yield f"{system_prompt}:{user_prompt}:{model}:{timeout_seconds}"

    supervisor = AsyncMock()
    supervisor.call_text_stream = MagicMock(side_effect=stream)
    service = BedrockService(supervisor_service=supervisor)

    chunks = [
        chunk async for chunk in service.call_claude_text_stream("system", "user")
    ]

    assert chunks == ["system:user:bedrock_supervisor_model:45.0"]
    supervisor.call_text_stream.assert_called_once_with(
        "system",
        "user",
        model="bedrock_supervisor_model",
        timeout_seconds=45.0,
    )


@pytest.mark.asyncio
async def test_explicit_model_stream_uses_public_bedrock_provider_override():
    async def stream(*args, **kwargs):
        yield "a"
        yield "b"

    supervisor = AsyncMock()
    supervisor.call_text_stream = MagicMock(side_effect=stream)
    gateway = MagicMock()
    gateway.generate_stream_with_provider = MagicMock(side_effect=stream)
    service = BedrockService()
    service.bind_llm_services(
        supervisor_service=supervisor,
        llm_provider=gateway,
        llm_gateway_config=LLMGatewayConfig(bedrock_request_timeout_seconds=9.0),
    )

    chunks = [
        chunk
        async for chunk in service.call_claude_text_stream(
            "system",
            "user",
            model="anthropic.claude-opus",
        )
    ]

    assert chunks == ["a", "b"]
    supervisor.call_text_stream.assert_not_called()
    gateway.generate_stream_with_provider.assert_called_once_with(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        model="anthropic.claude-opus",
        provider="bedrock",
        timeout_seconds=9.0,
    )
