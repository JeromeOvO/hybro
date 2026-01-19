import re
import uuid
from typing import Any

from common.utils.logger import get_logger
from database.mongodb import get_db
from models.agent import Agent
from models.error import (
    AgentCardRequiredError,
    AgentIdRequiredError,
    AgentNotFoundError,
    IllgalParameterError,
    QueryTextRequiredError, ProviderIdRequiredError,
)
from models.request import AgentCenterRequest
from models.response import AgentCenterResponse
from services.a2a_service import a2a_service
from services.database_service import db_service
from services.domain_alias_service import domain_alias_service
from services.openai_service import openai_service

logger = get_logger(__name__)


class AgentService:
    def __init__(self):
        self.database_service = db_service  # Use singleton
        self.openai_service = openai_service  # Use singleton
        self.a2a_service = a2a_service  # Use singleton

    async def get_agent_card_from_url(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        agent_url = request.agent_url
        if not agent_url:
            raise IllgalParameterError()

        try:
            agent_card = await self.a2a_service.get_agent_card_from_url(agent_url)
        except Exception as e:
            logger.error(f"AgentCenter: Failed to get agent card from url: {str(e)}")
            return AgentCenterResponse(success=False, error=str(e), status_code=500)

        return AgentCenterResponse(
            agent_card=agent_card, success=True, error=None, status_code=200
        )

    async def register_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        # check if agent_card is provided
        if request.agent_card is None:
            raise AgentCardRequiredError()

        new_agent_id = str(uuid.uuid4())
        provider_id = request.provider_id

        # Generate public (masked) URL for the agent
        public_url = None
        try:
            public_url = await domain_alias_service.generate_public_url(
                agent_name=request.agent_card.name,
                agent_id=new_agent_id,
                preferred_subdomain=getattr(request, "preferred_subdomain", None),
            )
            logger.info(f"AgentCenter: Generated public URL {public_url} for agent {new_agent_id}")
        except Exception as e:
            logger.warning(f"AgentCenter: Failed to generate public URL for agent {new_agent_id}: {str(e)}")

        # create agent with public_url
        agent = Agent(
            agent_id=new_agent_id,
            agent_card=request.agent_card,
            provider_id=provider_id,
            public_url=public_url,
        )

        # add agent to database
        try:
            agent_add_result = await self.database_service.add_agent(agent)
        except Exception as e:
            logger.error(f"AgentCenter: Failed to add agent to database: {str(e)}")
            return AgentCenterResponse(
                agent_id=new_agent_id, success=False, error=str(e), status_code=500
            )

        return AgentCenterResponse(
            agent_id=new_agent_id,
            provider_id=provider_id,
            agent=agent,
            success=agent_add_result,
            error=None,
            status_code=200,
            public_url=public_url,
        )

    async def update_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        agent_id = request.agent_id
        if agent_id is None:
            raise AgentIdRequiredError()

        # update the whole agent
        if request.agent:
            agent = request.agent

            try:
                agent_update_result = (
                    await self.database_service.update_agent_by_agent_id(
                        agent_id, agent
                    )
                )
            except Exception as e:
                logger.error(
                    f"AgentCenter: Failed to update agent in database: {str(e)}"
                )
                return AgentCenterResponse(
                    agent_id=agent_id, success=False, error=str(e), status_code=500
                )

            return AgentCenterResponse(
                agent_id=agent_id,
                agent=agent,
                success=agent_update_result,
                error=None,
                status_code=200,
            )

        # update the agent card
        if request.agent_card:
            agent_card = request.agent_card

            try:
                agent_update_result = (
                    await self.database_service.update_agent_agent_card_by_agent_id(
                        agent_id, agent_card
                    )
                )
            except Exception as e:
                logger.error(
                    f"AgentCenter: Failed to update agent card in database: {str(e)}"
                )
                return AgentCenterResponse(
                    agent_id=agent_id, success=False, error=str(e), status_code=500
                )

            return AgentCenterResponse(
                agent_id=agent_id,
                agent_card=agent_card,
                success=agent_update_result,
                error=None,
                status_code=200,
            )

    async def remove_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        agent_id = request.agent_id
        if agent_id is None:
            raise AgentIdRequiredError()

        try:
            agent_delete_result = await self.database_service.delete_agent_by_agent_id(
                agent_id
            )
        except Exception as e:
            logger.error(f"AgentCenter: Failed to delete agent in database: {str(e)}")
            return AgentCenterResponse(
                agent_id=agent_id, success=False, error=str(e), status_code=500
            )

        return AgentCenterResponse(
            agent_id=agent_id, success=agent_delete_result, error=None, status_code=200
        )

    async def query_agent_by_agent_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        agent_id = request.agent_id
        if agent_id is None:
            raise AgentIdRequiredError()

        try:
            agent = await self.database_service.get_agent_by_agent_id(agent_id)
            if not agent:
                raise AgentNotFoundError()
        except Exception as e:
            logger.error(f"AgentCenter: Failed to get agent in database: {str(e)}")
            return AgentCenterResponse(
                agent_id=agent_id, success=False, error=str(e), status_code=500
            )

        return AgentCenterResponse(
            agent_id=agent.agent_id,
            agent=agent,
            success=True,
            error=None,
            status_code=200,
        )

    async def get_agents_by_provider_id(
            self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        provider_id = request.provider_id
        if not provider_id:
            return AgentCenterResponse(
                success=False,
                error="provider_id is required",
                status_code=400,
            )

        try:
            agents = await self.database_service.get_agents_by_provider_id(provider_id)
        except Exception as e:
            logger.error(
                f"AgentCenter: Failed to get agents by provider_id {provider_id}: {str(e)}"
            )
            return AgentCenterResponse(success=False, error=str(e), status_code=500)

        return AgentCenterResponse(
            agents=agents,
            success=True,
            error=None,
            status_code=200,
        )

    async def get_all_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        try:
            agents = await self.database_service.get_all_agents()
        except Exception as e:
            logger.error(f"AgentCenter: Failed to get all agents in database: {str(e)}")
            return AgentCenterResponse(success=False, error=str(e), status_code=500)

        return AgentCenterResponse(
            agents=agents, success=True, error=None, status_code=200
        )

    async def get_all_active_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        """
        Get all agents with active status from the database.
        
        Args:
            request: AgentCenterRequest (unused but kept for consistency)
            
        Returns:
            AgentCenterResponse with list of active agents only
        """
        try:
            agents = await self.database_service.get_all_active_agents()
        except Exception as e:
            logger.error(f"AgentCenter: Failed to get all active agents in database: {str(e)}")
            return AgentCenterResponse(success=False, error=str(e), status_code=500)

        return AgentCenterResponse(
            agents=agents, success=True, error=None, status_code=200
        )

    async def get_agents_with_conditions(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        try:
            agents = await self.database_service.get_agents_with_conditions(
                request.query, request.limit
            )
        except Exception as e:
            logger.error(
                f"AgentCenter: Failed to get agents with conditions in database: {str(e)}"
            )
            return AgentCenterResponse(success=False, error=str(e), status_code=500)

        return AgentCenterResponse(
            agents=agents, success=True, error=None, status_code=200
        )

    async def query_similar_agents(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        query_text = request.query_text
        if query_text is None:
            raise QueryTextRequiredError()

        if request.agent_count is not None and request.agent_count <= 0:
            raise IllgalParameterError()

        try:
            if request.agent_count is not None and request.agent_count > 0:
                agents = await self.database_service.query_similar_agents(
                    query_text, request.agent_count
                )
            else:
                agents = await self.database_service.query_similar_agents(query_text, 5)
        except Exception as e:
            logger.error(
                f"AgentCenter: Failed to get similar agents in database: {str(e)}"
            )
            return AgentCenterResponse(success=False, error=str(e), status_code=500)

        return AgentCenterResponse(
            agents=agents, success=True, error=None, status_code=200
        )

    # agent validate service
    async def validate_agent_card(self, card_data: dict[str, Any]) -> list[str]:
        """Validate the structure and fields of an agent card."""
        errors: list[str] = []

        # Use a frozenset for efficient checking and to indicate immutability.
        required_fields = frozenset(
            [
                "name",
                "description",
                "url",
                "version",
                "capabilities",
                "defaultInputModes",
                "defaultOutputModes",
                "skills",
            ]
        )

        # Check for the presence of all required fields
        for field in required_fields:
            if field not in card_data:
                errors.append(f"Required field is missing: '{field}'.")

        # Check if 'url' is an absolute URL (basic check)
        if "url" in card_data and not (
            card_data["url"].startswith("http://")
            or card_data["url"].startswith("https://")
        ):
            errors.append(
                "Field 'url' must be an absolute URL starting with http:// or https://."
            )

        # Check if capabilities is a dictionary
        if "capabilities" in card_data and not isinstance(
            card_data["capabilities"], dict
        ):
            errors.append("Field 'capabilities' must be an object.")

        # Check if defaultInputModes and defaultOutputModes are arrays of strings
        for field in ["defaultInputModes", "defaultOutputModes"]:
            if field in card_data:
                if not isinstance(card_data[field], list):
                    errors.append(f"Field '{field}' must be an array of strings.")
                elif not all(isinstance(item, str) for item in card_data[field]):
                    errors.append(f"All items in '{field}' must be strings.")

        # Check skills array
        if "skills" in card_data:
            if not isinstance(card_data["skills"], list):
                errors.append("Field 'skills' must be an array of AgentSkill objects.")
            elif not card_data["skills"]:
                errors.append(
                    "Field 'skills' array is empty. Agent must have at least one skill if it performs actions."
                )

        return errors

    async def get_agent_url_by_agent_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        agent_id = request.agent_id
        if agent_id is None:
            raise AgentIdRequiredError()

        agent_query_result = await self.database_service.get_agent_by_agent_id(agent_id)
        if agent_query_result is None:
            return AgentCenterResponse(
                success=False, error="Agent not found", status_code=404
            )

        return AgentCenterResponse(
            agent_url=agent_query_result.agent_card.url,
            success=True,
            error=None,
            status_code=200,
        )

    def get_agent_root_url(self, agent_url: str) -> str:
        """Extract the root URL from a full agent URL escluding well-known paths and trailing slashes."""

        # remove well-known path (.well-known/agent.json) if present
        if "/.well-known/agent.json" in agent_url:
            return agent_url.split("/.well-known/agent.json")[0]
        # remove trailing slash if present
        if agent_url.endswith("/"):
            return agent_url[:-1]
        return agent_url

    async def get_agent_by_url(self, agent_url: str) -> AgentCenterResponse:
        """Get agent by URL."""

        if agent_url is None:
            raise IllgalParameterError("agent_url is required")

        root_url = self.get_agent_root_url(agent_url)
        escaped_root_url = re.escape(root_url)
        mongo_db = await get_db()
        agent_query_result = await mongo_db.agents.find_one(
            {"agent_card.url": {"$regex": escaped_root_url}}
        )
        if agent_query_result is None:
            return None

        return agent_query_result

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None:
        """Get agent by ID - internal service method"""

        return await self.database_service.get_agent_by_agent_id(agent_id)



agent_service = AgentService()
