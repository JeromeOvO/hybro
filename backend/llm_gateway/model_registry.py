from dataclasses import dataclass
from typing import Any, Literal

from common.config.settings import settings
from common.dto import ModelInfo
from llm_gateway.config import resolve_generation_provider
from llm_gateway.errors import LLMModelRoutingError


@dataclass(frozen=True, slots=True)
class ModelRouteInfo:
    logical_name: str
    provider: Literal["openai", "deepseek"]
    model_id: str
    api: Literal["chat_completions"]
    supports_native_tools: bool
    supports_provider_strict_schema: bool
    supports_local_structured_action: bool
    context_window: int
    max_output_tokens: int
    default_temperature: float | None
    timeout_seconds: float
    max_provider_retries: int
    supported_thinking_levels: tuple[str, ...]


class ModelRegistryImpl:
    def __init__(
        self,
        settings_obj: Any = None,
        *,
        generation_provider: str | None = None,
    ) -> None:
        self._settings = settings_obj or settings
        self._generation_provider = generation_provider or resolve_generation_provider(
            self._settings
        )
        if self._generation_provider not in {"openai", "deepseek"}:
            raise LLMModelRoutingError(
                f"Unsupported provider {self._generation_provider!r}"
            )
        self._models: dict[str, ModelInfo] = {}
        self._routes: dict[str, ModelRouteInfo] = {}
        self._register_defaults()

    def get_model(self, logical_name: str) -> ModelInfo:
        return self._models[logical_name]

    def get_route_configuration(self, logical_name: str) -> ModelRouteInfo:
        """Resolve capabilities by logical route, never a colliding model alias."""

        try:
            return self._routes[logical_name]
        except KeyError as exc:
            raise LLMModelRoutingError(
                f"No v3 model route configured for {logical_name!r}"
            ) from exc

    def supports_capability(self, model: str, capability: str) -> bool:
        try:
            model_info = self.get_model(model)
        except KeyError:
            return False
        return capability in model_info.capabilities

    def list_models(self, capability: str | None = None) -> list[ModelInfo]:
        unique = {model.logical_name: model for model in self._models.values()}
        models = sorted(unique.values(), key=lambda item: item.logical_name)
        if capability is None:
            return models
        return [model for model in models if capability in model.capabilities]

    def _register_defaults(self) -> None:
        uses_deepseek = self._generation_provider == "deepseek"
        generation_model = self._settings.deepseek_model_name if uses_deepseek else None
        generation_capabilities = (
            ["json_schema"] if uses_deepseek else ["json_schema", "tool_use", "vision"]
        )
        route_specs = (
            ("lead_ai_model", self._settings.lead_ai_model, 128000, 8192, 1),
            (
                "classifier_ai_model",
                self._settings.classifier_ai_model,
                128000,
                4096,
                1,
            ),
            ("context_memory_json_model", "gpt-4o-mini", 128000, 4096, 1),
            (
                "supervisor_model",
                self._settings.supervisor_model or self._settings.lead_ai_model,
                128000,
                8192,
                1,
            ),
        )
        for (
            logical_name,
            fallback_model,
            context_window,
            output_tokens,
            retries,
        ) in route_specs:
            self._register(
                logical_name=logical_name,
                model_id=generation_model or fallback_model,
                provider=self._generation_provider,
                capabilities=generation_capabilities,
                max_context_tokens=context_window,
                max_output_tokens=output_tokens,
                max_provider_retries=retries,
            )
        self._register(
            logical_name="embedding_model",
            model_id=self._settings.embedding_model,
            provider="openai",
            capabilities=["embedding"],
            max_context_tokens=8192,
            route_enabled=False,
        )

    def _register(
        self,
        *,
        logical_name: str,
        model_id: str,
        provider: Literal["openai", "deepseek"],
        capabilities: list[str],
        max_context_tokens: int,
        route_enabled: bool = True,
        max_output_tokens: int = 8192,
        max_provider_retries: int = 1,
        supported_thinking_levels: tuple[str, ...] = (),
    ) -> None:
        model_info = ModelInfo(
            model_id=model_id,
            logical_name=logical_name,
            provider=provider,
            capabilities=capabilities,
            max_context_tokens=max_context_tokens,
        )
        self._models[logical_name] = model_info
        if model_id != logical_name and model_id not in self._models:
            self._models[model_id] = model_info
        if route_enabled:
            self._routes[logical_name] = ModelRouteInfo(
                logical_name=logical_name,
                provider=provider,
                model_id=model_id,
                api="chat_completions",
                supports_native_tools=provider == "openai",
                supports_provider_strict_schema=provider == "openai",
                supports_local_structured_action=provider == "deepseek",
                context_window=max_context_tokens,
                max_output_tokens=max_output_tokens,
                default_temperature=None,
                timeout_seconds=60,
                max_provider_retries=max_provider_retries,
                supported_thinking_levels=supported_thinking_levels,
            )


__all__ = ["ModelRegistryImpl", "ModelRouteInfo"]
