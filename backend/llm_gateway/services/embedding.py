from common.protocols import LLMEmbeddingGateway


class EmbeddingLLMService:
    def __init__(self, llm_provider: LLMEmbeddingGateway) -> None:
        self._llm_provider = llm_provider

    async def get_embedding(
        self,
        text: str,
        target_dim: int | None = None,
        *,
        model: str = "embedding_model",
    ) -> list[float] | None:
        embedding = await self._llm_provider.embed(text, model=model)
        if target_dim is not None and target_dim > 0:
            return _resize_embedding(embedding, target_dim)
        return embedding


def _resize_embedding(embedding: list[float], target_dim: int) -> list[float]:
    if len(embedding) == target_dim:
        return embedding
    if len(embedding) > target_dim:
        return embedding[:target_dim]
    return embedding + [0.0] * (target_dim - len(embedding))
