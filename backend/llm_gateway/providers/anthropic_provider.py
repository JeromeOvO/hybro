"""Single-attempt Anthropic Messages REST adapter (no SDK or HTTP retries).

Wire contract: https://platform.claude.com/docs/en/api/messages/create and
https://platform.claude.com/docs/en/build-with-claude/streaming.
Only text and client tools are enabled; thinking and server tools are not sent.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import math
import re
import zlib
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

import httpx
from httpx_sse import EventSource, SSEError
from jsonschema import Draft202012Validator

from common.dto import LLMResponse, LLMStructuredResponse, LLMUsage
from llm_gateway.error_classification import ClassifiedGatewayError
from llm_gateway.errors import LLMModelRoutingError, LLMProviderFailure
from llm_gateway.structured_generation import (
    with_json_object_instruction,
    with_json_schema_instruction,
)
from llm_gateway.turn_types import (
    GatewayFinishReason,
    GatewayTurnEvent,
    GatewayTurnRequest,
    GatewayUsage,
)

_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_OUTPUT_BYTES = 2 * 1024 * 1024
# Bound SSE framing/metadata as well as decoded output, before line buffering.
_WIRE_BYTES = 16 * 1024 * 1024
_ERROR_BYTES = 64 * 1024
_DECODE_CHUNK_BYTES = 4096
_LINE_END = re.compile(r"[\r\n]")


class AnthropicProvider:
    def __init__(
        self, *, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if not api_key:
            raise ValueError("Provider API key is required")
        self._api_key = api_key
        self._transport = transport

    async def generate(
        self, messages: list[dict[str, Any]], model: str, **kwargs: Any
    ) -> LLMResponse:
        content = []
        usage = None
        calls: dict[str, dict[str, Any]] = {}
        async with aclosing(self._generate_events(messages, model, kwargs)) as events:
            async for event in events:
                if event.kind == "text_delta":
                    content.append(event.delta or "")
                elif event.kind == "tool_call_start":
                    calls[event.call_id] = {
                        "id": event.call_id,
                        "type": "function",
                        "function": {"name": event.tool_name, "arguments": ""},
                    }
                elif event.kind == "tool_call_arguments_delta":
                    calls[event.call_id]["function"]["arguments"] += event.delta or ""
                elif event.kind == "usage":
                    usage = LLMUsage(
                        prompt_tokens=event.usage.input_tokens,
                        completion_tokens=event.usage.output_tokens,
                        total_tokens=event.usage.input_tokens
                        + event.usage.output_tokens,
                    )
        return LLMResponse(
            content="".join(content),
            model=model,
            usage=usage,
            raw_response={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "".join(content),
                            "tool_calls": list(calls.values()),
                        }
                    }
                ]
            }
            if calls
            else {},
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
        messages = (
            with_json_schema_instruction(messages, schema)
            if schema is not None
            else with_json_object_instruction(messages)
        )
        response = await self.generate(messages, model, **kwargs)
        value = json.loads(response.content)
        if schema is not None:
            Draft202012Validator(schema).validate(value)
        return LLMStructuredResponse(data=value, model=model, usage=response.usage)

    async def generate_stream(
        self, messages: list[dict[str, Any]], model: str, **kwargs: Any
    ) -> AsyncIterator[str]:
        async with aclosing(self._generate_events(messages, model, kwargs)) as events:
            async for event in events:
                if event.kind == "text_delta":
                    yield event.delta or ""

    async def _generate_events(
        self, messages: list[dict[str, Any]], model: str, kwargs: dict[str, Any]
    ) -> AsyncIterator[GatewayTurnEvent]:
        tools = kwargs.pop("tools", [])
        choice = kwargs.pop("tool_choice", "auto")
        if isinstance(choice, dict):
            choice = choice["function"]["name"]
        payload = {
            "model": model,
            "system": "\n\n".join(
                str(m.get("content", "")) for m in messages if m["role"] == "system"
            ),
            "messages": _ordinary_messages(messages),
            "max_tokens": kwargs.pop("max_tokens", 8192),
            **kwargs,
            "stream": True,
        }
        if tools:
            payload["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"]["parameters"],
                }
                for tool in tools
            ]
            payload["tool_choice"] = (
                {"type": "any"}
                if choice == "required"
                else {"type": choice}
                if choice in {"auto", "none"}
                else {"type": "tool", "name": choice}
            )
        state = _MessageStream(
            tool_names={tool["function"]["name"] for tool in tools}, tool_choice=choice
        )
        async with aclosing(self._stream(payload, state, timeout=None)) as events:
            async for event in events:
                yield event

    async def embed(self, text: str, model: str) -> list[float]:
        raise LLMModelRoutingError("Anthropic does not provide an embeddings API")

    async def embed_batch(self, texts: list[str], model: str) -> list[list[float]]:
        raise LLMModelRoutingError("Anthropic does not provide an embeddings API")

    async def stream_turn_once(
        self,
        request: GatewayTurnRequest,
        *,
        model: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[GatewayTurnEvent]:
        # Route identity remains private: the original request is a caller transcript,
        # not an Anthropic-shaped public DTO or an impersonated OpenAI request.
        state = _MessageStream(
            tool_names={tool.name for tool in request.tools},
            tool_choice=request.tool_choice,
        )
        async with aclosing(
            self._stream(
                _payload(request, model),
                state,
                timeout=request.timeout_seconds,
                cancel_event=cancel_event,
            )
        ) as events:
            async for event in events:
                yield event

    async def _stream(
        self,
        payload: dict[str, Any],
        state: _MessageStream,
        *,
        timeout: float | None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[GatewayTurnEvent]:
        failure = None
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise asyncio.CancelledError
            # Per-attempt ownership closes both stream and client on success,
            # failure, early iterator close and caller cancellation. No shared pool
            # can be invalidated by another concurrent request's cancellation.
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    transport=self._transport or httpx.AsyncHTTPTransport(retries=0),
                    timeout=timeout,
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        _MESSAGES_URL,
                        json=payload,
                        headers={
                            "x-api-key": self._api_key,
                            "anthropic-version": "2023-06-01",
                            "accept": "text/event-stream",
                            "accept-encoding": "gzip",
                        },
                    ) as response:
                        async with aclosing(
                            _events(response, state, cancel_event)
                        ) as events:
                            async for event in events:
                                yield event
        except (TimeoutError, httpx.TimeoutException):
            failure = ClassifiedGatewayError("timeout", True)
        except httpx.TransportError:
            failure = ClassifiedGatewayError("network", True)
        except (ValueError, KeyError, TypeError, SSEError, zlib.error):
            failure = ClassifiedGatewayError("invalid_request", False)
        # Sever HTTP exception request/header references, including __context__.
        if failure is not None:
            raise LLMProviderFailure(failure, None)


async def _events(
    response: httpx.Response, state: _MessageStream, cancel: asyncio.Event | None
) -> AsyncIterator[GatewayTurnEvent]:
    state.provider_request_id = response.headers.get("request-id")
    if response.status_code != 200:
        error = await _error_body(response)
        raise LLMProviderFailure(
            _classification(
                error,
                status=response.status_code,
                retry_after=response.headers.get("retry-after"),
            ),
            None,
        )
    bounded = _SSEResponse(
        200,
        headers={"content-type": response.headers.get("content-type", "")},
        stream=_BoundedStream(response),
    )
    async with aclosing(EventSource(bounded).aiter_sse()) as stream:
        async for sse in stream:
            if cancel is not None and cancel.is_set():
                raise asyncio.CancelledError
            if not sse.data:
                continue
            event = json.loads(sse.data)
            for normalized in state.consume(event):
                yield normalized
    if not state.stopped:
        raise ValueError("Messages stream ended without message_stop")


class _SSEResponse(httpx.Response):
    async def aiter_lines(self) -> AsyncIterator[str]:
        # HTTPX's splitlines also splits U+0085/U+2028/U+2029 inside JSON.
        # SSE recognizes only CR, LF and CRLF. Join each line once, not once
        # per fragment, and retain UTF-8 decoder state across transport chunks.
        decoder = codecs.getincrementaldecoder("utf-8")()
        fragments: list[str] = []
        skip_lf = False
        async for chunk in self.aiter_bytes():
            text = decoder.decode(chunk)
            if not text:
                continue
            start = int(skip_lf and text.startswith("\n"))
            skip_lf = False
            for match in _LINE_END.finditer(text, start):
                end = match.start()
                if skip_lf and text[end] == "\n" and end == start:
                    start = end + 1
                    skip_lf = False
                    continue
                fragments.append(text[start:end])
                yield "".join(fragments)
                fragments.clear()
                start = end + 1
                skip_lf = text[end] == "\r"
            if start < len(text):
                fragments.append(text[start:])
                skip_lf = False
        fragments.append(decoder.decode(b"", final=True))
        if any(fragments):
            yield "".join(fragments)


class _BoundedStream(httpx.AsyncByteStream):
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async with aclosing(_bounded_bytes(self.response, _WIRE_BYTES)) as chunks:
            async for chunk in chunks:
                yield chunk


async def _bounded_bytes(response: httpx.Response, limit: int) -> AsyncIterator[bytes]:
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip"}:
        raise ValueError("Unsupported Messages content encoding")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    decoded_size = 0
    # aiter_bytes(chunk_size=...) chunks *after* HTTPX's unbounded decompression.
    # Read raw bytes instead, bounding both input and every inflater allocation.
    async with aclosing(_bounded_raw(response, limit)) as chunks:
        async for pending in chunks:
            if inflater is None:
                yield pending
                continue
            while True:
                budget = min(_DECODE_CHUNK_BYTES, limit - decoded_size + 1)
                decoded = inflater.decompress(pending, budget)
                decoded_size += len(decoded)
                if decoded_size > limit:
                    raise ValueError("Messages response exceeds decoded size limit")
                if inflater.unused_data:
                    raise ValueError("Unexpected data after Messages gzip stream")
                pending = inflater.unconsumed_tail
                if decoded:
                    yield decoded
                if not pending and len(decoded) < budget:
                    break
    # Drain unconsumed_tail (including bounded empty-input calls) above. Do not
    # call flush(): its length argument is an initial buffer size, not a cap.
    # eof verifies that all output and the gzip trailer have been consumed.
    if inflater is not None and not inflater.eof:
        raise ValueError("Incomplete Messages gzip stream")


async def _bounded_raw(response: httpx.Response, limit: int) -> AsyncIterator[bytes]:
    size = 0
    async with aclosing(response.aiter_raw()) as chunks:
        async for raw in chunks:
            size += len(raw)
            if size > limit:
                raise ValueError("Messages response exceeds raw size limit")
            # Keep unconsumed_tail copies bounded even for a large transport chunk.
            for offset in range(0, len(raw), _DECODE_CHUNK_BYTES):
                yield raw[offset : offset + _DECODE_CHUNK_BYTES]


def _payload(request: GatewayTurnRequest, model: str) -> dict[str, Any]:
    system = request.system_prompt
    payload: dict[str, Any] = {
        "model": model,
        "system": system,
        "messages": _messages(request),
        "max_tokens": request.max_output_tokens,
        "stream": True,
    }
    if request.temperature is not None:
        if not 0 <= request.temperature <= 1:
            raise ValueError("Anthropic temperature must be between zero and one")
        payload["temperature"] = request.temperature
    if request.tools:
        payload["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in request.tools
        ]
        choice = request.tool_choice
        payload["tool_choice"] = (
            {"type": "any"}
            if choice == "required"
            else {"type": choice}
            if choice in {"auto", "none"}
            else {"type": "tool", "name": choice}
        )
    return payload


def _messages(request: GatewayTurnRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        role = "user" if message.role == "tool" else message.role
        content: list[dict[str, Any]] = []
        for part in message.parts:
            if part.kind == "text":
                content.append({"type": "text", "text": part.text})
            elif part.kind == "tool_call" and role == "assistant":
                content.append(
                    {
                        "type": "tool_use",
                        "id": part.call_id,
                        "name": part.tool_name,
                        "input": part.arguments,
                    }
                )
            elif part.kind == "tool_result" and role == "user":
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": part.call_id,
                        "content": part.content,
                        "is_error": part.is_error,
                    }
                )
            else:
                raise ValueError("Invalid Messages content role")
        # Anthropic combines adjacent same-role turns. Do so explicitly to keep
        # parallel tool results together, in their original content order.
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(content)
        else:
            messages.append({"role": role, "content": content})
    return messages


@dataclass
class _Block:
    kind: str
    call_id: str | None = None
    name: str | None = None
    initial_input: dict[str, Any] = field(default_factory=dict)
    saw_arguments: bool = False


@dataclass
class _MessageStream:
    tool_names: set[str] = field(default_factory=set)
    tool_choice: str = "auto"
    provider_request_id: str | None = None
    started: bool = False
    stopped: bool = False
    blocks: dict[int, _Block] = field(default_factory=dict)
    seen_indices: set[int] = field(default_factory=set)
    call_ids: set[str] = field(default_factory=set)
    usage: dict[str, int] = field(default_factory=dict)
    finish: GatewayFinishReason | None = None
    output_bytes: int = 0

    def event(self, **kwargs: Any) -> GatewayTurnEvent:
        delta = kwargs.get("delta") or ""
        self.output_bytes += len(delta.encode())
        if self.output_bytes > _OUTPUT_BYTES:
            raise ValueError("Messages output exceeds size limit")
        return GatewayTurnEvent(
            **kwargs,
            provider_request_id=self.provider_request_id,
        )

    def consume(self, event: dict[str, Any]) -> list[GatewayTurnEvent]:
        kind = event["type"]
        if kind == "error":
            raise LLMProviderFailure(_classification(event.get("error", {})), None)
        if kind == "ping":
            return []
        if self.stopped:
            raise ValueError("Messages event after message_stop")
        if kind == "message_start":
            return self.start_message(event["message"])
        if not self.started:
            raise ValueError("Messages event before message_start")
        if kind.startswith("content_block_"):
            return self.content(event)
        if kind == "message_delta":
            self.merge_usage(event.get("usage", {}))
            reason = event["delta"].get("stop_reason")
            if reason is not None:
                self.finish = _finish_reason(reason)
            return []
        if kind == "message_stop":
            return self.complete()
        # New metadata event types may be added by Anthropic's versioning policy.
        return []

    def start_message(self, message: dict[str, Any]) -> list[GatewayTurnEvent]:
        if self.started:
            raise ValueError("Duplicate message_start")
        self.started = True
        self.merge_usage(message["usage"])
        return []

    def merge_usage(self, usage: dict[str, Any]) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = usage.get(name)
            if value is not None:
                if type(value) is not int or value < 0:
                    raise ValueError("Invalid Messages usage counter")
                # Message deltas are cumulative; null means no counter update.
                self.usage[name] = value

    def content(self, event: dict[str, Any]) -> list[GatewayTurnEvent]:
        index = event["index"]
        kind = event["type"]
        if kind == "content_block_start":
            return self.start_block(index, event["content_block"])
        block = self.blocks[index]
        if kind == "content_block_stop":
            del self.blocks[index]
            if block.kind != "tool_use":
                return []
            events = []
            if not block.saw_arguments:
                events.append(
                    self.event(
                        kind="tool_call_arguments_delta",
                        tool_index=index,
                        call_id=block.call_id,
                        delta=json.dumps(block.initial_input),
                    )
                )
            events.append(
                self.event(
                    kind="tool_call_end", tool_index=index, call_id=block.call_id
                )
            )
            return events
        if kind == "content_block_delta":
            delta = event["delta"]
            if delta["type"] == "text_delta" and block.kind == "text":
                return [self.event(kind="text_delta", delta=delta["text"])]
            if delta["type"] == "input_json_delta" and block.kind == "tool_use":
                block.saw_arguments = True
                return [
                    self.event(
                        kind="tool_call_arguments_delta",
                        tool_index=index,
                        call_id=block.call_id,
                        delta=delta["partial_json"],
                    )
                ]
            raise ValueError("Unsupported Messages content delta")
        return []

    def start_block(self, index: int, raw: dict[str, Any]) -> list[GatewayTurnEvent]:
        if index in self.seen_indices or self.finish is not None:
            raise ValueError("Invalid Messages block sequence")
        self.seen_indices.add(index)
        block = _Block(raw["type"])
        self.blocks[index] = block
        if block.kind == "text":
            return (
                [self.event(kind="text_delta", delta=raw["text"])]
                if raw["text"]
                else []
            )
        if block.kind != "tool_use":
            # Thinking requires signed transcript replay; never discard signatures
            # or pretend it is enabled for this non-thinking text route.
            raise ValueError("Unsupported Messages content block")
        block.call_id, block.name = raw["id"], raw["name"]
        block.initial_input = raw["input"]
        choice = self.tool_choice
        if (
            block.call_id in self.call_ids
            or not isinstance(block.initial_input, dict)
            or block.name not in self.tool_names
            or choice == "none"
            or (choice not in {"auto", "required"} and choice != block.name)
        ):
            raise ValueError("Invalid Messages tool declaration")
        self.call_ids.add(block.call_id)
        return [
            self.event(
                kind="tool_call_start",
                tool_index=index,
                call_id=block.call_id,
                tool_name=block.name,
            )
        ]

    def complete(self) -> list[GatewayTurnEvent]:
        if self.finish is None or self.blocks:
            raise ValueError("Incomplete Messages stream")
        if self.finish in {"stop", "tool_calls"}:
            if (self.finish == "tool_calls") != bool(self.call_ids):
                raise ValueError("Messages stop reason disagrees with tool inventory")
            if self.tool_choice not in {"auto", "none"} and not self.call_ids:
                raise ValueError("Required tool call missing")
        self.stopped = True
        cache_read = self.usage.get("cache_read_input_tokens", 0)
        cache_write = self.usage.get("cache_creation_input_tokens", 0)
        return [
            self.event(
                kind="usage",
                usage=GatewayUsage(
                    input_tokens=self.usage["input_tokens"] + cache_read + cache_write,
                    output_tokens=self.usage["output_tokens"],
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write,
                ),
            ),
            self.event(kind="finish", finish_reason=self.finish),
        ]


def _finish_reason(reason: str) -> GatewayFinishReason:
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "model_context_window_exceeded": "length",
        "refusal": "content_filter",
    }.get(reason, "error")


async def _error_body(response: httpx.Response) -> dict[str, Any]:
    body = bytearray()
    try:
        async with aclosing(_bounded_bytes(response, _ERROR_BYTES)) as chunks:
            async for chunk in chunks:
                body.extend(chunk)
        value = json.loads(body)
        return value.get("error", {}) if isinstance(value, dict) else {}
    except (ValueError, zlib.error):
        return {}


def _classification(
    error: dict[str, Any], *, status: int | None = None, retry_after: str | None = None
) -> ClassifiedGatewayError:
    if not isinstance(error, dict):
        error = {}
    status = status or {
        "authentication_error": 401,
        "permission_error": 403,
        "rate_limit_error": 429,
        "overloaded_error": 529,
        "api_error": 500,
        "invalid_request_error": 400,
        "not_found_error": 404,
        "request_too_large": 413,
        "timeout_error": 504,
    }.get(error.get("type"))
    delay = None
    try:
        parsed = float(retry_after) if retry_after is not None else None
        if parsed is not None and math.isfinite(parsed):
            delay = max(0.0, parsed)
    except ValueError:
        pass
    if status in {401, 403}:
        return ClassifiedGatewayError("authentication", False)
    if status == 429:
        return ClassifiedGatewayError("rate_limit", True, delay)
    if status in {408, 504}:
        return ClassifiedGatewayError("timeout", True, delay)
    if status is not None and status >= 500:
        return ClassifiedGatewayError("provider_5xx", True, delay)
    if status is not None and 400 <= status < 500:
        message = str(error.get("message", "")).lower()
        overflow = "prompt is too long" in message or (
            "context" in message
            and any(
                word in message for word in ("length", "window", "maximum", "too long")
            )
        )
        return ClassifiedGatewayError(
            "context_overflow" if overflow else "invalid_request", False
        )
    return ClassifiedGatewayError("unknown", False)


def _ordinary_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue
        content = []
        if role == "tool":
            role = "user"
            content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message.get("content", ""),
                }
            )
        else:
            if message.get("content"):
                content.append({"type": "text", "text": message["content"]})
            for call in message.get("tool_calls", []):
                content.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": json.loads(call["function"]["arguments"]),
                    }
                )
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(content)
        else:
            result.append({"role": role, "content": content})
    return result
