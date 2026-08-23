import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse, LLMUsage
from llm_gateway.turn_types import (
    GatewayToolCallPart,
    GatewayToolResultPart,
    GatewayTurnEvent,
    GatewayTurnRequest,
    GatewayUsage,
)


class OpenAIProvider:
    def __init__(
        self,
        client: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI(
            api_key=api_key or settings.openai_api_key or "missing"
        )

    async def generate(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            **kwargs,
        )
        return LLMResponse(
            content=_content_from_chat_response(response),
            model=_response_model(response, model),
            usage=_usage_from_openai(response),
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
        if schema is None and json_mode:
            response_format = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "schema": schema,
                    "strict": True,
                },
            }
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            response_format=response_format,
            **kwargs,
        )
        content = _content_from_chat_response(response)
        return LLMStructuredResponse(
            data=json.loads(content),
            model=_response_model(response, model),
            usage=_usage_from_openai(response),
            raw_response=_raw_response(response),
        )

    async def generate_stream(
        self,
        messages: list[dict],
        model: str,
        **kwargs,
    ):
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            **kwargs,
        )
        async for event in stream:
            choices = getattr(event, "choices", [])
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None)
            if content:
                yield content

    async def stream_turn_once(
        self,
        request: GatewayTurnRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[GatewayTurnEvent]:
        if request.provider != "openai" or request.api != "chat_completions":
            raise ValueError("OpenAI adapter received unsupported route")
        kwargs: dict[str, Any] = {
            "model": request.model_id,
            "messages": _gateway_messages(request),
            # Native tool calling must not use OpenAI strict mode: strict
            # rejects third-party agent schemas (``uniqueItems``, missing
            # ``additionalProperties``, ...) and buys nothing when the
            # caller validates arguments itself. Strict is reserved for the
            # structured-action strategy.
            "tools": [
                _gateway_tool(tool, strict=request.tool_strategy == "structured_action")
                for tool in request.tools
            ],
            "tool_choice": request.tool_choice,
            "stream": True,
            "stream_options": {"include_usage": True},
            # gpt-5/o-series models reject max_tokens; max_completion_tokens
            # is accepted by every current chat model the gateway routes.
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        stream = await self._client.chat.completions.create(**kwargs)
        call_state: dict[int, dict[str, Any]] = {}
        pending_finish: str | None = None
        finish_request_id: str | None = None
        try:
            async for chunk in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError
                request_id = str(getattr(chunk, "id", "") or "") or None
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    yield GatewayTurnEvent(
                        kind="usage",
                        provider_request_id=request_id,
                        usage=GatewayUsage(
                            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                            output_tokens=int(
                                getattr(usage, "completion_tokens", 0) or 0
                            ),
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
                choices = getattr(chunk, "choices", []) or []
                for choice in choices:
                    delta = getattr(choice, "delta", None)
                    text = getattr(delta, "content", None)
                    if text:
                        yield GatewayTurnEvent(
                            kind="text_delta",
                            delta=str(text),
                            provider_request_id=request_id,
                        )
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield GatewayTurnEvent(
                            kind="reasoning_delta",
                            delta=str(reasoning),
                            provider_request_id=request_id,
                        )
                    for raw_call in getattr(delta, "tool_calls", None) or []:
                        index = int(getattr(raw_call, "index", 0) or 0)
                        state = call_state.setdefault(
                            index,
                            {"id": None, "name": None, "pending": [], "started": False},
                        )
                        call_id = getattr(raw_call, "id", None)
                        function = getattr(raw_call, "function", None)
                        name = getattr(function, "name", None)
                        arguments = getattr(function, "arguments", None)
                        if call_id:
                            state["id"] = str(call_id)
                        if name:
                            state["name"] = str(name)
                        if arguments:
                            state["pending"].append(str(arguments))
                        if state["id"] and state["name"] and not state["started"]:
                            state["started"] = True
                            yield GatewayTurnEvent(
                                kind="tool_call_start",
                                tool_index=index,
                                call_id=state["id"],
                                tool_name=state["name"],
                                provider_request_id=request_id,
                            )
                            for pending in state["pending"]:
                                yield GatewayTurnEvent(
                                    kind="tool_call_arguments_delta",
                                    tool_index=index,
                                    call_id=state["id"],
                                    delta=pending,
                                    provider_request_id=request_id,
                                )
                            state["pending"].clear()
                        elif state["started"] and arguments:
                            yield GatewayTurnEvent(
                                kind="tool_call_arguments_delta",
                                tool_index=index,
                                call_id=state["id"],
                                delta=str(arguments),
                                provider_request_id=request_id,
                            )
                    raw_finish = getattr(choice, "finish_reason", None)
                    if raw_finish is not None:
                        finish = _normalize_finish_reason(str(raw_finish))
                        if finish == "tool_calls":
                            for index in sorted(call_state):
                                state = call_state[index]
                                if not state["started"]:
                                    raise ValueError(
                                        "tool call finished before ID and name arrived"
                                    )
                                yield GatewayTurnEvent(
                                    kind="tool_call_end",
                                    tool_index=index,
                                    call_id=state["id"],
                                    provider_request_id=request_id,
                                )
                        if pending_finish is not None:
                            raise ValueError("provider emitted multiple finish reasons")
                        pending_finish = finish
                        finish_request_id = request_id
            if pending_finish is None:
                raise ValueError("OpenAI stream ended without a finish reason")
            yield GatewayTurnEvent(
                kind="finish",
                finish_reason=pending_finish,
                provider_request_id=finish_request_id,
            )
        finally:
            close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    async def embed(self, text: str, model: str) -> list[float]:
        embeddings = await self.embed_batch([text], model=model)
        return embeddings[0] if embeddings else []

    async def embed_batch(
        self,
        texts: list[str],
        model: str,
    ) -> list[list[float]]:
        if not texts:
            return []
        response = await self._client.embeddings.create(
            model=model,
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


def _gateway_messages(request: GatewayTurnRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    for message in request.messages:
        if message.role == "assistant":
            content = "".join(
                part.text for part in message.parts if part.kind == "text"
            )
            tool_calls = [
                {
                    "id": part.call_id,
                    "type": "function",
                    "function": {
                        "name": part.tool_name,
                        "arguments": json.dumps(part.arguments, separators=(",", ":")),
                    },
                }
                for part in message.parts
                if isinstance(part, GatewayToolCallPart)
            ]
            payload: dict[str, Any] = {"role": "assistant", "content": content or None}
            if tool_calls:
                payload["tool_calls"] = tool_calls
            messages.append(payload)
            continue
        tool_results = [
            part for part in message.parts if isinstance(part, GatewayToolResultPart)
        ]
        if message.role == "tool" and tool_results:
            messages.extend(
                {
                    "role": "tool",
                    "tool_call_id": part.call_id,
                    "content": part.content,
                }
                for part in tool_results
            )
            continue
        messages.append(
            {
                "role": message.role,
                "content": "".join(
                    part.text for part in message.parts if part.kind == "text"
                ),
            }
        )
    return messages


def _strictify_schema(schema: Any) -> Any:
    """Normalize a tool input schema for OpenAI strict mode.

    Strict function calling requires ``additionalProperties: false`` at every
    object level and rejects keywords such as ``uniqueItems`` and ``$schema``.
    Agent cards are third-party input, so the provider normalizes instead of
    failing the turn.
    """
    if isinstance(schema, dict):
        result: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "$schema":
                continue
            if key == "uniqueItems":
                continue
            if key in ("properties", "patternProperties", "$defs", "definitions"):
                result[key] = {
                    name: _strictify_schema(item)
                    for name, item in (value or {}).items()
                }
            elif key in ("anyOf", "oneOf", "allOf", "prefixItems"):
                result[key] = [_strictify_schema(item) for item in value]
            elif key in ("items", "additionalProperties"):
                if isinstance(value, dict):
                    result[key] = _strictify_schema(value)
                else:
                    result[key] = value
            else:
                result[key] = value
        if result.get("type") == "object":
            result.setdefault("additionalProperties", False)
        return result
    if isinstance(schema, list):
        return [_strictify_schema(item) for item in schema]
    return schema


def _gateway_tool(tool: Any, *, strict: bool) -> dict[str, Any]:
    parameters = _strictify_schema(tool.input_schema) if strict else tool.input_schema
    function: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "parameters": parameters,
    }
    if strict:
        function["strict"] = True
    return {"type": "function", "function": function}


def _normalize_finish_reason(reason: str) -> str:
    return {
        "stop": "stop",
        "tool_calls": "tool_calls",
        "length": "length",
        "content_filter": "content_filter",
    }.get(reason, "error")


__all__ = ["OpenAIProvider"]


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
