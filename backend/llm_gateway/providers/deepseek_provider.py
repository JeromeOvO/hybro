import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse
from llm_gateway.errors import LLMModelRoutingError
from llm_gateway.providers.openai_provider import OpenAIProvider
from llm_gateway.structured_generation import (
    with_json_object_instruction,
    with_json_schema_instruction,
)


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek chat adapter implemented through its OpenAI-compatible API."""

    def __init__(
        self,
        client: Any | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            client=client
            or AsyncOpenAI(
                api_key=api_key or settings.deepseek_api_key or "missing",
                base_url=base_url or settings.deepseek_base_url,
            )
        )

    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> LLMResponse:
        return await super().generate(
            messages,
            model=model,
            **_with_default_thinking_disabled(kwargs),
        )

    async def generate_structured(
        self,
        messages: list[dict[str, Any]],
        model: str,
        schema: dict[str, Any] | None = None,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMStructuredResponse:
        if schema is None and not json_mode:
            raise LLMModelRoutingError(
                "generate_structured requires schema or json_mode=True"
            )
        structured_messages = (
            with_json_schema_instruction(messages, schema)
            if schema is not None
            else with_json_object_instruction(messages)
        )
        response = await self.generate(
            structured_messages,
            model=model,
            response_format={"type": "json_object"},
            **kwargs,
        )
        return LLMStructuredResponse(
            data=json.loads(response.content),
            model=response.model,
            usage=response.usage,
            raw_response=response.raw_response,
        )

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        async for chunk in super().generate_stream(
            messages,
            model=model,
            **_with_default_thinking_disabled(kwargs),
        ):
            yield chunk

    async def embed(self, text: str, model: str) -> list[float]:
        del text, model
        raise LLMModelRoutingError("DeepSeek does not provide an embeddings API")

    async def embed_batch(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        del texts, model
        raise LLMModelRoutingError("DeepSeek does not provide an embeddings API")


def _with_default_thinking_disabled(kwargs: dict[str, Any]) -> dict[str, Any]:
    updated = dict(kwargs)
    extra_body = dict(updated.pop("extra_body", None) or {})
    extra_body.setdefault("thinking", {"type": "disabled"})
    updated["extra_body"] = extra_body
    return updated


__all__ = ["DeepSeekProvider"]
