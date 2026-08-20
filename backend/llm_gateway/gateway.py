import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import aclosing
from typing import Any, Literal, Protocol, TypeVar

from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse, ModelInfo
from common.observability import get_logger, safe_exception_metadata
from common.protocols import LLMProviderAdapter
from llm_gateway.config import LLMGatewayConfig
from llm_gateway.errors import (
    LLMModelRoutingError,
    LLMProviderConfigurationError,
    LLMStreamingUnsupportedError,
)
from llm_gateway.model_registry import ModelRegistryImpl
from llm_gateway.providers import DeepSeekProvider, OpenAIProvider
from llm_gateway.turn_types import GatewayTurnEvent, GatewayTurnRequest

ProviderHint = Literal["openai", "deepseek"]
T = TypeVar("T")
logger = get_logger(__name__)


class LLMTurnGateway(Protocol):
    def stream_turn_once(
        self,
        request: GatewayTurnRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[GatewayTurnEvent]: ...


class LLMGatewayImpl:
    def __init__(
        self,
        model_registry: ModelRegistryImpl | None = None,
        providers: dict[str, LLMProviderAdapter] | None = None,
        config: LLMGatewayConfig | None = None,
        settings_obj: Any = None,
    ) -> None:
        settings_obj = settings_obj or settings
        self.config = config or LLMGatewayConfig.from_settings(settings_obj)
        self._model_registry = model_registry or ModelRegistryImpl(
            settings_obj,
            generation_provider=self.config.generation_provider,
        )
        self._enforce_provider_credentials = providers is None
        self._provider_credentials = {
            "openai": str(getattr(settings_obj, "openai_api_key", "") or ""),
            "deepseek": str(getattr(settings_obj, "deepseek_api_key", "") or ""),
        }
        if providers is None:
            providers = {
                "openai": OpenAIProvider(
                    api_key=getattr(settings_obj, "openai_api_key", "")
                ),
                "deepseek": DeepSeekProvider(
                    api_key=getattr(settings_obj, "deepseek_api_key", ""),
                ),
            }
        self._providers: dict[str, LLMProviderAdapter] = providers

    async def stream_turn_once(
        self,
        request: GatewayTurnRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[GatewayTurnEvent]:
        """Stream exactly one frozen provider attempt with no hidden retry."""

        if request.provider not in {"openai", "deepseek"}:
            raise LLMModelRoutingError(
                f"Unsupported turn provider {request.provider!r}"
            )
        if (
            self._enforce_provider_credentials
            and not self._provider_credentials[request.provider].strip()
        ):
            raise LLMProviderConfigurationError(
                f"{request.provider} API key is not configured"
            )
        provider = self._providers.get(request.provider)
        if provider is None:
            raise LLMModelRoutingError(f"No provider configured for {request.provider}")
        stream_method = getattr(provider, "stream_turn_once", None)
        if stream_method is None:
            raise LLMStreamingUnsupportedError(
                f"Provider {request.provider} does not support turn streaming"
            )
        async for event in stream_method(request, cancel_event=cancel_event):
            yield event

    async def generate(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        started_at = time.perf_counter()
        requested_model = model or self.config.default_generation_model
        try:
            timeout_seconds = _pop_timeout(
                kwargs,
                self.config.request_timeout_seconds,
            )
            model_info, provider = self._resolve_provider(requested_model)
        except Exception as exc:
            _log_call_completed(
                operation="generate",
                model=requested_model,
                started_at=started_at,
                outcome="error",
                error=exc,
            )
            raise
        return await self._with_retry(
            lambda: provider.generate(messages, model=model_info.model_id, **kwargs),
            timeout_seconds=timeout_seconds,
            operation_name="generate",
            provider=model_info.provider,
            model=model_info.model_id,
            started_at=started_at,
        )

    async def generate_structured(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        json_mode: bool = False,
        **kwargs: Any,
    ) -> LLMStructuredResponse:
        started_at = time.perf_counter()
        requested_model = model or self.config.default_generation_model
        try:
            if schema is None and not json_mode:
                raise LLMModelRoutingError(
                    "generate_structured requires schema or json_mode=True"
                )
            timeout_seconds = _pop_timeout(
                kwargs,
                self.config.request_timeout_seconds,
            )
            model_info, provider = self._resolve_provider(requested_model)
        except Exception as exc:
            _log_call_completed(
                operation="generate_structured",
                model=requested_model,
                started_at=started_at,
                outcome="error",
                error=exc,
            )
            raise
        return await self._with_retry(
            lambda: provider.generate_structured(
                messages,
                model=model_info.model_id,
                schema=schema,
                json_mode=json_mode,
                **kwargs,
            ),
            timeout_seconds=timeout_seconds,
            operation_name="generate_structured",
            provider=model_info.provider,
            model=model_info.model_id,
            started_at=started_at,
        )

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        started_at = time.perf_counter()
        requested_model = model or self.config.default_embedding_model
        try:
            model_info, provider = self._resolve_provider(requested_model)
            if "embedding" not in model_info.capabilities:
                raise ValueError(
                    f"Model {model_info.logical_name} does not support embeddings"
                )
        except Exception as exc:
            _log_call_completed(
                operation="embed",
                model=requested_model,
                started_at=started_at,
                outcome="error",
                error=exc,
            )
            raise
        return await self._with_retry(
            lambda: provider.embed(text, model=model_info.model_id),
            timeout_seconds=self.config.request_timeout_seconds,
            operation_name="embed",
            provider=model_info.provider,
            model=model_info.model_id,
            started_at=started_at,
        )

    async def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        started_at = time.perf_counter()
        requested_model = model or self.config.default_embedding_model
        try:
            model_info, provider = self._resolve_provider(requested_model)
            if "embedding" not in model_info.capabilities:
                raise ValueError(
                    f"Model {model_info.logical_name} does not support embeddings"
                )
        except Exception as exc:
            _log_call_completed(
                operation="embed_batch",
                model=requested_model,
                started_at=started_at,
                outcome="error",
                error=exc,
            )
            raise
        return await self._with_retry(
            lambda: provider.embed_batch(texts, model=model_info.model_id),
            timeout_seconds=self.config.request_timeout_seconds,
            operation_name="embed_batch",
            provider=model_info.provider,
            model=model_info.model_id,
            started_at=started_at,
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
        started_at = time.perf_counter()
        try:
            model_info, provider_adapter = self._resolve_provider(
                model,
                provider_hint=provider,
            )
        except Exception as exc:
            _log_call_completed(
                operation="generate",
                provider=provider,
                model=model,
                started_at=started_at,
                outcome="error",
                error=exc,
            )
            raise
        return await self._with_retry(
            lambda: provider_adapter.generate(
                messages, model=model_info.model_id, **kwargs
            ),
            timeout_seconds=timeout_seconds or self.config.request_timeout_seconds,
            operation_name="generate",
            provider=model_info.provider,
            model=model_info.model_id,
            started_at=started_at,
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
        started_at = time.perf_counter()
        try:
            if schema is None and not json_mode:
                raise LLMModelRoutingError(
                    "generate_structured requires schema or json_mode=True"
                )
            model_info, provider_adapter = self._resolve_provider(
                model,
                provider_hint=provider,
            )
        except Exception as exc:
            _log_call_completed(
                operation="generate_structured",
                provider=provider,
                model=model,
                started_at=started_at,
                outcome="error",
                error=exc,
            )
            raise
        return await self._with_retry(
            lambda: provider_adapter.generate_structured(
                messages,
                model=model_info.model_id,
                schema=schema,
                json_mode=json_mode,
                **kwargs,
            ),
            timeout_seconds=timeout_seconds or self.config.request_timeout_seconds,
            operation_name="generate_structured",
            provider=model_info.provider,
            model=model_info.model_id,
            started_at=started_at,
        )

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ):
        async with aclosing(
            self._generate_stream(
                messages,
                model=model or self.config.default_generation_model,
                timeout_seconds=timeout_seconds,
                provider_hint=None,
                **kwargs,
            )
        ) as stream:
            async for chunk in stream:
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
        async with aclosing(
            self.generate_stream_with_provider(
                messages,
                model=model,
                provider=provider_hint,
                timeout_seconds=timeout_seconds,
                **kwargs,
            )
        ) as stream:
            async for chunk in stream:
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
        async with aclosing(
            self._generate_stream(
                messages,
                model=model,
                timeout_seconds=timeout_seconds,
                provider_hint=provider,
                **kwargs,
            )
        ) as stream:
            async for chunk in stream:
                yield chunk

    async def _generate_stream(  # noqa: C901
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
        started_at = time.perf_counter()
        for attempt in range(1, attempts + 1):
            yielded = False
            model_info: ModelInfo | None = None
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
            except asyncio.CancelledError as exc:
                _log_stream_completed(
                    model_info=model_info,
                    requested_model=model,
                    attempt=attempt,
                    started_at=started_at,
                    outcome="cancelled",
                    error=exc,
                )
                raise
            except GeneratorExit as exc:
                _log_stream_completed(
                    model_info=model_info,
                    requested_model=model,
                    attempt=attempt,
                    started_at=started_at,
                    outcome="cancelled",
                    error=exc,
                )
                raise
            except (LLMModelRoutingError, LLMStreamingUnsupportedError) as exc:
                _log_stream_completed(
                    model_info=model_info,
                    requested_model=model,
                    attempt=attempt,
                    started_at=started_at,
                    outcome="error",
                    error=exc,
                )
                raise
            except Exception as exc:
                if yielded or attempt >= attempts:
                    _log_stream_completed(
                        model_info=model_info,
                        requested_model=model,
                        attempt=attempt,
                        started_at=started_at,
                        outcome="error",
                        error=exc,
                    )
                    raise
                try:
                    await asyncio.sleep(self.config.retry_backoff_seconds * attempt)
                except asyncio.CancelledError as cancel_exc:
                    _log_stream_completed(
                        model_info=model_info,
                        requested_model=model,
                        attempt=attempt,
                        started_at=started_at,
                        outcome="cancelled",
                        error=cancel_exc,
                    )
                    raise
            else:
                _log_stream_completed(
                    model_info=model_info,
                    requested_model=model,
                    attempt=attempt,
                    started_at=started_at,
                    outcome="success",
                )
                return

    def _resolve_provider(
        self,
        model: str,
        provider_hint: ProviderHint | None = None,
    ) -> tuple[ModelInfo, LLMProviderAdapter]:
        if provider_hint is not None and provider_hint not in {"openai", "deepseek"}:
            raise LLMModelRoutingError(f"Unsupported provider hint {provider_hint!r}")
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
                capabilities=(
                    ["json_schema"]
                    if provider_hint == "deepseek"
                    else ["json_schema", "tool_use", "vision"]
                ),
                max_context_tokens=0,
            )
        if model_info.provider not in {"openai", "deepseek"}:
            raise LLMModelRoutingError(
                f"Unsupported model provider {model_info.provider!r}"
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
        operation_name: str,
        provider: str,
        model: str,
        started_at: float | None = None,
    ) -> T:
        attempts = max(1, self.config.max_attempts)
        started_at = time.perf_counter() if started_at is None else started_at
        attempt = 1
        try:
            for attempt in range(1, attempts + 1):
                try:
                    async with asyncio.timeout(timeout_seconds):
                        result = await operation()
                except LLMModelRoutingError as exc:
                    _log_call_completed(
                        operation=operation_name,
                        provider=provider,
                        model=model,
                        attempt=attempt,
                        started_at=started_at,
                        outcome="error",
                        error=exc,
                    )
                    raise
                except Exception as exc:
                    if attempt >= attempts:
                        _log_call_completed(
                            operation=operation_name,
                            provider=provider,
                            model=model,
                            attempt=attempt,
                            started_at=started_at,
                            outcome="error",
                            error=exc,
                        )
                        raise
                    await asyncio.sleep(self.config.retry_backoff_seconds * attempt)
                else:
                    _log_call_completed(
                        operation=operation_name,
                        provider=provider,
                        model=model,
                        attempt=attempt,
                        started_at=started_at,
                        outcome="success",
                    )
                    return result
        except asyncio.CancelledError as exc:
            _log_call_completed(
                operation=operation_name,
                provider=provider,
                model=model,
                attempt=attempt,
                started_at=started_at,
                outcome="cancelled",
                error=exc,
            )
            raise
        raise RuntimeError("unreachable retry state")


def _pop_timeout(kwargs: dict[str, Any], default: float) -> float:
    timeout = kwargs.pop("timeout_seconds", None)
    return default if timeout is None else float(timeout)


def _log_stream_completed(
    *,
    model_info: ModelInfo | None,
    requested_model: str,
    attempt: int,
    started_at: float,
    outcome: str,
    error: BaseException | None = None,
) -> None:
    fields: dict[str, Any] = {
        "provider": model_info.provider if model_info is not None else None,
        "model": model_info.model_id if model_info is not None else requested_model,
        "operation": "generate_stream",
        "attempt": attempt,
        "outcome": outcome,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
    }
    if error is not None:
        fields.update(safe_exception_metadata(error))
        fields["error_code"] = _error_code(error)
    log_method = logger.error if outcome == "error" else logger.info
    log_method("llm_call_completed", extra=fields)


def _log_call_completed(
    *,
    operation: str,
    model: str,
    started_at: float,
    outcome: str,
    provider: str | None = None,
    attempt: int = 1,
    error: BaseException | None = None,
) -> None:
    fields: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "operation": operation,
        "attempt": attempt,
        "outcome": outcome,
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
    }
    if error is not None:
        fields.update(safe_exception_metadata(error))
        fields["error_code"] = _error_code(error)
    log_method = logger.error if outcome == "error" else logger.info
    log_method("llm_call_completed", extra=fields)


def _error_code(exc: BaseException) -> str | int | None:
    return getattr(exc, "code", None) or getattr(exc, "status_code", None)


__all__ = ["LLMGatewayImpl"]
