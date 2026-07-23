import inspect
import json
from typing import Any

from google import genai
from google.genai import types

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse, LLMUsage
from llm_gateway.structured_generation import with_json_object_instruction


class GeminiProvider:
    def __init__(
        self,
        client: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client or genai.Client(
            api_key=api_key or settings.google_api_key or "missing"
        )

    async def generate(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> LLMResponse:
        models = _models_client(self._client)
        response = await _maybe_await(
            models.generate_content(
                model=model,
                contents=_messages_to_contents(messages),
                **kwargs,
            )
        )
        return LLMResponse(
            content=getattr(response, "text", "") or "",
            model=model,
            usage=_usage_from_gemini(response),
            raw_response=_raw_response(response),
        )

    async def generate_structured(
        self,
        messages: list[dict],
        *args,
        model: str | None = None,
        schema: dict | None = None,
        json_mode: bool = False,
        **kwargs,
    ) -> LLMStructuredResponse:
        model, schema = _normalize_structured_args(
            args,
            model,
            schema,
        )
        kwargs = dict(kwargs)
        kwargs.setdefault("config", _json_config())
        structured_messages = (
            with_json_object_instruction(messages)
            if schema is None and json_mode
            else _with_schema_instruction(messages, schema or {})
        )
        models = _models_client(self._client)
        response = await _maybe_await(
            models.generate_content(
                model=model,
                contents=_messages_to_contents(structured_messages),
                **kwargs,
            )
        )
        content = getattr(response, "text", "") or ""
        return LLMStructuredResponse(
            data=json.loads(content),
            model=model,
            usage=_usage_from_gemini(response),
            raw_response=_raw_response(response),
        )

    async def generate_stream(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ):
        response = await self.generate(messages, model=model, **kwargs)
        if response.content:
            yield response.content

    async def embed(self, text: str, model: str) -> list[float]:
        embeddings = await self.embed_batch([text], model=model)
        return embeddings[0] if embeddings else []

    async def embed_batch(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        models = _models_client(self._client)
        response = await _maybe_await(
            models.embed_content(
                model=model,
                contents=texts,
            )
        )
        return [list(item.values) for item in getattr(response, "embeddings", [])]


def _messages_to_contents(messages: list[dict]) -> list[str]:
    contents: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(part) for part in content)
        contents.append(f"{role}: {content}" if role else str(content))
    return contents


def _models_client(client: Any) -> Any:
    aio_client = getattr(client, "aio", None)
    if aio_client is not None and hasattr(aio_client, "models"):
        return aio_client.models
    return client.models


def _usage_from_gemini(response: Any) -> LLMUsage | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        usage = getattr(response, "usageMetadata", None)
    if usage is None:
        return None

    prompt_tokens = int(_read_usage(usage, "prompt_token_count", "promptTokenCount"))
    completion_tokens = int(
        _read_usage(usage, "candidates_token_count", "candidatesTokenCount")
    )
    total_tokens = int(_read_usage(usage, "total_token_count", "totalTokenCount"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _read_usage(usage: Any, snake_name: str, camel_name: str) -> int:
    if isinstance(usage, dict):
        return int(usage.get(snake_name) or usage.get(camel_name) or 0)
    return int(
        getattr(usage, snake_name, None) or getattr(usage, camel_name, None) or 0
    )


def _with_schema_instruction(messages: list[dict], schema: dict) -> list[dict]:
    instruction = (
        "Return only valid JSON that conforms to this JSON Schema: "
        f"{json.dumps(schema, sort_keys=True)}"
    )
    updated = [dict(message) for message in messages]
    if updated and updated[0].get("role") == "system":
        updated[0]["content"] = f"{updated[0].get('content', '')}\n\n{instruction}"
    else:
        updated.insert(0, {"role": "system", "content": instruction})
    return updated


def _json_config() -> Any:
    try:
        return types.GenerateContentConfig(response_mime_type="application/json")
    except Exception:
        return {"response_mime_type": "application/json"}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _raw_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    return {}


__all__ = ["GeminiProvider"]


def _normalize_structured_args(
    args: tuple[Any, ...],
    model: str | None,
    schema: dict | str | None,
) -> tuple[str, dict | None]:
    if len(args) == 2:
        legacy_schema, legacy_model = args
        return str(legacy_model), legacy_schema if isinstance(
            legacy_schema, dict
        ) else None
    if len(args) == 1:
        first = args[0]
        if isinstance(first, dict):
            if model is None:
                raise TypeError("model is required")
            return model, first
        return str(first), schema if isinstance(schema, dict) else None
    if model is None:
        raise TypeError("model is required")
    return model, schema if isinstance(schema, dict) else None
