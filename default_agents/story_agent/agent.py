import json
import os
import sys

from collections.abc import AsyncGenerator
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


load_dotenv()


class StoryAgent:
    """story Agent."""

    def __init__(self):
        """Initialize the story dialogue model"""
        try:
            with open('config.json') as f:
                config = json.load(f)
            if not os.getenv(config['api_key']):
                print(f'{config["api_key"]} environment variable not set.')
                sys.exit(1)
            api_key = os.getenv(config['api_key'])

            self.model = ChatOpenAI(
                model=config['model_name'] or 'gpt-4o',
                base_url=config['base_url'] or None,
                api_key=api_key,  # type: ignore
                temperature=0.7,  # Control the generation randomness (0-2, higher values indicate greater randomness)
            )
        except FileNotFoundError:
            print('Error: The configuration file config.json cannot be found.')
            sys.exit()
        except KeyError as e:
            print(f'The configuration file is missing required fields: {e}')
            sys.exit()

    async def stream(self, query: str) -> AsyncGenerator[dict[str, Any], None]:
        """Stream the response of the large model back to the client."""
        try:
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
            async for chunk in self.model.astream(messages):
                # Return the text content block.
                if hasattr(chunk, 'content') and chunk.content:
                    yield {'content': chunk.content, 'done': False}
            yield {'content': '', 'done': True}

        except Exception as e:
            print(f'error：{e!s}')
            yield {
                'content': 'Sorry, an error occurred while processing your request.',
                'done': True,
            }
