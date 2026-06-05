from collections.abc import AsyncIterator

from common.dto import RoomMessageSummary
from common.protocols import LLMStreamGateway


class SummaryLLMService:
    def __init__(self, llm_provider: LLMStreamGateway) -> None:
        self._llm_provider = llm_provider

    def summarize_agent_responses_stream(
        self,
        agent_responses: list[RoomMessageSummary],
        mode: str = "non_debate",
        user_question: str | None = None,
    ) -> AsyncIterator[str]:
        answers = "\n\n".join(
            f"--- {item.agent_name or 'Unknown Agent'} ---\n{item.message}"
            for item in agent_responses
        )
        system_prompt = (
            "You are HYBRO AI synthesizing multi-agent responses. Preserve useful "
            "agent attribution and return a concise user-facing answer."
        )
        if mode == "debate":
            system_prompt = (
                "You are HYBRO AI summarizing a multi-agent debate. Compare "
                "perspectives, agreements, disagreements, and actionable conclusions."
            )
        user_prompt = (
            f"The user asked: {user_question or 'Not provided'}\n\n"
            f"Agent responses:\n{answers}\n\n"
            "Return the final response for the user."
        )
        return self._llm_provider.generate_stream(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model="lead_ai_model",
        )
