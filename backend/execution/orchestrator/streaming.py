"""Provider-neutral model stream assembly."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal, cast

from .models import (
    AssistantMessage,
    FinishReason,
    ModelStreamEvent,
    TextPart,
    ToolCall,
    UsageRecord,
)

_ALLOWED_FINISH_REASONS = frozenset(
    {"stop", "tool_calls", "length", "content_filter", "error", "aborted"}
)


ModelStreamAssemblyErrorCode = Literal[
    "stream_contract_violation",
    "malformed_tool_arguments",
    "truncated_tool_call",
]


class ModelStreamAssemblyError(ValueError):
    """Raised when a provider stream violates the normalized event contract."""

    def __init__(
        self,
        message: str,
        *,
        code: ModelStreamAssemblyErrorCode = "stream_contract_violation",
    ) -> None:
        super().__init__(message)
        self.code = code


class MalformedToolArgumentsError(ModelStreamAssemblyError):
    """Raised only when a completed tool call has invalid arguments."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="malformed_tool_arguments")


class TruncatedToolCallError(ModelStreamAssemblyError):
    """Raised only when tool-call assembly finishes before every call closes."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="truncated_tool_call")


class ModelStreamAssembler:
    """Assemble one normalized assistant message from ordered stream events."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._tool_names: dict[str, str] = {}
        self._tool_arguments: dict[str, list[str]] = {}
        self._closed_calls: list[str] = []
        self._usage: UsageRecord | None = None
        self._finish_reason: FinishReason | None = None
        self._attempt: int | None = None
        self._failed_attempt: int | None = None
        self._scheduled_attempt: int | None = None
        self.retry_events: list[ModelStreamEvent] = []
        self.provider_request_id: str | None = None

    def accept(self, event: ModelStreamEvent) -> None:
        if self._finish_reason is not None:
            raise ModelStreamAssemblyError("stream event received after finish")
        if event.provider_request_id is not None:
            self.provider_request_id = event.provider_request_id
        handler = getattr(self, f"_accept_{event.kind}", None)
        if handler is not None:
            handler(event)

    def _accept_attempt_started(self, event: ModelStreamEvent) -> None:
        if event.attempt is None:
            raise ModelStreamAssemblyError("attempt_started requires an attempt number")
        if self._attempt is not None:
            if event.attempt != self._scheduled_attempt:
                raise ModelStreamAssemblyError(
                    "next model attempt must match the scheduled retry"
                )
            self._text.clear()
            self._tool_names.clear()
            self._tool_arguments.clear()
            self._closed_calls.clear()
            self._usage = None
        elif event.attempt != 1:
            raise ModelStreamAssemblyError("first model attempt must be 1")
        self._attempt = event.attempt
        self._failed_attempt = None
        self._scheduled_attempt = None

    def _accept_attempt_failed(self, event: ModelStreamEvent) -> None:
        if self._attempt is None or event.attempt != self._attempt:
            raise ModelStreamAssemblyError(
                "attempt_failed must identify the active model attempt"
            )
        if self._failed_attempt is not None:
            raise ModelStreamAssemblyError("model attempt failed more than once")
        self._failed_attempt = event.attempt
        self.retry_events.append(event)

    def _accept_retry_scheduled(self, event: ModelStreamEvent) -> None:
        if self._failed_attempt != self._attempt:
            raise ModelStreamAssemblyError(
                "retry_scheduled must follow a failed model attempt"
            )
        if event.attempt != self._attempt + 1:
            raise ModelStreamAssemblyError(
                "retry_scheduled must identify the next model attempt"
            )
        if self._scheduled_attempt is not None:
            raise ModelStreamAssemblyError("model retry scheduled more than once")
        failed_event = self.retry_events[-1]
        if (
            event.error_class != failed_event.error_class
            or event.retryable != failed_event.retryable
        ):
            raise ModelStreamAssemblyError(
                "scheduled retry must preserve failed-attempt classification"
            )
        self._scheduled_attempt = event.attempt
        self.retry_events.append(event)

    def _accept_text_delta(self, event: ModelStreamEvent) -> None:
        self._text.append(event.delta or "")

    def _accept_tool_call_start(self, event: ModelStreamEvent) -> None:
        if not event.call_id or not event.tool_name:
            raise ModelStreamAssemblyError(
                "tool_call_start requires call_id and tool_name"
            )
        if event.call_id in self._tool_names:
            raise ModelStreamAssemblyError("duplicate tool_call_start")
        self._tool_names[event.call_id] = event.tool_name
        self._tool_arguments[event.call_id] = []

    def _accept_tool_call_arguments_delta(self, event: ModelStreamEvent) -> None:
        if not event.call_id or event.call_id not in self._tool_arguments:
            raise ModelStreamAssemblyError("arguments delta precedes tool call start")
        self._tool_arguments[event.call_id].append(event.delta or "")

    def _accept_tool_call_end(self, event: ModelStreamEvent) -> None:
        if not event.call_id or event.call_id not in self._tool_arguments:
            raise ModelStreamAssemblyError("tool_call_end precedes tool call start")
        if event.call_id in self._closed_calls:
            raise ModelStreamAssemblyError("duplicate tool_call_end")
        self._closed_calls.append(event.call_id)

    def _accept_usage(self, event: ModelStreamEvent) -> None:
        if event.usage is None:
            raise ModelStreamAssemblyError("usage event has no usage")
        self._usage = event.usage

    def _accept_finish(self, event: ModelStreamEvent) -> None:
        if event.finish_reason not in _ALLOWED_FINISH_REASONS:
            raise ModelStreamAssemblyError("unsupported finish reason")
        self._finish_reason = cast(FinishReason, event.finish_reason)

    def _accept_error(self, event: ModelStreamEvent) -> None:
        self._finish_reason = "error"

    def build(self, *, message_id: str, created_at: datetime) -> AssistantMessage:
        if self._finish_reason is None:
            raise ModelStreamAssemblyError("stream did not finish")

        tool_calls: list[ToolCall] = []
        if self._finish_reason == "tool_calls":
            if set(self._closed_calls) != set(self._tool_names):
                raise TruncatedToolCallError(
                    "tool-call finish contains truncated calls"
                )
            tool_calls = [
                self._build_tool_call(call_id) for call_id in self._closed_calls
            ]

        content = [TextPart(text="".join(self._text))] if self._text else []
        return AssistantMessage(
            message_id=message_id,
            content=content,
            tool_calls=tool_calls,
            finish_reason=self._finish_reason,
            usage=self._usage,
            created_at=created_at,
        )

    def _build_tool_call(self, call_id: str) -> ToolCall:
        raw_arguments = "".join(self._tool_arguments[call_id]) or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise MalformedToolArgumentsError(
                f"malformed arguments for tool call {call_id!r}"
            ) from exc
        if not isinstance(arguments, dict):
            raise MalformedToolArgumentsError("tool arguments must be an object")
        return ToolCall(
            call_id=call_id,
            tool_name=self._tool_names[call_id],
            arguments=arguments,
        )
