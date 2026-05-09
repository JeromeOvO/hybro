from typing import Protocol, runtime_checkable

from common.dto import (
    EmbeddingResult,
    LLMRequest,
    LLMResponse,
    LLMStructuredResponse,
    ModelInfo,
)


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse: ...
    async def generate_structured(
        self, request: LLMRequest, schema: dict
    ) -> LLMStructuredResponse: ...
    async def embed(self, text: str | list[str]) -> list[EmbeddingResult]: ...


@runtime_checkable
class ModelRegistry(Protocol):
    async def get_model(self, logical_name: str) -> ModelInfo | None: ...
    async def list_models(self, provider: str | None = None) -> list[ModelInfo]: ...


__all__ = [
    "LLMProvider",
    "ModelRegistry",
]
