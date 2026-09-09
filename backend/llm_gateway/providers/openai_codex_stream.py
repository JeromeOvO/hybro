"""Responses SSE translation adapted from pi-ai; see llm_gateway/oauth_notice.py.

The original gateway has no signed reasoning replay field. Emit summaries only;
never fabricate reasoning items or retain per-conversation session state.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from llm_gateway._diagnostics import _http_diagnostic
from llm_gateway.error_classification import ClassifiedGatewayError
from llm_gateway.errors import LLMProviderFailure
from llm_gateway.providers.anthropic_provider import _BoundedStream, _SSEResponse
from llm_gateway.turn_types import GatewayTurnEvent, GatewayUsage


async def sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    content_type = response.headers.get("content-type")
    # Codex can omit Content-Type on a valid SSE response. Like pi-ai, parse
    # the body; JSON events and a terminal response are still required.
    if content_type and content_type.split(";", 1)[0] != "text/event-stream":
        raise ValueError("Expected SSE")
    bounded = _SSEResponse(200, stream=_BoundedStream(response))
    data: list[str] = []
    async for line in bounded.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            value = "\n".join(data)
            data.clear()
            if value.strip() != "[DONE]":
                yield json.loads(value)
    if data:
        value = "\n".join(data)
        if value.strip() != "[DONE]":
            yield json.loads(value)


@dataclass
class _Slot:
    kind: str
    item_id: str
    call_id: str | None = None
    name: str | None = None
    text: str = ""
    ended: bool = False


@dataclass
class CodexStream:
    tools: set[str]
    choice: str
    max_tokens: int
    request_id: str | None = None
    slots: dict[int, _Slot] = field(default_factory=dict)
    call_ids: set[str] = field(default_factory=set)
    stopped: bool = False
    output_bytes: int = 0

    def event(self, **fields: Any) -> GatewayTurnEvent:
        delta = fields.get("delta", "")
        if not isinstance(delta, str):
            raise ValueError("Invalid delta")
        self.output_bytes += len(delta.encode("utf-8"))
        # Codex rejects max_output_tokens. This is a local byte ceiling, NOT an
        # exact token counter or a guarantee about server-side token consumption.
        if self.output_bytes > min(2 * 1024 * 1024, self.max_tokens * 4):
            raise ValueError("Codex local output byte limit exceeded")
        return GatewayTurnEvent(provider_request_id=self.request_id, **fields)

    def start(self, index: int, item: dict[str, Any]) -> list[GatewayTurnEvent]:
        if type(index) is not int or index < 0 or index in self.slots:
            raise ValueError("Invalid output index")
        kind, item_id = item["type"], item["id"]
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("Invalid output item ID")
        if kind not in {"function_call", "message", "reasoning"}:
            raise ValueError("Unsupported output item")
        slot = _Slot(kind, item_id)
        self.slots[index] = slot
        if kind != "function_call":
            return []
        slot.call_id, slot.name = item["call_id"], item["name"]
        if (
            not isinstance(slot.call_id, str)
            or not slot.call_id
            or slot.call_id in self.call_ids
            or slot.name not in self.tools
            or self.choice == "none"
            or (self.choice not in {"auto", "required"} and self.choice != slot.name)
        ):
            raise ValueError("Invalid function declaration")
        self.call_ids.add(slot.call_id)
        events = [
            self.event(
                kind="tool_call_start",
                tool_index=index,
                call_id=slot.call_id,
                tool_name=slot.name,
            )
        ]
        if item.get("arguments"):
            events.extend(self.append(index, item["arguments"]))
        return events

    def append(self, index: int, delta: str) -> list[GatewayTurnEvent]:
        slot = self.slots[index]
        if slot.ended or not isinstance(delta, str):
            raise ValueError("Invalid output delta")
        slot.text += delta
        if not delta:
            return []
        if slot.kind == "function_call":
            return [
                self.event(
                    kind="tool_call_arguments_delta",
                    tool_index=index,
                    call_id=slot.call_id,
                    delta=delta,
                )
            ]
        return [
            self.event(
                kind="text_delta" if slot.kind == "message" else "reasoning_delta",
                delta=delta,
            )
        ]

    def backfill(self, index: int, complete: str) -> list[GatewayTurnEvent]:
        previous = self.slots[index].text
        if not isinstance(complete, str) or not complete.startswith(previous):
            raise ValueError("Final output disagrees with deltas")
        return self.append(index, complete[len(previous) :])

    def end(
        self, index: int, item: dict[str, Any], *, incomplete: bool = False
    ) -> list[GatewayTurnEvent]:
        events = []
        if index not in self.slots:
            events.extend(self.start(index, {**item, "arguments": ""}))
        slot = self.slots[index]
        if slot.ended or (slot.kind, slot.item_id) != (item["type"], item["id"]):
            raise ValueError("Invalid output item completion")
        incomplete = incomplete or item.get("status") == "incomplete"
        if slot.kind == "function_call":
            if (item["call_id"], item["name"]) != (slot.call_id, slot.name):
                raise ValueError("Function identity changed")
            events.extend(self.backfill(index, item["arguments"]))
            if not incomplete:
                if not isinstance(json.loads(slot.text), dict):
                    raise ValueError("Function arguments must be an object")
                events.append(
                    self.event(
                        kind="tool_call_end", tool_index=index, call_id=slot.call_id
                    )
                )
        elif slot.kind == "message":
            complete = "".join(
                part["text"] if part["type"] == "output_text" else part["refusal"]
                for part in item.get("content", [])
            )
            events.extend(self.backfill(index, complete))
        # Reasoning signatures cannot be represented by the original public DTO.
        # Their private encrypted content is intentionally not emitted as text.
        slot.ended = not incomplete
        return events

    def consume(self, event: dict[str, Any]) -> list[GatewayTurnEvent]:
        kind = event["type"]
        if self.stopped:
            raise ValueError("Event after terminal response")
        if kind in {"error", "response.failed"}:
            error = event if kind == "error" else event["response"].get("error")
            raise _stream_failure(error)
        if kind == "response.created":
            self.request_id = event["response"]["id"]
            return []
        if kind == "response.output_item.added":
            return self.start(event["output_index"], event["item"])
        if kind == "response.output_item.done":
            return self.end(event["output_index"], event["item"])
        expected = {
            "response.output_text.delta": "message",
            "response.refusal.delta": "message",
            "response.reasoning_summary_text.delta": "reasoning",
            "response.reasoning_text.delta": "reasoning",
            "response.reasoning_summary_part.done": "reasoning",
            "response.function_call_arguments.delta": "function_call",
            "response.function_call_arguments.done": "function_call",
        }.get(kind)
        if expected is not None:
            index = event["output_index"]
            slot = self.slots[index]
            if (
                slot.kind != expected
                or event.get("item_id", slot.item_id) != slot.item_id
            ):
                raise ValueError("Output slot mismatch")
            if kind == "response.function_call_arguments.done":
                return self.backfill(index, event["arguments"])
            return self.append(
                index,
                "\n\n"
                if kind == "response.reasoning_summary_part.done"
                else event["delta"],
            )
        if kind in {"response.completed", "response.incomplete", "response.done"}:
            return self.complete(_terminal_response(kind, event["response"]))
        # Other Responses events carry metadata/part boundaries, not output deltas.
        return []

    def complete(self, response: dict[str, Any]) -> list[GatewayTurnEvent]:
        events = []
        self.request_id = response.get("id", self.request_id)
        status = response.get("status")
        if status == "completed":
            reason = "tool_calls" if self.call_ids else "stop"
        elif (
            status == "incomplete"
            and (response.get("incomplete_details") or {}).get("reason")
            == "max_output_tokens"
        ):
            reason = "length"
        else:
            raise _stream_failure(response.get("error"))
        usage = _usage(response.get("usage"))
        if usage is not None and usage.output_tokens > self.max_tokens:
            # This is already consumed usage, not a failed inference to retry.
            # Codex has no server-side max_output_tokens request control.
            reason = "length"
        for index, item in enumerate(response.get("output", [])):
            if index not in self.slots or not self.slots[index].ended:
                events.extend(self.end(index, item, incomplete=reason == "length"))
        if reason != "length":
            if any(not slot.ended for slot in self.slots.values()):
                raise ValueError("Unfinished output item")
            if self.choice not in {"auto", "none"} and not self.call_ids:
                raise ValueError("Required function missing")
            reason = "tool_calls" if self.call_ids else "stop"
        if usage is not None:
            events.append(self.event(kind="usage", usage=usage))
        self.stopped = True
        events.append(self.event(kind="finish", finish_reason=reason))
        return events


def _terminal_response(kind: str, response: dict[str, Any]) -> dict[str, Any]:
    # pi-ai accepts absent status on completed/done. Never infer success
    # from response.incomplete or an explicit failed/unknown status.
    if kind == "response.incomplete":
        if response.get("status") not in (None, "incomplete"):
            raise ValueError("Conflicting terminal status")
        return {**response, "status": "incomplete"}
    if response.get("status") is None:
        return {**response, "status": "completed"}
    return response


def _usage(raw: dict[str, Any] | None) -> GatewayUsage | None:
    if raw is None:
        return None
    details = raw.get("input_tokens_details") or {}
    counters = [
        raw["input_tokens"],
        raw["output_tokens"],
        details.get("cached_tokens", 0),
        details.get("cache_write_tokens", 0),
    ]
    if any(type(n) is not int or n < 0 for n in counters):
        raise ValueError("Invalid usage")
    # Original GatewayUsage counts total input, including cached tokens.
    return GatewayUsage(
        input_tokens=counters[0],
        output_tokens=counters[1],
        cache_read_tokens=counters[2],
        cache_write_tokens=counters[3],
    )


def _stream_failure(error: object) -> LLMProviderFailure:
    return LLMProviderFailure(
        _stream_error(error),
        None,
        diagnostic=_http_diagnostic(
            "stream", None, error.get("code") if isinstance(error, dict) else None
        ),
    )


def _stream_error(error: object) -> ClassifiedGatewayError:
    # Never retain/log provider messages. Only bounded, known wire codes affect
    # classification; the existing caller still owns every retry decision.
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str) or len(code) > 128:
        return ClassifiedGatewayError("unknown", False)
    return {
        "server_error": ClassifiedGatewayError("provider_5xx", True),
        "rate_limit_exceeded": ClassifiedGatewayError("rate_limit", True),
        "insufficient_quota": ClassifiedGatewayError("rate_limit", False),
        "usage_limit_reached": ClassifiedGatewayError("rate_limit", False),
        "usage_not_included": ClassifiedGatewayError("rate_limit", False),
        "invalid_api_key": ClassifiedGatewayError("authentication", False),
        "invalid_token": ClassifiedGatewayError("authentication", False),
        "token_expired": ClassifiedGatewayError("authentication", False),
        "authentication_error": ClassifiedGatewayError("authentication", False),
        "context_length_exceeded": ClassifiedGatewayError("context_overflow", False),
        "content_filter": ClassifiedGatewayError("content_filter", False),
    }.get(code, ClassifiedGatewayError("unknown", False))
