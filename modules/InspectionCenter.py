import logging

import httpx
from a2a.client.client import A2ACardResolver, A2AClient

from models.error import AgentNotFoundError
from models.request import InspectionCenterRequest
from models.response import InspectionCenterResponse
from services.a2a_service import A2AService
from services.agent_service import AgentService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class InspectionCenter:
    """
    InspectionCenter - Agent Validation and Health Check Service

    InspectionCenter serves as the central hub for agent validation, health monitoring,
    and quality assurance in the multi-agent system. It provides comprehensive
    inspection capabilities and acts as a controller layer for external interface requests.

    Key Responsibilities:
    1. Agent Card Validation:
       - Validates agent card format and structure
       - Checks agent capabilities and skill definitions
       - Verifies agent metadata and configuration
       - Ensures compliance with A2A protocol standards

    2. Agent Health Monitoring:
       - Performs connectivity tests to agent endpoints
       - Validates agent availability and responsiveness
       - Monitors agent performance and response times
       - Detects agent failures and issues

    3. A2A Protocol Compliance:
       - Tests A2A protocol implementation
       - Validates message exchange capabilities
       - Verifies agent communication standards
       - Ensures proper error handling and responses

    4. External Interface Controller:
       - Provides RESTful API endpoints for inspection operations
       - Handles inspection request validation and response formatting
       - Manages cross-service communication for inspection coordination

    5. Quality Assurance:
       - Ensures agent quality and reliability
       - Validates agent functionality and capabilities
       - Monitors agent performance metrics
       - Provides detailed inspection reports

    6. Pre-deployment Validation:
       - Validates agents before system integration
       - Ensures agents meet system requirements
       - Performs compatibility checks
       - Validates security and authentication

    Service Dependencies:
    - AgentService: For agent card validation and business logic
    - A2AService: For A2A protocol testing and communication
    - HTTPX Client: For network connectivity testing
    - A2ACardResolver: For agent card retrieval and parsing

    Usage:
    This center is typically used by external clients, other centers (like AgentCenter),
    and internal services that need to validate and monitor agent health and capabilities.
    The center ensures system reliability and agent quality before deployment.
    """

    def __init__(self):
        """
        Initialize InspectionCenter with required service dependencies.

        Sets up the agent service for validation logic and A2A service
        for protocol testing and communication capabilities.
        """
        self.agent_service = AgentService()
        self.a2a_service = A2AService()

    async def inspect_agent_card(
        self, request: InspectionCenterRequest
    ) -> InspectionCenterResponse:
        """
        Inspect and validate an agent's card information.

        This method performs comprehensive validation of an agent's card:
        1. Validates the provided agent URL format and accessibility
        2. Retrieves the agent card from the specified URL
        3. Validates the card structure and content
        4. Checks agent capabilities and skill definitions
        5. Verifies compliance with A2A protocol standards
        6. Returns detailed validation results and recommendations

        The inspection process includes:
        - URL accessibility and format validation
        - Agent card structure validation
        - Capability and skill verification
        - Protocol compliance checking
        - Metadata validation

        Args:
            request: InspectionCenterRequest containing:
                - agent_url: The URL of the agent to inspect
                - validation_level: Optional validation depth (basic/standard/comprehensive)

        Returns:
            InspectionCenterResponse containing:
                - agent_url: The inspected agent URL
                - agent_card: The retrieved agent card (if successful)
                - result: Validation results and any errors found
                - status_code: HTTP status code indicating success/failure

        Raises:
            AgentNotFoundError: If agent URL is invalid or agent is not accessible
            ValidationError: If agent card fails validation checks
            NetworkError: If network connectivity issues occur
        """
        # check if agent url is valid
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
                card_resolver = A2ACardResolver(client, agent_url)
                card = await card_resolver.get_agent_card()

            card_data = card.model_dump(exclude_none=False)

            validation_errors = await self.agent_service.validate_agent_card(card_data)
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=validation_errors,
                status_code=200,
            )

        except httpx.RequestError:
            logger.error(f"Failed to connect to agent at {agent_url}", exc_info=True)
            errorMessage = ["Http RequestError: Failed to connect to agent: {e}"]
            return InspectionCenterResponse(
                agent_url=agent_url, result=errorMessage, status_code=502
            )
        except Exception:
            logger.error("An internal server error occurred", exc_info=True)
            errorMessage = [
                "InspectionCenter Server Error: An internal server error occurred: {e}"
            ]
            return InspectionCenterResponse(
                agent_url=agent_url, result=errorMessage, status_code=500
            )

    async def inspect_a2a_connection(
        self, request: InspectionCenterRequest
    ) -> InspectionCenterResponse:
        """
        Test A2A protocol connectivity and communication with an agent.

        This method performs comprehensive A2A protocol testing:
        1. Validates the provided agent URL and accessibility
        2. Initializes A2A client with the agent's card
        3. Performs connectivity tests to the agent endpoint
        4. Tests message exchange capabilities
        5. Validates response handling and error management
        6. Returns detailed connection test results

        The connection test includes:
        - Agent URL validation and accessibility
        - A2A client initialization and configuration
        - Message sending and response testing
        - Protocol compliance verification
        - Error handling validation

        Args:
            request: InspectionCenterRequest containing:
                - agent_url: The URL of the agent to test
                - test_message: Optional custom test message (defaults to "Hello, how are you?")
                - timeout: Optional connection timeout settings

        Returns:
            InspectionCenterResponse containing:
                - agent_url: The tested agent URL
                - agent_card: The agent's card information
                - result: Connection test results and any issues found
                - status_code: HTTP status code indicating success/failure

        Raises:
            AgentNotFoundError: If agent URL is invalid or agent is not accessible
            ConnectionError: If A2A connection fails
            ProtocolError: If A2A protocol compliance issues are found
        """
        # check if agent url is valid
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

        # initialize a2a client
        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            card_resolver = A2ACardResolver(httpx_client, str(agent_url))
            card = await card_resolver.get_agent_card()
            a2a_client = A2AClient(httpx_client, agent_card=card)

        except Exception as e:
            logger.error(f"Failed to initialize a2a client: {e}", exc_info=True)
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=["Failed to initialize a2a client"],
                status_code=500,
            )

        # send a2a client to agent
        inspection_message = "Hello, how are you?"

        try:
            dry_send_message_response = await self.a2a_service.dry_send_message(
                a2a_client, card, inspection_message
            )

            if not dry_send_message_response.is_valid:
                return InspectionCenterResponse(
                    agent_url=agent_url,
                    agent_card=card,
                    result=dry_send_message_response.result,
                    status_code=500,
                )

            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=dry_send_message_response.result,
                status_code=200,
            )

        except Exception as e:
            logger.error(f"Failed to send message to agent: {e}", exc_info=True)
            return InspectionCenterResponse(
                agent_url=agent_url,
                agent_card=card,
                result=["Failed to send message to agent"],
                status_code=500,
            )
