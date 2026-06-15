from common.protocols import LLMTextGateway
from llm_gateway.errors import LLMModelRoutingError


class DiscoveryLLMService:
    def __init__(
        self,
        llm_provider: LLMTextGateway,
        max_expansion_words: int = 5,
    ) -> None:
        self._llm_provider = llm_provider
        self._max_expansion_words = max_expansion_words

    async def expand_query_for_discovery(self, query: str) -> str:
        query = query.strip()
        if len(query.split()) > self._max_expansion_words:
            return query
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a query expansion assistant for an AI agent discovery "
                    "system. Return only the expanded query."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Expand this query for better agent discovery with synonyms, "
                    f"related terms, and use case context:\n{query}"
                ),
            },
        ]
        try:
            response = await self._llm_provider.generate(
                messages,
                model="lead_ai_model",
                temperature=0.3,
                max_tokens=100,
            )
        except LLMModelRoutingError:
            raise
        except Exception:
            return query
        expanded = response.content.strip()
        if not expanded or len(expanded) < len(query):
            return query
        return expanded
