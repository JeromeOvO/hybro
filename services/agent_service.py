import uuid
from typing import Any
from urllib.parse import urlparse, urlunparse

from common.utils.logger import get_logger
from database.mongodb import get_db
from models.agent import Agent
from models.error import (
    AgentCardRequiredError,
    AgentIdRequiredError,
    AgentNotFoundError,
    IllgalParameterError,
    QueryTextRequiredError,
)
from models.request import AgentCenterRequest
from models.response import AgentCenterResponse
from services.a2a_service import a2a_service
from services.database_service import db_service
from services.domain_alias_service import domain_alias_service
from services.openai_service import openai_service

logger = get_logger(__name__)

# Local host aliases that should be normalized to "localhost"
LOCAL_HOST_ALIASES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def normalize_agent_url(url: str) -> str:
    """
    Normalize an agent URL for consistent comparison.
    - Lowercase the hostname
    - Normalize localhost aliases (127.0.0.1, ::1, 0.0.0.0) to "localhost"
    - Remove default ports (80 for http, 443 for https)
    - Remove trailing slashes from path
    - Remove .well-known paths
    """
    if not url:
        return url

    # Remove well-known paths first
    for well_known_path in ["/.well-known/agent-card.json", "/.well-known/agent.json"]:
        if well_known_path in url:
            url = url.split(well_known_path)[0]

    try:
        parsed = urlparse(url)
    except Exception:
        return url  # Return as-is if parsing fails

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return url  # Invalid URL, return as-is

    # Normalize localhost aliases to canonical "localhost"
    if hostname in LOCAL_HOST_ALIASES:
        hostname = "localhost"

    # Remove default ports
    port = parsed.port
    if (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    ):
        port = None

    # Reconstruct netloc
    netloc = hostname
    if port:
        netloc = f"{hostname}:{port}"

    # Remove trailing slash from path
    path = parsed.path.rstrip("/")

    # Reconstruct URL (preserve query string - some agents may use it)
    normalized = urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            "",  # params
            parsed.query,  # preserve query string
            "",  # fragment
        )
    )

    return normalized


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

        # Check for duplicate using normalized URL from agent_card
        normalized_url = normalize_agent_url(request.agent_card.url)
        existing_agent = await self._find_agent_by_normalized_url(normalized_url)
        if existing_agent:
            return AgentCenterResponse(
                success=False,
                error="Agent with this URL is already registered",
                status_code=400,
            )

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
            logger.info(
                f"AgentCenter: Generated public URL {public_url} for agent {new_agent_id}"
            )
        except Exception as e:
            logger.warning(
                f"AgentCenter: Failed to generate public URL for agent {new_agent_id}: {str(e)}"
            )

        # create agent with public_url and normalized_url
        agent = Agent(
            agent_id=new_agent_id,
            agent_card=request.agent_card,
            provider_id=provider_id,
            public_url=public_url,
            normalized_url=normalized_url,
        )

        # add agent to database
        try:
            agent_add_result = await self.database_service.add_agent(agent)
        except ValueError as e:
            # Handle duplicate key error from database
            if "already registered" in str(e).lower() or "duplicate" in str(e).lower():
                return AgentCenterResponse(
                    success=False,
                    error="Agent with this URL is already registered",
                    status_code=400,
                )
            logger.error(f"AgentCenter: Failed to add agent to database: {str(e)}")
            return AgentCenterResponse(
                agent_id=new_agent_id, success=False, error=str(e), status_code=500
            )
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

    async def _find_agent_by_normalized_url(self, normalized_url: str) -> Agent | None:
        """Find agent by normalized URL - checks both normalized_url field and agent_card.url."""
        mongo_db = await get_db()

        # First try exact match on normalized_url field (for new agents)
        agent_doc = await mongo_db.agents.find_one({"normalized_url": normalized_url})
        if agent_doc:
            return Agent(**agent_doc)

        # Fallback: check agent_card.url for legacy agents without normalized_url
        cursor = mongo_db.agents.find({"normalized_url": {"$exists": False}})
        async for doc in cursor:
            if doc.get("agent_card", {}).get("url"):
                if normalize_agent_url(doc["agent_card"]["url"]) == normalized_url:
                    return Agent(**doc)

        return None

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
            if not agent.is_public and (
                request.user_id is None or agent.provider_id != request.user_id
            ):
                return AgentCenterResponse(
                    agent_id=agent_id,
                    success=False,
                    error="Agent not found",
                    status_code=404,
                )
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
            agents = await self.database_service.get_all_visible_agents(request.user_id)
        except Exception as e:
            logger.error(f"AgentCenter: Failed to get all agents in database: {str(e)}")
            return AgentCenterResponse(success=False, error=str(e), status_code=500)

        return AgentCenterResponse(
            agents=agents, success=True, error=None, status_code=200
        )

    async def get_all_active_agents(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        """
        Get all agents with active status from the database.

        Args:
            request: AgentCenterRequest - may contain user_id for visibility filtering

        Returns:
            AgentCenterResponse with list of active agents only
        """
        try:
            agents = await self.database_service.get_all_active_agents(request.user_id)
        except Exception as e:
            logger.error(
                f"AgentCenter: Failed to get all active agents in database: {str(e)}"
            )
            return AgentCenterResponse(success=False, error=str(e), status_code=500)

        return AgentCenterResponse(
            agents=agents, success=True, error=None, status_code=200
        )

    async def get_agents_with_conditions(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        try:
            agents = await self.database_service.get_agents_with_conditions_visible(
                request.user_id, request.query, request.limit
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
            count = (
                request.agent_count
                if (request.agent_count and request.agent_count > 0)
                else 5
            )
            agents = await self.database_service.query_similar_agents(
                query_text=query_text,
                count=count,
                user_id=request.user_id,
            )
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
        """Extract the root URL from a full agent URL excluding well-known paths and trailing slashes."""

        # remove well-known path (.well-known/agent.json) if present
        if "/.well-known/agent.json" in agent_url:
            return agent_url.split("/.well-known/agent.json")[0]
        # remove trailing slash if present
        if agent_url.endswith("/"):
            return agent_url[:-1]
        return agent_url

    async def get_agent_by_url(self, agent_url: str) -> Agent | None:
        """Get agent by URL using normalized URL matching."""

        if agent_url is None:
            raise IllgalParameterError("agent_url is required")

        normalized_url = normalize_agent_url(agent_url)
        return await self._find_agent_by_normalized_url(normalized_url)

    async def get_agent_by_agent_id(self, agent_id: str) -> Agent | None:
        """Get agent by ID - internal service method"""

        return await self.database_service.get_agent_by_agent_id(agent_id)

    def _mask_sensitive_information(
        self, response: AgentCenterResponse, fields: list[str]
    ) -> AgentCenterResponse:
        """
        Sanitize sensitive fields in AgentCenterResponse.
        Supports:
          - top-level fields (e.g. 'agent_url')
          - nested fields (e.g. 'agent_card.url','agent_card.skills.id')
          - nested agent fields for resp.agent and resp.agents list
        Returns a NEW AgentCenterResponse
        """
        data = response.model_dump()

        def remove_nested_field(obj, path_parts):
            if not path_parts:
                return

            if isinstance(obj, dict):
                if len(path_parts) == 1:
                    obj[path_parts[0]] = ""
                elif path_parts[0] in obj:
                    remove_nested_field(obj[path_parts[0]], path_parts[1:])

            elif isinstance(obj, list):
                for item in obj:
                    remove_nested_field(item, path_parts)

        for field_path in fields:
            parts = field_path.split(".")

            if len(parts) == 1:
                data.pop(parts[0], None)
            else:
                if "agents" in data:
                    remove_nested_field(data["agents"], parts)
                if "agent" in data:
                    remove_nested_field(data["agent"], parts)

        return AgentCenterResponse(**data)


agent_service = AgentService()
