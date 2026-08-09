from common.dto import RoomMemoryGenerationInput
from common.protocols import LLMTextGateway


class RoomMemoryLLMService:
    def __init__(
        self,
        llm_provider: LLMTextGateway,
        default_model: str = "lead_ai_model",
    ) -> None:
        self._llm_provider = llm_provider
        self._default_model = default_model

    async def generate_room_memory_content(
        self, request: RoomMemoryGenerationInput
    ) -> str:
        system_prompt = (
            "You are an expert room memory content generator for multi-agent "
            "conversation rooms. Create and maintain a comprehensive memory "
            "summary from agent interactions."
        )
        message_summaries = "\n\n".join(
            f"Agent: {message.agent_name}\nContent: {message.message}"
            for message in request.messages
        )
        prompt_parts = ["**RECENT AGENT MESSAGES:**", message_summaries]
        if request.existing_memory and request.existing_memory.strip():
            prompt_parts.extend(
                [
                    "",
                    "**EXISTING ROOM MEMORY:**",
                    request.existing_memory,
                    "",
                    "**TASK:** Update the room memory with the recent messages.",
                ]
            )
        else:
            prompt_parts.extend(
                [
                    "",
                    "**TASK:** Create an initial room memory summary.",
                ]
            )
        return await self._generate_text(system_prompt, "\n".join(prompt_parts))

    async def _generate_text(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._llm_provider.generate(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=self._default_model,
        )
        return response.content.strip()


__all__ = ["RoomMemoryLLMService"]
