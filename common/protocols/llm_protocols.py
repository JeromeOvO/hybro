from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from common.dto import LLMResponse, LLMStructuredResponse, ModelInfo


@runtime_checkable
class LLMProviderAdapter(Protocol):
    async def generate(
        self, messages: list[dict[str, Any]], model: str, **kwargs: Any
    ) -> LLMResponse: ...

    async def generate_structured(
        self,
        messages: list[dict[str, Any]],
        model: str,
        schema: dict[str, Any] | None = None,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMStructuredResponse: ...

    async def embed(self, text: str, model: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str], model: str) -> list[list[float]]: ...


@runtime_checkable
class LLMStreamingProvider(Protocol):
    def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> AsyncIterator[str]: ...


@runtime_checkable
class LLMTextGateway(Protocol):
    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse: ...


@runtime_checkable
class LLMStructuredGateway(Protocol):
    async def generate_structured(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        json_mode: bool = False,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> LLMStructuredResponse: ...


@runtime_checkable
class LLMEmbeddingGateway(Protocol):
    async def embed(self, text: str, model: str | None = None) -> list[float]: ...

    async def embed_batch(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]: ...


@runtime_checkable
class LLMStreamGateway(Protocol):
    def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]: ...


@runtime_checkable
class LLMGateway(
    LLMTextGateway,
    LLMStructuredGateway,
    LLMEmbeddingGateway,
    LLMStreamGateway,
    Protocol,
):
    pass


@runtime_checkable
class LLMProvider(Protocol):
    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse: ...

    async def generate_structured(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        json_mode: bool = False,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> LLMStructuredResponse: ...

    async def embed(self, text: str, model: str | None = None) -> list[float]: ...

    async def embed_batch(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]: ...


@runtime_checkable
class EmbeddingServiceProtocol(Protocol):
    async def get_embedding(
        self, text: str, target_dim: int | None = None
    ) -> list[float] | None: ...


@runtime_checkable
class RequiredEmbeddingServiceProtocol(Protocol):
    async def get_embedding(
        self, text: str, target_dim: int | None = None
    ) -> list[float]: ...


@runtime_checkable
class ModelSelectableEmbeddingServiceProtocol(Protocol):
    async def get_embedding(
        self,
        text: str,
        target_dim: int | None = None,
        *,
        model: str = "embedding_model",
    ) -> list[float] | None: ...


@runtime_checkable
class ModelRegistry(Protocol):
    def get_model(self, logical_name: str) -> ModelInfo: ...
    def supports_capability(self, model: str, capability: str) -> bool: ...
    def list_models(self, capability: str | None = None) -> list[ModelInfo]: ...


__all__ = [
    "EmbeddingServiceProtocol",
    "LLMEmbeddingGateway",
    "LLMGateway",
    "LLMProvider",
    "LLMProviderAdapter",
    "LLMStreamGateway",
    "LLMStreamingProvider",
    "LLMStructuredGateway",
    "LLMTextGateway",
    "ModelRegistry",
    "ModelSelectableEmbeddingServiceProtocol",
    "RequiredEmbeddingServiceProtocol",
]
