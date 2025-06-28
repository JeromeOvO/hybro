import asyncio
import httpx
import logging
from a2a.types import (
    AgentCard
)

from a2a.client.client import A2ACardResolver

from services.agent_service import AgentService
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

    async def inspect(self, request: InspectionCenterRequest) -> InspectionCenterResponse:

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

