from __future__ import annotations

import httpx
from a2a.client import A2AClient

from common.utils.logger import get_logger
from models.error import AgentNotFoundError
from models.request import InspectionCenterRequest
from models.response import InspectionCenterResponse
from app_shell.a2a_runtime import a2a_service
from app_shell.agent_service import agent_service

logger = get_logger(__name__)


class AppShellInspectionCenter:
    def __init__(self, *, agent_service_dep=None, a2a_service_dep=None):
        self.agent_service = agent_service_dep or agent_service
        self.a2a_service = a2a_service_dep or a2a_service

    async def inspect_agent_card(
        self, request: InspectionCenterRequest
    ) -> InspectionCenterResponse:
        try:
            agent_url = request.agent_url
            if not agent_url:
                raise AgentNotFoundError(
                    status_code=400,
                    error_code="Agent Fetch Error",
                    error_message="Agent URL is required.",
                )
        except Exception:
            logger.warning("Failed to parse JSON from /agent-card request.")
            raise AgentNotFoundError(
                status_code=400,
                error_code="Agent Fetch Error",
                error_message="Agent URL is invalid.",
            ) from None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                card = await self.a2a_service._fetch_agent_card_with_fallback(
                    client, agent_url
                )
            validation_errors = await self.agent_service.validate_agent_card(
                card.model_dump(exclude_none=False)
            )
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=validation_errors,
                status_code=200,
            )
        except httpx.RequestError as e:
            logger.error("Failed to connect to agent at %s", agent_url, exc_info=True)
            return InspectionCenterResponse(
                agent_url=agent_url,
                result=[f"Http RequestError: Failed to connect to agent: {e}"],
                status_code=502,
            )
        except Exception as e:
            logger.error("An internal server error occurred", exc_info=True)
            return InspectionCenterResponse(
                agent_url=agent_url,
                result=[
                    "InspectionCenter Server Error: "
                    f"An internal server error occurred: {e}"
                ],
                status_code=500,
            )

    async def inspect_a2a_connection(
        self, request: InspectionCenterRequest
    ) -> InspectionCenterResponse:
        try:
            agent_url = request.agent_url
            if not agent_url:
                raise AgentNotFoundError(
                    status_code=400,
                    error_code="Agent Fetch Error",
                    error_message="Agent URL is required.",
                )
        except Exception:
            logger.warning("Failed to parse JSON from /agent-card request.")
            raise AgentNotFoundError(
                status_code=400,
                error_code="Agent Fetch Error",
                error_message="Agent URL is invalid.",
            ) from None

        async with httpx.AsyncClient(timeout=600.0) as httpx_client:
            try:
                card = await self.a2a_service._fetch_agent_card_with_fallback(
                    httpx_client, str(agent_url)
                )
                a2a_client = A2AClient(httpx_client, agent_card=card)
            except Exception as e:
                logger.error("Failed to initialize a2a client: %s", e, exc_info=True)
                return InspectionCenterResponse(
                    agent_url=agent_url,
                    agent_card=None,
                    result=[f"Failed to initialize a2a client: {e}"],
                    status_code=500,
                )

            try:
                response = await self.a2a_service.dry_send_message(
                    a2a_client, card, "Hello, how are you?"
                )
                return InspectionCenterResponse(
                    agent_url=agent_url,
                    agent_card=card,
                    result=response.result,
                    status_code=200 if response.is_valid else 500,
                )
            except Exception:
                logger.error("Failed to send message to agent", exc_info=True)
                return InspectionCenterResponse(
                    agent_url=agent_url,
                    agent_card=card,
                    result=["Failed to send message to agent"],
                    status_code=500,
                )


__all__ = ["AppShellInspectionCenter"]
