from models.request import AgentCenterRequest
from models.response import AgentCenterResponse
from services.agent_service import agent_service

# Implementation of agent management service


class AgentCenter:
    """
    AgentCenter - Agent Management and Controller Service

    AgentCenter serves as the central management hub for agent-related operations in the multi-agent system.
    It provides comprehensive agent management capabilities and acts as a controller layer for external interface requests.

    Key Responsibilities:
    1. Agent Lifecycle Management:
       - Agent registration and onboarding
       - Agent information updates and modifications
       - Agent removal and deactivation

    2. Agent Discovery and Query:
       - Agent retrieval by ID
       - Similar agent search and matching
       - Complete agent catalog access

    3. External Interface Controller:
       - Provides RESTful API endpoints for agent operations
       - Handles request validation and response formatting
       - Manages cross-service communication and coordination

    4. Business Logic Orchestration:
       - Delegates legacy controller calls to AgentService
       - Implements business rules and validation logic
       - Ensures data consistency and integrity

    Service Dependencies:
    - AgentService: For core agent business logic

    Usage:
    This center is typically used by external clients, other centers (like WorkflowCenter),
    and internal services that need to interact with agents in the system.
    """

    def __init__(self):
        """
        Initialize AgentCenter with required service dependencies.

        Initializes the agent service singleton for business logic.
        """
        self.agent_service = agent_service  # Use singleton

    async def get_agent_card_from_url(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        """
        Get an agent from a URL.
        """
        return await self.agent_service.get_agent_card_from_url(request)

    async def register_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        """
        Register a new agent in the system.

        This legacy adapter delegates to AgentService. AgentService and the
        agent facade handle URL validation and card resolution.

        Args:
            request: AgentCenterRequest containing agent_url and other registration details

        Returns:
            AgentCenterResponse with registration status and agent information

        """
        return await self.agent_service.register_agent(request)

    async def update_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        """
        Update an existing agent's information.

        Allows modification of agent properties, capabilities, and metadata.
        Validates the update request and ensures data consistency.

        Args:
            request: AgentCenterRequest containing updated agent information

        Returns:
            AgentCenterResponse with update status
        """
        return await self.agent_service.update_agent(request)

    async def remove_agent(self, request: AgentCenterRequest) -> AgentCenterResponse:
        """
        Remove an agent from the system.

        Performs agent deactivation and cleanup operations.
        Ensures proper removal of all associated data and references.

        Args:
            request: AgentCenterRequest containing agent_id for removal

        Returns:
            AgentCenterResponse with removal status
        """
        return await self.agent_service.remove_agent(request)

    async def query_agent_by_agent_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        """
        Retrieve a specific agent by its unique identifier.

        Provides detailed agent information including capabilities,
        skills, and current status.

        Args:
            request: AgentCenterRequest containing agent_id

        Returns:
            AgentCenterResponse with agent details or error if not found
        """
        return await self.agent_service.query_agent_by_agent_id(request)

    async def get_all_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        """
        Retrieve all registered agents in the system.

        Returns a comprehensive list of all available agents
        with their basic information and capabilities.

        Args:
            request: AgentCenterRequest (may contain filtering parameters)

        Returns:
            AgentCenterResponse with list of all agents
        """
        return await self.agent_service.get_all_agents(request)

    async def get_all_active_agents(self, request: AgentCenterRequest) -> AgentCenterResponse:
        """
        Retrieve all active agents in the system.

        Returns a list of agents that have active status only,
        filtering out inactive and deleted agents.

        Args:
            request: AgentCenterRequest (may contain filtering parameters)

        Returns:
            AgentCenterResponse with list of active agents only
        """
        return await self.agent_service.get_all_active_agents(request)

    async def get_agents_with_conditions(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        """
        Get agents with conditions from the database.
        """
        return await self.agent_service.get_agents_with_conditions(request)

    async def query_similar_agents(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        """
        Find agents similar to a given query or criteria.

        Uses semantic search and matching algorithms to find
        agents that best match the provided query text or requirements.
        Useful for agent selection and recommendation.

        Args:
            request: AgentCenterRequest containing query_text and agent_count

        Returns:
            AgentCenterResponse with list of similar agents ranked by relevance
        """
        return await self.agent_service.query_similar_agents(request)

    async def get_agents_by_provider_id(
        self, request: AgentCenterRequest
    ) -> AgentCenterResponse:
        """
        Get all agents from a given provider ID.
        Returns a comprehensive list of all available agents by the provider

        Args:
            request: AgentCenterRequest containing provider_id

        Returns:
            AgentCenterResponse with list of all agents
        """
        return await self.agent_service.get_agents_by_provider_id(request)

    def _mask_sensitive_information(
            self, response: AgentCenterResponse, fields: list[str]
    ) -> AgentCenterResponse:
        """
        Remove sensitive fields from an AgentCenterResponse.

        This helper method applies field-level masking based on the
        specified field paths (including nested attributes), and returns
        a sanitized AgentCenterResponse suitable for API output.

        Args:
            response: AgentCenterResponse to be filtered
            fields: list of field paths to mask (e.g., "agent_url", "agent_card.url")

        Returns:
            AgentCenterResponse with the specified fields removed
        """
        return self.agent_service._mask_sensitive_information(response, fields)
