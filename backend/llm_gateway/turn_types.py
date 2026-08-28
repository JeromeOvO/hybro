"""Provider-neutral single-attempt LLM turn contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GatewayContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GatewayTextPart(GatewayContract):
    kind: Literal["text"] = "text"
    text: str


class GatewayToolCallPart(GatewayContract):
    kind: Literal["tool_call"] = "tool_call"
    call_id: str
    tool_name: str
    arguments: dict[str, object]


class GatewayToolResultPart(GatewayContract):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str
    tool_name: str
    content: str
    is_error: bool = False


GatewayTurnPart = Annotated[
    GatewayTextPart | GatewayToolCallPart | GatewayToolResultPart,
    Field(discriminator="kind"),
]


class GatewayTurnMessage(GatewayContract):
    role: Literal["user", "assistant", "tool"]
    parts: list[GatewayTurnPart]


class GatewayToolDefinition(GatewayContract):
    name: str
    description: str
    input_schema: dict[str, object]


class GatewayUsage(GatewayContract):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)


class GatewayTurnRequest(GatewayContract):
    provider: Literal["openai", "deepseek"]
    model_id: str
    api: Literal["chat_completions", "responses"]
    system_prompt: str
    messages: list[GatewayTurnMessage]
    tools: list[GatewayToolDefinition]
    tool_choice: Literal["auto", "none", "required"] = "auto"
    tool_strategy: Literal["native", "structured_action"]
    temperature: float | None = None
    thinking_level: str | None = None
    max_output_tokens: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0)
    turn_id: str

    @model_validator(mode="after")
    def _strategy_matches_supported_route(self) -> GatewayTurnRequest:
        if self.provider == "openai" and self.tool_strategy != "native":
            raise ValueError("OpenAI route requires native tools")
        if self.provider == "deepseek" and self.tool_strategy != "structured_action":
            raise ValueError("DeepSeek route requires structured action")
        if self.provider == "deepseek" and self.api != "chat_completions":
            raise ValueError("DeepSeek route requires chat completions")
        return self


GatewayFinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error"]
GatewayErrorClass = Literal[
    "authentication",
    "rate_limit",
    "timeout",
    "network",
    "provider_5xx",
    "context_overflow",
    "invalid_request",
    "content_filter",
    "aborted",
    "unknown",
]


class GatewayTurnEvent(GatewayContract):
    kind: Literal[
        "text_delta",
        "reasoning_delta",
        "tool_call_start",
        "tool_call_arguments_delta",
        "tool_call_end",
        "usage",
        "finish",
    ]
    provider_request_id: str | None = None
    tool_index: int | None = Field(default=None, ge=0)
    call_id: str | None = None
    tool_name: str | None = None
    delta: str | None = None
    usage: GatewayUsage | None = None
    finish_reason: GatewayFinishReason | None = None

    @model_validator(mode="after")
    def _event_has_required_payload(self) -> GatewayTurnEvent:
        if self.kind == "tool_call_start" and (
            self.tool_index is None or not self.call_id or not self.tool_name
        ):
            raise ValueError("tool_call_start requires index, ID, and name")
        if self.kind in {"tool_call_arguments_delta", "tool_call_end"} and (
            self.tool_index is None or not self.call_id
        ):
            raise ValueError(f"{self.kind} requires index and call ID")
        if self.kind == "usage" and self.usage is None:
            raise ValueError("usage requires normalized usage")
        if self.kind == "finish" and self.finish_reason is None:
            raise ValueError("finish requires normalized reason")
        return self


__all__ = [
    "GatewayErrorClass",
    "GatewayFinishReason",
    "GatewayTextPart",
    "GatewayToolCallPart",
    "GatewayToolDefinition",
    "GatewayToolResultPart",
    "GatewayTurnEvent",
    "GatewayTurnMessage",
    "GatewayTurnPart",
    "GatewayTurnRequest",
    "GatewayUsage",
]
