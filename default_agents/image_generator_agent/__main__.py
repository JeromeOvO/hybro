"""This file serves as the main entry point for the application.

It initializes the A2A server, defines the agent's capabilities,
and starts the server to handle incoming requests.
"""

import logging
import os

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, TransportProtocol
from agent import ImageGenerationAgent
from agent_executor import ImageGenerationAgentExecutor
from starlette.applications import Starlette

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_app() -> Starlette:
    """Builds the A2A application: agent card + JSON-RPC endpoint."""
    port = int(os.getenv("SERVER_PORT", "6002"))
    server_domain = os.getenv("SERVER_DOMAIN", "localhost")

    capabilities = AgentCapabilities(streaming=False)
    skill = AgentSkill(
        id="image_generator",
        name="Image Generator",
        description=(
            "Generate stunning, high-quality images on demand and leverage"
            " powerful editing capabilities to modify, enhance, or completely"
            " transform visuals."
        ),
        tags=["generate image", "edit image"],
        examples=["Generate a photorealistic image of raspberry lemonade"],
    )

    agent_host_url = f"http://{server_domain}:{port}/"
    agent_card = AgentCard(
        name="Image Generator Agent",
        description=(
            "Generate stunning, high-quality images on demand and leverage"
            " powerful editing capabilities to modify, enhance, or completely"
            " transform visuals."
        ),
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version=PROTOCOL_VERSION_1_0,
                url=agent_host_url,
            )
        ],
        version="1.0.0",
        default_input_modes=ImageGenerationAgent.SUPPORTED_CONTENT_TYPES,
        default_output_modes=ImageGenerationAgent.SUPPORTED_CONTENT_TYPES,
        capabilities=capabilities,
        skills=[skill],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=ImageGenerationAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    return Starlette(
        routes=create_agent_card_routes(agent_card)
        + create_jsonrpc_routes(request_handler, rpc_url="/")
    )


def main():
    """Entry point for the A2A + OpenAI Image generation agent."""
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "6002"))

    try:
        import uvicorn

        uvicorn.run(build_app(), host=host, port=port)

    except Exception as e:
        logger.error(f"An error occurred during server startup: {e}")
        exit(1)


if __name__ == "__main__":
    main()
