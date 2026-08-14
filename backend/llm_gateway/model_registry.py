from typing import Any

from common.config.settings import settings
from common.dto import ModelInfo
from llm_gateway.config import resolve_generation_provider


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
        self._models: dict[str, ModelInfo] = {}
        self._register_defaults()

    def get_model(self, logical_name: str) -> ModelInfo:
        return self._models[logical_name]

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

    # Phase 2 is intentionally static: model registration is derived from settings
    # until later container/integration phases define runtime extension points.
    def _register_defaults(self) -> None:
        generation_provider = self._generation_provider
        uses_deepseek = generation_provider == "deepseek"
        uses_gemini = generation_provider == "gemini"
        if uses_deepseek:
            generation_capabilities = ["json_schema"]
            generation_model = self._settings.deepseek_model_name
        elif uses_gemini:
            generation_capabilities = ["json_schema", "vision"]
            generation_model = self._settings.gemini_model_name
        else:
            generation_capabilities = ["json_schema", "tool_use", "vision"]
            generation_model = None

        self._register(
            logical_name="lead_ai_model",
            model_id=generation_model or self._settings.lead_ai_model,
            provider=generation_provider,
            capabilities=generation_capabilities,
            max_context_tokens=128000,
        )
        self._register(
            logical_name="classifier_ai_model",
            model_id=generation_model or self._settings.classifier_ai_model,
            provider=generation_provider,
            capabilities=generation_capabilities,
            max_context_tokens=128000,
        )
        self._register(
            logical_name="embedding_model",
            model_id=self._settings.embedding_model,
            provider="openai",
            capabilities=["embedding"],
            max_context_tokens=8192,
        )
        self._register(
            logical_name="context_memory_json_model",
            model_id=generation_model or "gpt-4o-mini",
            provider=generation_provider,
            capabilities=["json_schema"],
            max_context_tokens=128000,
        )
        self._register(
            logical_name="supervisor_model",
            model_id=(
                generation_model
                or self._settings.supervisor_model
                or self._settings.lead_ai_model
            ),
            provider=generation_provider,
            capabilities=(
                ["json_schema"]
                if uses_deepseek or uses_gemini
                else ["json_schema", "tool_use"]
            ),
            max_context_tokens=128000,
        )
        self._register(
            logical_name="gemini_model_name",
            model_id=self._settings.gemini_model_name,
            provider="gemini",
            capabilities=["json_schema", "vision"],
            max_context_tokens=1048576,
        )
        self._register(
            logical_name="gemini_embedding_model_name",
            model_id=self._settings.gemini_embedding_model_name,
            provider="gemini",
            capabilities=["embedding"],
            max_context_tokens=8192,
        )

    def _register(
        self,
        *,
        logical_name: str,
        model_id: str,
        provider: str,
        capabilities: list[str],
        max_context_tokens: int,
    ) -> None:
        model_info = ModelInfo(
            model_id=model_id,
            logical_name=logical_name,
            provider=provider,
            capabilities=capabilities,
            max_context_tokens=max_context_tokens,
        )
        self._models[logical_name] = model_info
        if model_id != logical_name:
            self._models.setdefault(model_id, model_info)


__all__ = ["ModelRegistryImpl"]
