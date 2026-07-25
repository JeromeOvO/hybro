from common.config.settings import settings
from common.dto import ModelInfo


class ModelRegistryImpl:
    def __init__(self) -> None:
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
        self._register(
            logical_name="lead_ai_model",
            model_id=settings.lead_ai_model,
            provider="openai",
            capabilities=["json_schema", "tool_use", "vision"],
            max_context_tokens=128000,
        )
        self._register(
            logical_name="classifier_ai_model",
            model_id=settings.classifier_ai_model,
            provider="openai",
            capabilities=["json_schema", "tool_use", "vision"],
            max_context_tokens=128000,
        )
        self._register(
            logical_name="embedding_model",
            model_id=settings.embedding_model,
            provider="openai",
            capabilities=["embedding"],
            max_context_tokens=8192,
        )
        self._register(
            logical_name="context_memory_legacy_json_model",
            model_id="gpt-4o-mini",
            provider="openai",
            capabilities=["json_schema"],
            max_context_tokens=128000,
        )
        self._register(
            logical_name="supervisor_model",
            model_id=settings.supervisor_model or settings.lead_ai_model,
            provider="openai",
            capabilities=["json_schema", "tool_use"],
            max_context_tokens=128000,
        )
        self._register(
            logical_name="gemini_model_name",
            model_id=settings.gemini_model_name,
            provider="gemini",
            capabilities=["json_schema", "vision"],
            max_context_tokens=1048576,
        )
        self._register(
            logical_name="gemini_embedding_model_name",
            model_id=settings.gemini_embedding_model_name,
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
