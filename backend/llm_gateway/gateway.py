import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Literal, TypeVar

from common.dto import LLMResponse, LLMStructuredResponse, ModelInfo
from common.protocols import LLMProviderAdapter
from llm_gateway.config import LLMGatewayConfig
from llm_gateway.errors import LLMModelRoutingError, LLMStreamingUnsupportedError
from llm_gateway.model_registry import ModelRegistryImpl
from llm_gateway.providers import GeminiProvider, OpenAIProvider

ProviderHint = Literal["openai", "gemini"]
T = TypeVar("T")


class LLMGatewayImpl:
    def __init__(
        self,
        model_registry: ModelRegistryImpl | None = None,
        providers: dict[str, LLMProviderAdapter] | None = None,
        config: LLMGatewayConfig | None = None,
    ) -> None:
        self._model_registry = model_registry or ModelRegistryImpl()
        self.config = config or LLMGatewayConfig()
        if providers is None:
            providers = {
                "openai": OpenAIProvider(),
                "gemini": GeminiProvider(),
            }
        self._providers: dict[str, LLMProviderAdapter] = providers

    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        timeout_seconds = _pop_timeout(kwargs, self.config.request_timeout_seconds)
        model_info, provider = self._resolve_provider(
            model or self.config.default_generation_model
        )
        return await self._with_retry(
            lambda: provider.generate(messages, model=model_info.model_id, **kwargs),
            timeout_seconds=timeout_seconds,
        )

    async def generate_structured(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMStructuredResponse:
        if schema is None and not json_mode:
            raise LLMModelRoutingError(
                "generate_structured requires schema or json_mode=True"
            )
        timeout_seconds = _pop_timeout(kwargs, self.config.request_timeout_seconds)
        model_info, provider = self._resolve_provider(
            model or self.config.default_generation_model
        )
        return await self._with_retry(
            lambda: provider.generate_structured(
                messages,
                model=model_info.model_id,
                schema=schema,
                json_mode=json_mode,
                **kwargs,
            ),
            timeout_seconds=timeout_seconds,
        )

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        model_info, provider = self._resolve_provider(
            model or self.config.default_embedding_model
        )
        if "embedding" not in model_info.capabilities:
            raise ValueError(
                f"Model {model_info.logical_name} does not support embeddings"
            )
        return await self._with_retry(
            lambda: provider.embed(text, model=model_info.model_id),
            timeout_seconds=self.config.request_timeout_seconds,
        )

    async def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        model_info, provider = self._resolve_provider(
            model or self.config.default_embedding_model
        )
        if "embedding" not in model_info.capabilities:
            raise ValueError(
                f"Model {model_info.logical_name} does not support embeddings"
            )
        return await self._with_retry(
            lambda: provider.embed_batch(texts, model=model_info.model_id),
            timeout_seconds=self.config.request_timeout_seconds,
        )

    async def _generate_with_provider_hint(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        provider_hint: ProviderHint,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self.generate_with_provider(
            messages,
            model=model,
            provider=provider_hint,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )

    async def generate_with_provider(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        provider: ProviderHint,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        model_info, provider_adapter = self._resolve_provider(
            model, provider_hint=provider
        )
        return await self._with_retry(
            lambda: provider_adapter.generate(
                messages, model=model_info.model_id, **kwargs
            ),
            timeout_seconds=timeout_seconds or self.config.request_timeout_seconds,
        )

    async def _generate_structured_with_provider_hint(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        provider_hint: ProviderHint,
        schema: dict[str, Any] | None = None,
        json_mode: bool = False,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> LLMStructuredResponse:
        return await self.generate_structured_with_provider(
            messages,
            model=model,
            provider=provider_hint,
            schema=schema,
            json_mode=json_mode,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )

    async def generate_structured_with_provider(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        provider: ProviderHint,
        schema: dict[str, Any] | None = None,
        json_mode: bool = False,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> LLMStructuredResponse:
        if schema is None and not json_mode:
            raise LLMModelRoutingError(
                "generate_structured requires schema or json_mode=True"
            )
        model_info, provider_adapter = self._resolve_provider(
            model, provider_hint=provider
        )
        return await self._with_retry(
            lambda: provider_adapter.generate_structured(
                messages,
                model=model_info.model_id,
                schema=schema,
                json_mode=json_mode,
                **kwargs,
            ),
            timeout_seconds=timeout_seconds or self.config.request_timeout_seconds,
        )

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ):
        async for chunk in self._generate_stream(
            messages,
            model=model or self.config.default_generation_model,
            timeout_seconds=timeout_seconds,
            provider_hint=None,
            **kwargs,
        ):
            yield chunk

    async def _generate_stream_with_provider_hint(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        provider_hint: ProviderHint,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ):
        async for chunk in self.generate_stream_with_provider(
            messages,
            model=model,
            provider=provider_hint,
            timeout_seconds=timeout_seconds,
            **kwargs,
        ):
            yield chunk

    async def generate_stream_with_provider(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        provider: ProviderHint,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ):
        async for chunk in self._generate_stream(
            messages,
            model=model,
            timeout_seconds=timeout_seconds,
            provider_hint=provider,
            **kwargs,
        ):
            yield chunk

    async def _generate_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        timeout_seconds: float | None,
        provider_hint: ProviderHint | None,
        **kwargs: Any,
    ):
        timeout = timeout_seconds or self.config.stream_timeout_seconds
        attempts = max(1, self.config.max_attempts)
        for attempt in range(1, attempts + 1):
            yielded = False
            try:
                model_info, provider = self._resolve_provider(
                    model,
                    provider_hint=provider_hint,
                )
                stream_method = getattr(provider, "generate_stream", None)
                if stream_method is None:
                    raise LLMStreamingUnsupportedError(
                        f"Provider {model_info.provider} does not support streaming"
                    )
                async with asyncio.timeout(timeout):
                    async for chunk in stream_method(
                        messages,
                        model=model_info.model_id,
                        **kwargs,
                    ):
                        yielded = True
                        yield chunk
                return
            except (LLMModelRoutingError, LLMStreamingUnsupportedError):
                raise
            except Exception:
                if yielded or attempt >= attempts:
                    raise
                await asyncio.sleep(self.config.retry_backoff_seconds * attempt)

    def _resolve_provider(
        self,
        model: str,
        provider_hint: ProviderHint | None = None,
    ) -> tuple[ModelInfo, LLMProviderAdapter]:
        try:
            model_info = self._model_registry.get_model(model)
        except KeyError as exc:
            if provider_hint is None:
                raise LLMModelRoutingError(
                    f"Model {model!r} is not registered and no provider hint was supplied"
                ) from exc
            model_info = ModelInfo(
                model_id=model,
                logical_name=model,
                provider=provider_hint,
                capabilities=["json_schema", "tool_use", "vision"],
                max_context_tokens=0,
            )
        if provider_hint is not None and model_info.provider != provider_hint:
            raise LLMModelRoutingError(
                f"Model {model!r} routes to {model_info.provider}, not {provider_hint}"
            )
        provider = self._providers.get(model_info.provider)
        if provider is None:
            raise LLMModelRoutingError(
                f"No provider configured for {model_info.provider}"
            )
        return model_info, provider

    async def _with_retry(
        self,
        operation: Callable[[], Awaitable[T] | Coroutine[Any, Any, T]],
        *,
        timeout_seconds: float,
    ) -> T:
        attempts = max(1, self.config.max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                async with asyncio.timeout(timeout_seconds):
                    return await operation()
            except LLMModelRoutingError:
                raise
            except Exception:
                if attempt >= attempts:
                    raise
                await asyncio.sleep(self.config.retry_backoff_seconds * attempt)
        raise RuntimeError("unreachable retry state")


def _pop_timeout(kwargs: dict[str, Any], default: float) -> float:
    timeout = kwargs.pop("timeout_seconds", None)
    return default if timeout is None else float(timeout)


__all__ = ["LLMGatewayImpl"]
