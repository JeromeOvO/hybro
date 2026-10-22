from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from common.dto import LLMResponse, LLMStructuredResponse


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


__all__ = [
    "LLMEmbeddingGateway",
    "LLMGateway",
    "LLMProviderAdapter",
    "LLMStreamGateway",
    "LLMStructuredGateway",
    "LLMTextGateway",
]
