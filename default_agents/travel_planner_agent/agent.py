from __future__ import annotations

import json
import os
from pathlib import Path

from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

try:
    from load_repo_env import load_repo_env
except ImportError:  # Host run: helper lives in default_agents/
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from load_repo_env import load_repo_env
    except ImportError:  # Wheel install: helper is not packaged with the agent.
        from dotenv import load_dotenv

        def load_repo_env(*, start=None):
            load_dotenv()


load_repo_env(start=Path(__file__))

# Resolve config.json relative to this file so it is found regardless of the
# process working directory.
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

SYSTEM_PROMPT = """\
You are an expert travel assistant specializing in trip planning, destination information,
and travel recommendations. Your goal is to help users plan enjoyable, safe, and
realistic trips based on their preferences and constraints.

CRITICAL RULE: When the user's request is missing either Destination (where they want to go)
or Duration (how many days/nights), you MUST call the AskUserForClarification tool instead
of generating a response:
- Destination (where they want to go)
- Duration (how many days/nights)

If BOTH destination and duration are provided (either in the initial query or across prior
turns in the conversation history), you MUST NOT call the AskUserForClarification tool.
Proceed immediately to create a detailed day-by-day itinerary with reasonable assumptions
for dates, budget, and group size.

Do NOT call the AskUserForClarification tool for secondary details (budget, dates, preferences)
if destination and duration are already known.

When providing information:
- Be specific and practical with your advice
- Consider seasonality, budget constraints, and travel logistics
- Highlight cultural experiences and authentic local activities
- Include practical travel tips relevant to the destination
- Format information clearly with headings and bullet points when appropriate

For itineraries:
- Create realistic day-by-day plans that account for travel time between attractions
- Balance popular tourist sites with off-the-beaten-path experiences
- Include approximate timing and practical logistics
- Suggest meal options highlighting local cuisine
- Consider weather, local events, and opening hours in your planning

Always maintain a helpful, enthusiastic but realistic tone and acknowledge
any limitations in your knowledge when appropriate.
"""


class TravelPlannerAgent:
    """travel planner Agent."""

    def __init__(self):
        """Construct the agent WITHOUT validating credentials."""

        self._model: ChatOpenAI | None = None

    def _ensure_model(self) -> ChatOpenAI:
        """Build (once) and return the chat model, validating config lazily."""
        if self._model is not None:
            return self._model

        with open(CONFIG_PATH) as f:
            config = json.load(f)

        api_key_var = config.get("api_key") or "OPENAI_API_KEY"
        api_key = os.getenv(api_key_var)
        if not api_key:
            raise RuntimeError(f"{api_key_var} environment variable not set.")

        self._model = ChatOpenAI(
            model=config.get("model_name") or "gpt-4o",
            base_url=config.get("base_url") or None,
            api_key=api_key,
            temperature=0.7,
        )
        return self._model

    async def stream(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream the response of the large model back to the client.

        Args:
            query: The current user message.
            history: Optional prior conversation turns as
                     [{"role": "user"|"agent", "text": "..."}].
        """
        try:
            model = self._ensure_model()

            class AskUserForClarification(BaseModel):
                """Call this when you need the user to clarify something, like specific dates, cities, or preferences."""

                question: str = Field(description="The question to ask the user")

            model_with_tools = model.bind_tools([AskUserForClarification])

            messages: list = [SystemMessage(content=SYSTEM_PROMPT)]

            if history:
                for turn in history:
                    if turn["role"] == "user":
                        messages.append(HumanMessage(content=turn["text"]))
                    else:
                        messages.append(AIMessage(content=turn["text"]))

            messages.append(HumanMessage(content=query))

            full_response = None
            async for chunk in model_with_tools.astream(messages):
                full_response = (
                    chunk if full_response is None else full_response + chunk
                )
                if hasattr(chunk, "content") and chunk.content:
                    yield {"content": chunk.content, "done": False}

            if full_response:
                tool_calls = getattr(full_response, "tool_calls", None) or []
                for tc in tool_calls:
                    if tc.get("name") == "AskUserForClarification":
                        args = tc.get("args") or {}
                        question = (
                            args.get("question")
                            if isinstance(args, dict)
                            else None
                        )
                        if not isinstance(question, str) or not question.strip():
                            question = "Could you provide more details?"
                        yield {
                            "content": question.strip(),
                            "done": True,
                            "status": "input_required",
                        }
                        return

                invalid_tool_calls = (
                    getattr(full_response, "invalid_tool_calls", None) or []
                )
                for itc in invalid_tool_calls:
                    if itc.get("name") == "AskUserForClarification":
                        yield {
                            "content": "Could you provide more details?",
                            "done": True,
                            "status": "input_required",
                        }
                        return

            yield {"content": "", "done": True}

        except Exception as e:
            print(f"error：{e!s}")
            yield {
                "content": "Sorry, an error occurred while processing your request.",
                "done": True,
            }
