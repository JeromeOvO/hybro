"""This file serves as the main entry point for the application.

It initializes the A2A server, defines the agent's capabilities,
and starts the server to handle incoming requests.
"""

import logging
import os
from pathlib import Path

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from agent import ImageGenerationAgent
from agent_executor import ImageGenerationAgentExecutor

try:
    from load_repo_env import load_repo_env
except ImportError:  # Host run: module lives in default_agents/
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from load_repo_env import load_repo_env

load_repo_env(start=Path(__file__))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Entry point for the A2A + OpenAI Image generation agent."""
    host = os.getenv('SERVER_HOST', '0.0.0.0')
    port = int(os.getenv('SERVER_PORT', '6002'))
    server_domain = os.getenv('SERVER_DOMAIN', 'localhost')

    try:
        capabilities = AgentCapabilities(streaming=False)
        skill = AgentSkill(
            id='image_generator',
            name='Image Generator',
            description=(
                'Generate stunning, high-quality images on demand and leverage'
                ' powerful editing capabilities to modify, enhance, or completely'
                ' transform visuals.'
            ),
            tags=['generate image', 'edit image'],
            examples=['Generate a photorealistic image of raspberry lemonade'],
        )

        agent_host_url = f'http://{server_domain}:{port}'
        agent_card = AgentCard(
            name='Image Generator Agent',
            description=(
                'Generate stunning, high-quality images on demand and leverage'
                ' powerful editing capabilities to modify, enhance, or completely'
                ' transform visuals.'
            ),
            url=agent_host_url,
            version='1.0.0',
            default_input_modes=ImageGenerationAgent.SUPPORTED_CONTENT_TYPES,
            default_output_modes=ImageGenerationAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        request_handler = DefaultRequestHandler(
            agent_executor=ImageGenerationAgentExecutor(),
            task_store=InMemoryTaskStore(),
        )
        server = A2AStarletteApplication(
            agent_card=agent_card, http_handler=request_handler
        )
        import uvicorn

        uvicorn.run(server.build(), host=host, port=port)

    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
        exit(1)


if __name__ == '__main__':
    main()
