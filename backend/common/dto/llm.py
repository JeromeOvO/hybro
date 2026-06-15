from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class LLMRequest(FrozenDTO):
    messages: list[dict[str, Any]]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class EmbeddingResult(FrozenDTO):
    text: str
    embedding: list[float]
    model: str | None = None
    dimensions: int | None = None


class ModelInfo(FrozenDTO):
    model_id: str
    logical_name: str
    provider: str
    capabilities: list[str]
    max_context_tokens: int


__all__ = [
    "EmbeddingResult",
    "LLMRequest",
    "LLMResponse",
    "LLMStructuredResponse",
    "LLMUsage",
    "ModelInfo",
]
