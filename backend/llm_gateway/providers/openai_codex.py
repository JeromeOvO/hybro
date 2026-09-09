"""Single-attempt raw ChatGPT/Codex Responses adapter, not a Codex CLI loop.

Adapted from pi-ai (MIT); see llm_gateway/oauth_notice.py. No endpoint/header
passthrough, SDK retries, WebSockets, response cache, or tool execution.
"""

from __future__ import annotations

import asyncio
import json
import math
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from common.dto import LLMResponse, LLMStructuredResponse, LLMUsage
from common.observability import get_log_context
from llm_gateway._diagnostics import _Diagnostic, _request_diagnostic
from llm_gateway.error_classification import ClassifiedGatewayError
from llm_gateway.errors import LLMModelRoutingError, LLMProviderFailure
from llm_gateway.providers.anthropic_provider import _bounded_bytes, _classification
from llm_gateway.providers.openai_codex_stream import CodexStream, sse_events
from llm_gateway.runtime_config import OAuthCredential
from llm_gateway.structured_generation import (
    with_json_object_instruction,
    with_json_schema_instruction,
)
from llm_gateway.turn_types import GatewayTurnEvent, GatewayTurnRequest

RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CredentialResolver = Callable[[], Awaitable[OAuthCredential]]


class OpenAICodexProvider:
    def __init__(
        self,
        credential: OAuthCredential,
        *,
        resolve: CredentialResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credential = credential
        self._resolve = resolve
        self._transport = transport

    async def generate(
        self, messages: list[dict[str, Any]], model: str, **kwargs: Any
    ) -> LLMResponse:
        content: list[str] = []
        calls: dict[str, dict[str, Any]] = {}
        usage = None
        finish = None
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
                elif event.kind == "finish":
                    finish = event.finish_reason
        if finish not in {"stop", "tool_calls", "length"}:
            raise LLMProviderFailure(
                ClassifiedGatewayError("invalid_request", False), None
            )
        text = "".join(content)
        return LLMResponse(
            content=text,
            model=model,
            usage=usage,
            raw_response={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {
                            "role": "assistant",
                            "content": text,
                            "tool_calls": list(calls.values()),
                        },
                    }
                ],
            },
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
        # Verified Codex body does not advertise JSON schema format support. Use
        # the existing local instruction + validation path, without repair calls.
        try:
            if schema is not None:
                Draft202012Validator.check_schema(schema)
            instructed = (
                with_json_schema_instruction(messages, schema)
                if schema is not None
                else with_json_object_instruction(messages)
            )
            response = await self.generate(instructed, model, **kwargs)
            value = json.loads(response.content)
            if not isinstance(value, dict):
                raise ValueError
            if schema is not None:
                Draft202012Validator(schema).validate(value)
        except (ValueError, ValidationError, SchemaError) as exc:
            failure = LLMProviderFailure(
                ClassifiedGatewayError("invalid_request", False),
                None,
                diagnostic=_Diagnostic(
                    "structured",
                    "bad_json"
                    if isinstance(exc, json.JSONDecodeError)
                    else "structured_validation",
                ),
            )
        else:
            return LLMStructuredResponse(data=value, model=model, usage=response.usage)
        raise failure

    async def generate_stream(
        self, messages: list[dict[str, Any]], model: str, **kwargs: Any
    ) -> AsyncIterator[str]:
        async with aclosing(self._generate_events(messages, model, kwargs)) as events:
            async for event in events:
                if event.kind == "text_delta":
                    yield event.delta or ""

    async def embed(self, text: str, model: str) -> list[float]:
        raise LLMModelRoutingError("OAuth does not provide an embeddings API")

    async def embed_batch(self, texts: list[str], model: str) -> list[list[float]]:
        raise LLMModelRoutingError("OAuth does not provide an embeddings API")

    async def _generate_events(
        self, messages: list[dict[str, Any]], model: str, options: dict[str, Any]
    ) -> AsyncIterator[GatewayTurnEvent]:
        system, inputs = _ordinary_messages(messages)
        tools = options.pop("tools", [])
        choice = options.pop("tool_choice", "auto")
        if isinstance(choice, dict):
            choice = choice["function"]["name"]
        max_tokens = options.pop("max_tokens", 8192)
        payload = _body(
            model,
            system,
            inputs,
            [
                {
                    "type": "function",
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "parameters": tool["function"]["parameters"],
                    "strict": None,
                }
                for tool in tools
            ],
            choice,
        )
        timeout = options.pop("timeout_seconds", 60)
        # Correlation is a request header only, never a cache/session key.
        correlation = options.pop(
            "client_request_id", get_log_context().get("client_request_id")
        )
        temperature = options.pop("temperature", None)
        if temperature is not None:
            if (
                not isinstance(temperature, (int, float))
                or not math.isfinite(temperature)
                or not 0 <= temperature <= 2
            ):
                raise LLMModelRoutingError("Invalid Codex temperature")
            payload["temperature"] = temperature
        if options:
            raise LLMModelRoutingError("Unsupported Codex generation options")
        async with aclosing(
            self._stream(payload, max_tokens, timeout, correlation)
        ) as events:
            async for event in events:
                yield event

    async def stream_turn_once(
        self, request: GatewayTurnRequest, *, cancel_event: asyncio.Event | None = None
    ) -> AsyncIterator[GatewayTurnEvent]:
        inputs: list[dict[str, Any]] = []
        for message in request.messages:
            for part in message.parts:
                if part.kind == "text" and message.role in {"user", "assistant"}:
                    inputs.append(_text(message.role, part.text, len(inputs)))
                elif part.kind == "tool_call" and message.role == "assistant":
                    inputs.append(
                        {
                            "type": "function_call",
                            "call_id": part.call_id,
                            "name": part.tool_name,
                            "arguments": json.dumps(part.arguments),
                        }
                    )
                elif part.kind == "tool_result" and message.role in {"tool", "user"}:
                    inputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": part.call_id,
                            "output": part.content,
                        }
                    )
                else:
                    raise LLMModelRoutingError("Invalid Codex message role")
        payload = _body(
            request.model_id,
            request.system_prompt,
            inputs,
            [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": None,
                }
                for tool in request.tools
            ],
            request.tool_choice,
        )
        if request.thinking_level is not None:
            payload["reasoning"] = {
                "effort": _reasoning_effort(request.model_id, request.thinking_level),
                "summary": "auto",
            }
        async with aclosing(
            self._stream(
                payload,
                request.max_output_tokens,
                request.timeout_seconds,
                get_log_context().get("client_request_id"),
                cancel_event,
            )
        ) as events:
            async for event in events:
                yield event

    async def _stream(  # noqa: C901 - single attempt and cancellation ownership
        self,
        payload: dict[str, Any],
        max_tokens: int,
        timeout: float,
        correlation: str | None,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[GatewayTurnEvent]:
        if type(max_tokens) is not int or not 0 < max_tokens <= 32768:
            raise LLMModelRoutingError("Codex local output limit must be 1..32768")
        if (
            not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 600
        ):
            raise LLMModelRoutingError(
                "Codex timeout must be positive and at most 600 seconds"
            )
        if correlation is not None and (
            not isinstance(correlation, str)
            or not 1 <= len(correlation) <= 256
            or not all(32 < ord(c) < 127 for c in correlation)
        ):
            raise LLMModelRoutingError("Invalid request correlation")
        if len(json.dumps(payload).encode()) > 2 * 1024 * 1024:
            raise LLMModelRoutingError("Codex request exceeds local byte limit")
        owner = asyncio.current_task()

        async def watch() -> None:
            await cancel.wait()
            owner.cancel()

        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        watcher = asyncio.create_task(watch()) if cancel is not None else None
        failure = None
        diagnostic = None
        status = None
        try:
            async with asyncio.timeout(timeout):
                credential = (
                    await self._resolve() if self._resolve else self._credential
                )
                headers = {
                    "authorization": "Bearer "
                    + credential.access_token.get_secret_value(),
                    "chatgpt-account-id": credential.account_id,
                    "originator": "hybro",
                    "user-agent": "hybro",
                    "OpenAI-Beta": "responses=experimental",
                    "accept": "text/event-stream",
                    "accept-encoding": "identity",
                }
                if correlation is not None:
                    headers["x-client-request-id"] = correlation
                state = CodexStream(
                    {t["name"] for t in payload.get("tools", [])},
                    payload["tool_choice"]
                    if isinstance(payload["tool_choice"], str)
                    else payload["tool_choice"]["name"],
                    max_tokens,
                )
                async with httpx.AsyncClient(
                    transport=self._transport or httpx.AsyncHTTPTransport(retries=0),
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST", RESPONSES_URL, json=payload, headers=headers
                    ) as response:
                        status = response.status_code
                        if status != 200:
                            detail = await _http_error_diagnostic(response)
                            raise LLMProviderFailure(
                                _classification({}, status=status),
                                None,
                                diagnostic=detail,
                            )
                        state.request_id = response.headers.get("x-request-id")
                        async with aclosing(sse_events(response)) as raw:
                            async for event in raw:
                                for normalized in state.consume(event):
                                    yield normalized
                                if state.stopped:
                                    # The terminal response, not HTTP EOF, owns
                                    # completion. Close without reading trailers.
                                    break
                        if not state.stopped:
                            raise ValueError(
                                "Codex stream ended without terminal response"
                            )
        except LLMProviderFailure as exc:
            failure = exc.classification
            diagnostic = exc._diagnostic
            if diagnostic is not None and diagnostic.status is None:
                diagnostic = _Diagnostic(
                    diagnostic.stage, diagnostic.category, status, diagnostic.code
                )
        except (TimeoutError, httpx.TimeoutException):
            failure = ClassifiedGatewayError("timeout", True)
            diagnostic = _Diagnostic(
                "stream" if status == 200 else "http", "timeout", status
            )
        except httpx.TransportError:
            failure = ClassifiedGatewayError("network", True)
            diagnostic = _Diagnostic(
                "stream" if status == 200 else "http", "network", status
            )
        except json.JSONDecodeError:
            failure = ClassifiedGatewayError("invalid_request", False)
            diagnostic = _Diagnostic("stream", "bad_json", status)
        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            IndexError,
            RecursionError,
            zlib.error,
        ):
            failure = ClassifiedGatewayError("invalid_request", False)
            diagnostic = _Diagnostic("stream", "stream_validation", status)
        finally:
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
        if failure is not None:
            raise LLMProviderFailure(failure, correlation, diagnostic=diagnostic)


async def _http_error_diagnostic(response: httpx.Response) -> _Diagnostic:
    body = bytearray()
    try:
        async with aclosing(_bounded_bytes(response, 64 * 1024)) as chunks:
            async for chunk in chunks:
                body.extend(chunk)
    except (ValueError, zlib.error):
        return _request_diagnostic(response.status_code, None)
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        # Codex also returns plain-text errors; classify without echoing them.
        value = body.decode("utf-8", errors="replace")
    return _request_diagnostic(response.status_code, value)


def _reasoning_effort(model: str, level: str) -> str:
    from llm_gateway.catalog import TEXT_MODELS

    entry = next(
        (m for m in TEXT_MODELS if m.auth == "oauth" and m.model_id == model), None
    )
    if entry is None or level not in entry.thinking_levels:
        raise LLMModelRoutingError("Unsupported Codex reasoning effort")
    # pi-ai 0.73.1 model thinkingLevelMap; no unsupported xhigh for 5.1.
    if model == "gpt-5.1-codex-mini" and level in {"minimal", "low"}:
        return "medium"
    if not model.startswith("gpt-5.1") and level == "minimal":
        return "low"
    return level


def _body(
    model: str,
    system: str,
    inputs: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    choice: str,
) -> dict[str, Any]:
    from llm_gateway.catalog import TEXT_MODELS

    if model not in {m.model_id for m in TEXT_MODELS if m.auth == "oauth"}:
        raise LLMModelRoutingError("Model is not subscription eligible")
    names = [tool["name"] for tool in tools]
    if (
        len(names) != len(set(names))
        or (choice not in {"auto", "none"} and not tools)
        or (choice not in {"auto", "none", "required"} and choice not in names)
    ):
        raise LLMModelRoutingError("Invalid Codex tool selection")
    body: dict[str, Any] = {
        "model": model,
        "store": False,
        "stream": True,
        "instructions": system or "You are a helpful assistant.",
        "input": inputs,
        "text": {"verbosity": "low"},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": choice
        if choice in {"auto", "none", "required"}
        else {"type": "function", "name": choice},
        "parallel_tool_calls": True,
    }
    if tools:
        body["tools"] = tools
    return body


def _text(role: str, text: str, index: int) -> dict[str, Any]:
    if not isinstance(text, str):
        raise LLMModelRoutingError("Codex gateway accepts text content only")
    if role == "user":
        # Codex uses pi-ai's explicit input parts, not the public API shorthand.
        return {"role": role, "content": [{"type": "input_text", "text": text}]}
    # Replay complete output items as pi-ai does when no provider signature is
    # available. IDs are request-local; original function call IDs stay unchanged.
    return {
        "type": "message",
        "role": "assistant",
        "id": f"msg_{index}",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _ordinary_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    system: list[str] = []
    inputs: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        content = message.get("content") or ""
        if role in {"system", "developer"}:
            if not isinstance(content, str):
                raise LLMModelRoutingError("Codex instructions must be text")
            system.append(content)
        elif role == "tool":
            inputs.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": content,
                }
            )
        elif role in {"user", "assistant"}:
            if content:
                inputs.append(_text(role, content, len(inputs)))
            for call in message.get("tool_calls", []):
                if role != "assistant":
                    raise LLMModelRoutingError("Function calls require assistant role")
                arguments = call["function"]["arguments"]
                if not isinstance(json.loads(arguments), dict):
                    raise LLMModelRoutingError("Function arguments must be an object")
                inputs.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["function"]["name"],
                        "arguments": arguments,
                    }
                )
        else:
            raise LLMModelRoutingError("Unsupported Codex message role")
    return "\n\n".join(system), inputs
