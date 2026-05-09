from typing import Protocol, runtime_checkable

from common.dto import LLMResponse, LLMStructuredResponse, ModelInfo


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> LLMResponse: ...

    async def generate_structured(
        self, messages: list[dict], schema: dict, model: str | None = None, **kwargs
    ) -> LLMStructuredResponse: ...

    async def embed(self, text: str, model: str | None = None) -> list[float]: ...
    async def embed_batch(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]: ...


@runtime_checkable
class ModelRegistry(Protocol):
    def get_model(self, logical_name: str) -> ModelInfo: ...
    def supports_capability(self, model: str, capability: str) -> bool: ...
    def list_models(self, capability: str | None = None) -> list[ModelInfo]: ...


__all__ = ["LLMProvider", "ModelRegistry"]
