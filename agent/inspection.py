from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from a2a_adapter import inspection as a2a_inspection
from agent.card_validation import validate_agent_card_data
from common.utils.logger import get_logger
from models.error import AgentNotFoundError
from models.request import InspectionCenterRequest
from models.response import InspectionCenterResponse

logger = get_logger(__name__)


AgentCardValidator = Callable[[dict[str, Any]], Awaitable[list[str]]]


class AgentInspectionService:
    def __init__(self, validator: AgentCardValidator | None = None) -> None:
        self._validate_agent_card = validator or validate_agent_card_data

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
            card = await a2a_inspection.fetch_agent_card_for_inspection(agent_url)
            validation_errors = await self._validate_agent_card(
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

        try:
            response = await a2a_inspection.inspect_a2a_connection(str(agent_url))
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=response.get("agent_card"),
                result=response.get("result") or [],
                status_code=response.get("status_code", 500),
            )
        except Exception as e:
            logger.error("Failed to initialize a2a client: %s", e, exc_info=True)
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=None,
                result=[f"Failed to initialize a2a client: {e}"],
                status_code=500,
            )


AppShellInspectionCenter = AgentInspectionService

__all__ = ["AgentInspectionService", "AppShellInspectionCenter"]
