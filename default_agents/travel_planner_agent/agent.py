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

SYSTEM_PROMPT = """
You are an expert travel assistant specializing in trip planning, destination information, 
and travel recommendations. Your goal is to help users plan enjoyable, safe, and 
realistic trips based on their preferences and constraints.

CRITICAL RULE: When the user's request is missing ANY of these essential details, you MUST
call the AskUserForClarification tool instead of generating a response:
- Destination (where they want to go)
- Duration (how many days/nights)
- Travel dates or time of year
- Budget range
- Number of travelers or group composition
Do NOT write a text response asking for these details — you MUST use the tool.
Only generate a full travel plan when you have at least destination AND duration.

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

            tool_call_started = False
            tool_call_args = ""
            text_chunks: list[str] = []
            async for chunk in model_with_tools.astream(messages):
                if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                    for tc in chunk.tool_call_chunks:
                        if tc.get("name") == "AskUserForClarification":
                            tool_call_started = True
                        if tool_call_started and tc.get("args"):
                            tool_call_args += tc["args"]

                if hasattr(chunk, "content") and chunk.content:
                    text_chunks.append(chunk.content)
                    yield {"content": chunk.content, "done": False}

            if tool_call_started:
                try:
                    args = json.loads(tool_call_args)
                    question = args.get("question", "Could you provide more details?")
                except Exception:
                    question = "Could you provide more details?"
                yield {"content": question, "done": True, "status": "input_required"}
                return

            full_text = "".join(text_chunks).strip()
            looks_like_question = (
                full_text.endswith("?")
                and len(full_text) < 1000
                and not any(
                    kw in full_text.lower()
                    for kw in ["day 1", "itinerary", "here's your", "here is your"]
                )
            )
            if looks_like_question:
                yield {"content": "", "done": True, "status": "input_required"}
            else:
                yield {"content": "", "done": True}

        except Exception as e:
            print(f"error：{e!s}")
            yield {
                "content": "Sorry, an error occurred while processing your request.",
                "done": True,
            }
