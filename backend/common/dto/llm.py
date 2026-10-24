from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class LLMUsage(FrozenDTO):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(FrozenDTO):
    content: str
    model: str
    usage: LLMUsage | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)


class LLMStructuredResponse(FrozenDTO):
    data: dict[str, Any]
    model: str
    usage: LLMUsage | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "LLMResponse",
    "LLMStructuredResponse",
    "LLMUsage",
]
