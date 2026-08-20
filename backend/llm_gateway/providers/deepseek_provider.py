import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse
from llm_gateway.errors import LLMModelRoutingError
from llm_gateway.providers.openai_provider import OpenAIProvider, _gateway_messages
from llm_gateway.structured_generation import (
    parse_structured_action,
    structured_action_instruction,
    with_json_object_instruction,
    with_json_schema_instruction,
)
from llm_gateway.turn_types import GatewayTurnEvent, GatewayTurnRequest, GatewayUsage

DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek chat adapter implemented through its OpenAI-compatible API."""

    def __init__(
        self,
        client: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(
            client=client
            or AsyncOpenAI(
                api_key=api_key or settings.deepseek_api_key or "missing",
                base_url=DEEPSEEK_OFFICIAL_BASE_URL,
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

    async def stream_turn_once(
        self,
        request: GatewayTurnRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[GatewayTurnEvent]:
        if (
            request.provider != "deepseek"
            or request.api != "chat_completions"
            or request.tool_strategy != "structured_action"
        ):
            raise LLMModelRoutingError("DeepSeek adapter received unsupported route")
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        messages = _gateway_messages(request)
        instruction = structured_action_instruction(request.tools)
        messages = with_json_object_instruction(messages)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = f"{messages[0]['content']}\n\n{instruction}"
        else:
            messages.insert(0, {"role": "system", "content": instruction})
        kwargs: dict[str, Any] = {
            "model": request.model_id,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": request.max_output_tokens,
            **_with_selected_thinking(request.thinking_level),
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        response = await self._client.chat.completions.create(**kwargs)
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        request_id = str(getattr(response, "id", "") or "") or None
        usage = getattr(response, "usage", None)
        if usage is not None:
            yield GatewayTurnEvent(
                kind="usage",
                provider_request_id=request_id,
                usage=GatewayUsage(
                    input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                    cache_read_tokens=int(
                        getattr(
                            getattr(usage, "prompt_tokens_details", None),
                            "cached_tokens",
                            0,
                        )
                        or 0
                    ),
                ),
            )
        choices = getattr(response, "choices", None) or []
        choice = choices[0] if choices else None
        raw_finish = str(getattr(choice, "finish_reason", "stop") or "stop")
        normalized_finish = {
            "stop": "stop",
            "tool_calls": "tool_calls",
            "length": "length",
            "max_tokens": "length",
            "content_filter": "content_filter",
        }.get(raw_finish, "error")
        if normalized_finish in {"length", "content_filter", "error"}:
            yield GatewayTurnEvent(
                kind="finish",
                finish_reason=normalized_finish,
                provider_request_id=request_id,
            )
            return
        message = getattr(choice, "message", None)
        content = str(getattr(message, "content", "") or "")
        for event in parse_structured_action(
            content,
            tools=request.tools,
            turn_id=request.turn_id,
            provider_request_id=request_id,
        ):
            yield event

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


def _with_selected_thinking(level: str | None) -> dict[str, Any]:
    return {"extra_body": {"thinking": {"type": level or "disabled"}}}


def _with_default_thinking_disabled(kwargs: dict[str, Any]) -> dict[str, Any]:
    updated = dict(kwargs)
    extra_body = dict(updated.pop("extra_body", None) or {})
    extra_body.setdefault("thinking", {"type": "disabled"})
    updated["extra_body"] = extra_body
    return updated


__all__ = ["DeepSeekProvider"]
