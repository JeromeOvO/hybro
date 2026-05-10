import inspect
import json
from typing import Any

from google import genai
from google.genai import types

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse


class GeminiProvider:
    def __init__(
        self,
        client: Any | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        default_embedding_model: str | None = None,
    ) -> None:
        self._client = client or genai.Client(
            api_key=api_key or settings.google_api_key or "missing"
        )
        self._default_model = default_model or settings.gemini_model_name
        self._default_embedding_model = (
            default_embedding_model or settings.gemini_embedding_model_name
        )

    async def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        models = _models_client(self._client)
        response = await _maybe_await(
            models.generate_content(
                model=model or self._default_model,
                contents=_messages_to_contents(messages),
                **kwargs,
            )
        )
        return LLMResponse(
            content=getattr(response, "text", "") or "",
            model=model or self._default_model,
            raw_response=_raw_response(response),
        )

    async def generate_structured(
        self,
        messages: list[dict],
        schema: dict,
        model: str | None = None,
        **kwargs,
    ) -> LLMStructuredResponse:
        kwargs = dict(kwargs)
        kwargs.setdefault("config", _json_config())
        models = _models_client(self._client)
        response = await _maybe_await(
            models.generate_content(
                model=model or self._default_model,
                contents=_messages_to_contents(_with_schema_instruction(messages, schema)),
                **kwargs,
            )
        )
        content = getattr(response, "text", "") or ""
        return LLMStructuredResponse(
            data=json.loads(content),
            model=model or self._default_model,
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
        models = _models_client(self._client)
        response = await _maybe_await(
            models.embed_content(
                model=model or self._default_embedding_model,
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
