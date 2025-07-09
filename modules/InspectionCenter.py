import asyncio
import httpx
import logging
from a2a.types import (
    AgentCard
)

from a2a.client.client import A2ACardResolver
from a2a.client.client import A2AClient

from services.agent_service import AgentService
from services.a2a_service import A2AService

from models.error import AgentNotFoundError
from models.agent import Agent
from models.request import InspectionCenterRequest
from models.response import InspectionCenterResponse


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


class InspectionCenter:

    def __init__(self):
        self.agent_service = AgentService()
        self.a2a_service = A2AService()

    async def inspect_agent_card(self, request: InspectionCenterRequest) -> InspectionCenterResponse:

        # check if agent url is valid
        try:
            agent_url = request.agent_url

            if not agent_url:
                raise AgentNotFoundError(
                    status_code=400,
                    error_code="Agent Fetch Error",
                    error_message="Agent URL is required."
                )
        except Exception:
            logger.warning('Failed to parse JSON from /agent-card request.')
            raise AgentNotFoundError(
                status_code=400,
                error_code="Agent Fetch Error",
                error_message="Agent URL is invalid."
            )

        try:
            async with httpx.AsyncClient(
                timeout=30.0
            ) as client:
                card_resolver = A2ACardResolver(client, agent_url)
                card = await card_resolver.get_agent_card()

            card_data = card.model_dump(exclude_none=False)

            validation_errors = await self.agent_service.validate_agent_card(card_data)
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=validation_errors,
                status_code=200
            )

        except httpx.RequestError as e:
            logger.error(f'Failed to connect to agent at {agent_url}', exc_info=True)
            errorMessage = ["Http RequestError: Failed to connect to agent: {e}"]
            return InspectionCenterResponse(
                agent_url=agent_url,
                result=errorMessage,
                status_code=502
            )
        except Exception as e:
            logger.error('An internal server error occurred', exc_info=True)
            errorMessage = ["InspectionCenter Server Error: An internal server error occurred: {e}"]
            return InspectionCenterResponse(
                agent_url=agent_url,
                result=errorMessage,
                status_code=500
            )

    async def inspect_a2a_connection(self, request: InspectionCenterRequest) -> InspectionCenterResponse:
        # check if agent url is valid
        try:
            agent_url = request.agent_url

            if not agent_url:
                raise AgentNotFoundError(
                    status_code=400,
                    error_code="Agent Fetch Error",
                    error_message="Agent URL is required."
                )
        except Exception:
            logger.warning('Failed to parse JSON from /agent-card request.')
            raise AgentNotFoundError(
                status_code=400,
                error_code="Agent Fetch Error",
                error_message="Agent URL is invalid."
            )

        # initialize a2a client
        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            card_resolver = A2ACardResolver(httpx_client, str(agent_url))
            card = await card_resolver.get_agent_card()
            a2a_client = A2AClient(httpx_client, agent_card=card)

        except Exception as e:
            logger.error(
                f'Failed to initialize a2a client: {e}', exc_info=True
            )
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=["Failed to initialize a2a client"],
                status_code=500
            )
        
        # send a2a client to agent
        inspection_message = "Hello, how are you?"

        try:
            dry_send_message_response = await self.a2a_service.dry_send_message(a2a_client, card, inspection_message)

            if not dry_send_message_response.is_valid:
                return InspectionCenterResponse(
                    agent_url=agent_url,
                    agent_card=card,
                    result=dry_send_message_response.result,
                    status_code=500
                )

            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=dry_send_message_response.result,
                status_code=200
            )
        
        except Exception as e:
            logger.error(f'Failed to send message to agent: {e}', exc_info=True)
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=["Failed to send message to agent"],
                status_code=500
            )