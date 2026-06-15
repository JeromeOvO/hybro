from common.protocols import LLMTextGateway


class DebateLLMService:
    def __init__(
        self,
        llm_provider: LLMTextGateway,
        default_model: str = "lead_ai_model",
    ) -> None:
        self._llm_provider = llm_provider
        self._default_model = default_model

    async def short_debate_with_openai(
        self, original_userinput: str, other_agent_answer: str
    ) -> str:
        return await self._debate_response(
            original_userinput,
            other_agent_answer,
            "Based off the opinion of other agents, can you give an updated response . . .",
        )

    async def long_debate_with_openai(
        self, original_userinput: str, other_agent_answer: str
    ) -> str:
        return await self._debate_response(
            original_userinput,
            other_agent_answer,
            "Using the opinion of other agents as additional advice, can you give an updated response . . .",
        )

    async def _debate_response(
        self,
        original_userinput: str,
        other_agent_answer: str,
        instruction: str,
    ) -> str:
        response = await self._llm_provider.generate(
            [
                {
                    "role": "system",
                    "content": "You are an expert AI agent participating in a debate.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Original user input: {original_userinput}\n\n"
                        "These are the solutions to the problem from other agents: "
                        f"{other_agent_answer}\n{instruction}"
                    ),
                },
            ],
            model=self._default_model,
        )
        return response.content or ""


__all__ = ["DebateLLMService"]
