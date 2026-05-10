from common.config.settings import settings
from common.dto import LLMResponse, LLMStructuredResponse, ModelInfo

from .model_registry import ModelRegistryImpl
from .providers import BedrockProvider, GeminiProvider, OpenAIProvider


class LLMGatewayImpl:
    def __init__(
        self,
        model_registry: ModelRegistryImpl | None = None,
        providers: dict[str, object] | None = None,
    ) -> None:
        self._model_registry = model_registry or ModelRegistryImpl()
        self._providers = providers or {
            "openai": OpenAIProvider(),
            "gemini": GeminiProvider(),
            "bedrock": BedrockProvider(),
        }

    async def generate(
        self,
        messages: list[dict],
        model: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        model_info, provider = self._provider_for(model or settings.lead_ai_model)
        return await provider.generate(messages, model=model_info.model_id, **kwargs)

    async def generate_structured(
        self,
        messages: list[dict],
        schema: dict,
        model: str | None = None,
        **kwargs,
    ) -> LLMStructuredResponse:
        model_info, provider = self._provider_for(model or settings.lead_ai_model)
        return await provider.generate_structured(
            messages,
            schema,
            model=model_info.model_id,
            **kwargs,
        )

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        model_info, provider = self._provider_for(model or settings.embedding_model)
        if "embedding" not in model_info.capabilities:
            raise ValueError(f"Model {model_info.logical_name} does not support embeddings")
        return await provider.embed(text, model=model_info.model_id)

    async def embed_batch(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]:
        model_info, provider = self._provider_for(model or settings.embedding_model)
        if "embedding" not in model_info.capabilities:
            raise ValueError(f"Model {model_info.logical_name} does not support embeddings")
        return await provider.embed_batch(texts, model=model_info.model_id)

    def _provider_for(self, model: str) -> tuple[ModelInfo, object]:
        model_info = self._model_registry.get_model(model)
        provider = self._providers.get(model_info.provider)
        if provider is None:
            raise RuntimeError(f"No provider configured for {model_info.provider}")
        return model_info, provider


__all__ = ["LLMGatewayImpl"]
