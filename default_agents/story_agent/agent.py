from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from runtime_config import get_config


class StoryAgent:
    """story Agent."""

    def __init__(self):
        """Construct the agent WITHOUT validating credentials."""

        self._model: ChatOpenAI | None = None

    def _ensure_model(self) -> ChatOpenAI:
        """Build (once) and return the chat model, validating config lazily."""
        if self._model is not None:
            return self._model

        config = get_config()
        self._model = ChatOpenAI(
            model=config.text_model,
            base_url=config.base_url,
            api_key=config.token,
            temperature=0.9,
        )
        return self._model

    async def stream(self, query: str) -> AsyncGenerator[dict[str, Any], None]:
        """Stream the response of the large model back to the client."""
        try:
            model = self._ensure_model()

            # Initialize the conversation history (system messages can be added)
            messages = [
                SystemMessage(
                    content="""
                You are a creative storytelling and writing expert with a passion for crafting engaging narratives 
                and helping others develop their writing skills. Your goal is to inspire creativity, provide 
                constructive feedback, and guide writers through the storytelling process.

                When helping with creative writing:
                - Encourage original ideas and unique perspectives
                - Provide guidance on plot structure, character development, and pacing
                - Suggest techniques for creating compelling dialogue and descriptions
                - Help develop themes and emotional depth in stories
                - Offer constructive feedback on writing style and voice

                For storytelling assistance:
                - Help brainstorm plot ideas, characters, and settings
                - Suggest ways to overcome writer's block
                - Provide examples of different narrative techniques and genres
                - Guide through story arcs and character journeys
                - Help with world-building for fantasy and sci-fi stories

                For writing improvement:
                - Offer tips on grammar, style, and clarity
                - Suggest ways to enhance descriptions and imagery
                - Help with transitions and story flow
                - Provide guidance on different writing formats (short stories, novels, scripts)

                Always maintain an encouraging, supportive tone and celebrate creativity while providing 
                helpful guidance. Ask clarifying questions about genre preferences, target audience, or specific goals.
                """
                )
            ]

            # Add the user message to the history.
            messages.append(HumanMessage(content=query))

            # Invoke the model in streaming mode to generate a response.
            async for chunk in model.astream(messages):
                # Return the text content block.
                if hasattr(chunk, "content") and chunk.content:
                    yield {"content": chunk.content, "done": False}
            yield {"content": "", "done": True}

        except Exception as e:
            print(f"error: {e!s}")
            yield {
                "content": "Sorry, an error occurred while processing your request.",
                "done": True,
            }
