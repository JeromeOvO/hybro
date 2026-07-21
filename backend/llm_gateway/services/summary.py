from collections.abc import AsyncIterator

from common.dto import RoomMessageSummary
from common.prompts.markdown_response_format import HYBRO_MARKDOWN_RESPONSE_FORMAT
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
            "You are HYBRO AI writing the final answer to the user's request from "
            "multi-agent evidence. Answer the user's goal directly instead of "
            "reporting that agents were called. Lead with the requested outcome; "
            "include only useful facts, caveats, and next actions. Do not copy full "
            "artifacts or JSON unless explicitly requested, and never expose internal "
            "task labels, dispatch text, step numbers, or strings such as "
            "'Requesting ...'. Preserve agent attribution only when it helps the user.\n\n"
            + HYBRO_MARKDOWN_RESPONSE_FORMAT
        )
        if mode == "debate":
            system_prompt = (
                "You are HYBRO AI summarizing a multi-agent debate. Compare "
                "perspectives, agreements, disagreements, and actionable conclusions.\n\n"
                + HYBRO_MARKDOWN_RESPONSE_FORMAT
            )
        user_prompt = (
            f"The user asked: {user_question or 'Not provided'}\n\n"
            f"Agent responses:\n{answers}\n\n"
            "Write the final answer that best fulfills the user's request."
        )
        return self._llm_provider.generate_stream(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model="lead_ai_model",
        )
