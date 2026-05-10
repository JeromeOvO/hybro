import json
from typing import Any

from openai import AsyncOpenAI

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse, LLMUsage


class OpenAIProvider:
    def __init__(
        self,
        client: Any | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        default_embedding_model: str | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI(
            api_key=api_key or settings.openai_api_key or "missing"
        )
        self._default_model = default_model or settings.lead_ai_model
        self._default_embedding_model = (
            default_embedding_model or settings.embedding_model
        )

    async def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=messages,
            **kwargs,
        )
        return LLMResponse(
            content=_content_from_chat_response(response),
            model=_response_model(response, model or self._default_model),
            usage=_usage_from_openai(response),
            raw_response=_raw_response(response),
        )

    async def generate_structured(
        self,
        messages: list[dict],
        schema: dict,
        model: str | None = None,
        **kwargs,
    ) -> LLMStructuredResponse:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_response",
                "schema": schema,
                "strict": True,
            },
        }
        response = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=messages,
            response_format=response_format,
            **kwargs,
        )
        content = _content_from_chat_response(response)
        return LLMStructuredResponse(
            data=json.loads(content),
            model=_response_model(response, model or self._default_model),
            usage=_usage_from_openai(response),
            raw_response=_raw_response(response),
        )

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        embeddings = await self.embed_batch([text], model=model)
        return embeddings[0] if embeddings else []

    async def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=model or self._default_embedding_model,
            input=texts,
        )
        return [list(item.embedding) for item in response.data]


def _content_from_chat_response(response: Any) -> str:
    choices = getattr(response, "choices", [])
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    return getattr(message, "content", "") or ""


def _usage_from_openai(response: Any) -> LLMUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return LLMUsage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
    )


def _response_model(response: Any, fallback: str) -> str:
    return str(getattr(response, "model", None) or fallback)


def _raw_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    return {}


__all__ = ["OpenAIProvider"]
