from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.client.errors import A2AClientHTTPError
from a2a.types import (
    AgentCard,
    JSONRPCErrorResponse,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    PushNotificationConfig,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)

from common.utils.logger import get_logger
from config.settings import settings
from models.error import A2AServiceError, IllgalParameterError
from models.response import InsepectionCenterConnectionValidationResponse
from services.a2a_constants import INTERACTIVE_STATES, is_terminal_state

logger = get_logger(__name__)


class A2AService:
    def __init__(self):
        pass

    async def _fetch_agent_card_with_fallback(
        self, httpx_client: httpx.AsyncClient, agent_url: str
    ) -> AgentCard:
        """
        Fetch agent card with fallback support.
        First tries the new path (agent-card.json), then falls back to the old path (agent.json).
        """
        # Try new path first
        card_resolver = A2ACardResolver(
            httpx_client, str(agent_url), AGENT_CARD_WELL_KNOWN_PATH
        )
        try:
            logger.debug(
                f"Attempting to fetch agent card from {AGENT_CARD_WELL_KNOWN_PATH}"
            )
            card = await card_resolver.get_agent_card()
            logger.info(
                f"Successfully fetched agent card from {AGENT_CARD_WELL_KNOWN_PATH}"
            )
            return card
        except A2AClientHTTPError as e:
            # If 404, try the old path
            if e.status_code == 404:
                logger.debug(
                    f"Agent card not found at {AGENT_CARD_WELL_KNOWN_PATH}, "
                    f"trying fallback path {PREV_AGENT_CARD_WELL_KNOWN_PATH}"
                )
                card_resolver_fallback = A2ACardResolver(
                    httpx_client, str(agent_url), PREV_AGENT_CARD_WELL_KNOWN_PATH
                )
                card = await card_resolver_fallback.get_agent_card()
                logger.info(
                    f"Successfully fetched agent card from fallback path {PREV_AGENT_CARD_WELL_KNOWN_PATH}"
                )
                return card
            # If not 404, re-raise the error
            raise

    async def get_agent_card_from_url(self, agent_url: str) -> AgentCard:
        if not agent_url:
            raise IllgalParameterError()

        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            card = await self._fetch_agent_card_with_fallback(httpx_client, agent_url)
            return card

        except Exception as e:
            logger.error(f"Failed to get agent card from url: {e}", exc_info=True)
            raise A2AServiceError() from e

    async def get_a2a_client(self, agent_url: str) -> A2AClient:
        # check if agent url is valid

        if not agent_url:
            raise IllgalParameterError()

        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            card = await self._fetch_agent_card_with_fallback(httpx_client, agent_url)
            a2a_client = A2AClient(httpx_client, agent_card=card)

            return a2a_client

        except Exception as e:
            logger.error(f"Failed to initialize a2a client: {e}", exc_info=True)
            raise A2AServiceError() from e

    async def create_a2a_client(self, agent_card: AgentCard) -> A2AClient:
        try:
            httpx_client = httpx.AsyncClient(timeout=600.0)
            a2a_client = A2AClient(httpx_client, agent_card=agent_card)
            return a2a_client

        except Exception as e:
            logger.error(f"Failed to initialize a2a client: {e}", exc_info=True)
            raise A2AServiceError() from e

    def has_streaming_capability(self, agent_card: AgentCard) -> bool:
        """
        Check if an agent supports streaming capability.

        Args:
            agent_card: The agent's card with capabilities

        Returns:
            True if agent supports streaming, False otherwise
        """
        return (
            hasattr(agent_card.capabilities, "streaming")
            and agent_card.capabilities.streaming is True
        )

    def has_push_notification_capability(self, agent_card: AgentCard) -> bool:
        """
        Check if an agent supports push notifications (webhooks).

        Args:
            agent_card: The agent's card with capabilities

        Returns:
            True if agent supports push notifications, False otherwise
        """
        has_caps = agent_card.capabilities is not None
        # Check both snake_case (A2A SDK) and camelCase (custom types) attribute names
        push_val = False
        if has_caps:
            push_val = getattr(agent_card.capabilities, "push_notifications", None)
            if push_val is None:
                push_val = getattr(agent_card.capabilities, "pushNotifications", False)
        logger.debug(
            f"has_push_notification_capability: agent={agent_card.name}, "
            f"has_capabilities={has_caps}, push_notifications={push_val}"
        )
        return has_caps and bool(push_val)

    async def send_message_with_task_tracking(
        self,
        room_id: str,
        user_id: str,
        agent_card: AgentCard,
        message: Message,
        agent_id: str | None = None,
        related_message_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Send message to agent with task tracking for long-running operations.

        This method:
        1. Creates a placeholder task record to get internal_id and webhook token
        2. Sends message to agent with push notification config (if supported)
        3. Handles Message response (fast path) or Task response (async path)
        4. Returns appropriate response for frontend

        Args:
            room_id: Room this message belongs to
            user_id: User who sent the message
            agent_card: The agent's card
            message: A2A Message to send
            agent_id: Optional agent ID for frontend rendering
            related_message_id: Optional room user message ID that initiated the task

        Returns:
            For Message response: {"type": "message", "content": "..."}
            For Task response: {"type": "task", "internal_id": "...", "status": "..."}
            For Interactive states: {"type": "task", "status": "input_required", ...}
        """
        from services.a2a_task_service import get_a2a_task_service

        task_service = get_a2a_task_service()

        # 1. Create placeholder task record to get internal_id and webhook token
        placeholder_task = Task(
            id="pending",
            context_id=message.context_id or str(uuid4()),
            status=TaskStatus(state=TaskState.submitted),
        )

        try:
            internal_id, webhook_token = await task_service.create_task(
                room_id=room_id,
                user_id=user_id,
                agent_url=agent_card.url,
                task=placeholder_task,
                agent_name=agent_card.name,
                agent_id=agent_id,
                related_message_id=related_message_id,
            )
        except ValueError as e:
            # Task limit exceeded
            raise A2AServiceError(str(e)) from e

        # 2. Build request with push notification config
        push_config = None
        has_capability = self.has_push_notification_capability(agent_card)
        webhook_url = (
            settings.webhook_base_url or "http://localhost:8000"
        )  # Fallback to default

        logger.info(
            f"Push notification check: has_capability={has_capability}, "
            f"webhook_url='{webhook_url}'"
        )

        if has_capability and webhook_url:
            push_config = PushNotificationConfig(
                id=internal_id,
                url=f"{webhook_url}/api/v1/webhooks/a2a/{internal_id}",
                token=webhook_token,
            )
            logger.info(f"Enabled push notifications for task {internal_id}")
        else:
            logger.warning(
                f"Push notifications DISABLED for task {internal_id}. "
                f"Reason: {'Agent missing capability' if not has_capability else 'Missing WEBHOOK_BASE_URL setting'}"
            )

        payload = MessageSendParams(
            message=message,
            configuration=MessageSendConfiguration(
                acceptedOutputModes=["text/plain"],
                push_notification_config=push_config,
            ),
        )

        # Debug: log the actual payload being sent
        logger.debug(
            f"MessageSendParams configuration: push_notification_config={push_config}, "
            f"payload.configuration={payload.configuration}"
        )
        if payload.configuration:
            logger.debug(
                f"Configuration details: push_notification_config={payload.configuration.push_notification_config}"
            )

        send_message_request = SendMessageRequest(
            id=str(uuid4()),
            method="message/send",
            jsonrpc="2.0",
            params=payload,
        )

        # 3. Send to agent
        try:
            a2a_client = await self.create_a2a_client(agent_card)
            response = await a2a_client.send_message(send_message_request)
        except Exception as e:
            # Mark task as failed IMMEDIATELY (don't wait for stale checker)
            failed_task = Task(
                id="failed",
                context_id=placeholder_task.context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        role=Role.agent,
                        parts=[TextPart(text=f"Failed to contact agent: {str(e)}")],
                    ),
                ),
            )
            await task_service.update_task(internal_id, failed_task)
            logger.error(f"Failed to send message to agent: {e}")
            raise A2AServiceError(str(e)) from e

        # Handle error response
        if isinstance(response.root, JSONRPCErrorResponse):
            error_msg = str(response.root.error.message)
            failed_task = Task(
                id="failed",
                context_id=placeholder_task.context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        role=Role.agent,
                        parts=[TextPart(text=f"Agent error: {error_msg}")],
                    ),
                ),
            )
            await task_service.update_task(internal_id, failed_task)
            raise A2AServiceError(error_msg)

        result = response.root.result

        # 4. Handle Message response (fast path)
        if result.kind == "message":
            # Create completed task with message as artifact
            completed_task = self._message_to_completed_task(
                result, placeholder_task.context_id
            )
            await task_service.update_task(internal_id, completed_task)

            return {
                "type": "message",
                "internal_id": internal_id,
                "content": self._extract_text_from_message(result),
            }

        # 5. Handle Task response (async path)
        if result.kind == "task":
            # Update with real task from agent
            await task_service.update_task(internal_id, result)

            state = result.status.state

            # If already terminal, return content
            if is_terminal_state(state):
                return {
                    "type": "message",
                    "internal_id": internal_id,
                    "content": self._extract_text_from_task(result),
                    "status": state.value if hasattr(state, "value") else str(state),
                }

            # Handle interactive states
            if state in INTERACTIVE_STATES:
                return {
                    "type": "task",
                    "internal_id": internal_id,
                    "task_id": result.id,
                    "status": state.value if hasattr(state, "value") else str(state),
                    "requires_input": state == TaskState.input_required,
                    "requires_auth": state == TaskState.auth_required,
                    "message": self._extract_status_message(result),
                }

            # Still processing - client should wait for webhook/SSE
            return {
                "type": "task",
                "internal_id": internal_id,
                "task_id": result.id,
                "status": state.value if hasattr(state, "value") else str(state),
                "agent_name": agent_card.name,
            }

        raise A2AServiceError(f"Unexpected response kind: {result.kind}")

    def _message_to_completed_task(self, message: Message, context_id: str) -> Task:
        """Convert a Message response to a completed Task."""
        from a2a.types import Artifact

        return Task(
            id=str(uuid4()),
            context_id=context_id,
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id=str(uuid4()),
                    name="response",
                    parts=message.parts,
                )
            ],
        )

    def _extract_text_from_message(self, message: Message) -> str:
        """Extract text content from a Message."""
        texts = []
        for part in message.parts or []:
            if hasattr(part, "text") and part.text:
                texts.append(part.text)
            elif hasattr(part, "root") and hasattr(part.root, "text"):
                texts.append(part.root.text)
        return "".join(texts)

    def _extract_text_from_task(self, task: Task) -> str | None:
        """Extract text content from a Task's artifacts."""
        if not task.artifacts:
            return None
        texts = []
        for artifact in task.artifacts:
            for part in artifact.parts or []:
                if hasattr(part, "text") and part.text:
                    texts.append(part.text)
                elif hasattr(part, "root") and hasattr(part.root, "text"):
                    texts.append(part.root.text)
        return "".join(texts) if texts else None

    def _extract_status_message(self, task: Task) -> str | None:
        """Extract human-readable message from task status."""
        if task.status.message and task.status.message.parts:
            for part in task.status.message.parts:
                if hasattr(part, "text") and part.text:
                    return part.text
                if hasattr(part, "root") and hasattr(part.root, "text"):
                    return part.root.text
        return None

    async def send_message_sync(
        self,
        agent_card: AgentCard,
        message: Message,
    ) -> SendMessageResponse | None:
        """
        Send message to agent using synchronous (non-streaming) endpoint.

        Args:
            agent_card: The card of the target agent
            message: A2A Message to send

        Returns:
            Task data as dict

        Raises:
            A2AServiceError: If sending fails
        """

        try:
            # Use provided client or create new one
            a2a_client = await self.create_a2a_client(agent_card)

            payload = MessageSendParams(
                message=message,
                configuration=MessageSendConfiguration(
                    acceptedOutputModes=["text/plain"]
                ),
            )

            send_message_request = SendMessageRequest(
                id=str(uuid4()),
                method="message/send",
                jsonrpc="2.0",
                params=payload,
            )

            logger.debug(f"a2a_service: Sending sync message to agent: {agent_card}")
            response = await a2a_client.send_message(send_message_request)

            # Handle error
            if isinstance(response.root, JSONRPCErrorResponse):
                error_msg = str(response.root.error.message)
                logger.error(f"a2a_service: Agent error: {error_msg}")
                raise A2AServiceError(error_msg)
            return response

        except A2AServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to send sync message: {e}", exc_info=True)
            raise A2AServiceError(str(e)) from e

    async def send_message_streaming(
        self,
        agent_card: AgentCard,
        message: Message,
    ) -> AsyncGenerator[SendStreamingMessageResponse, None]:
        """
        Send message to agent with TRUE passthrough streaming.

        This method acts as a transparent passthrough - whatever the agent sends
        (whether immediately or after processing), we forward it immediately to
        the caller. No buffering.

        Note: This method assumes the agent supports streaming. Use send_message()
        for automatic capability detection.

        Args:
            agent_card: The card of the target agent
            message: A2A Message to send

        Yields:
            Dict events in our internal format (TokenStreamingEvent, TaskUpdateStreamingEvent, etc.)
        """
        a2a_client = await self.create_a2a_client(agent_card)

        payload = MessageSendParams(
            message=message,
            configuration=MessageSendConfiguration(acceptedOutputModes=["text/plain"]),
        )

        stream_request = SendStreamingMessageRequest(
            id=str(uuid4()),
            method="message/stream",
            jsonrpc="2.0",
            params=payload,
        )

        logger.debug(f"a2a_service: Starting streaming from agent: {agent_card}")
        response_stream = a2a_client.send_message_streaming(stream_request)
        # Yield each event IMMEDIATELY as it arrives
        async for response in response_stream:
            yield response

    async def send_message(
        self, agent_card: AgentCard, message: Message
    ) -> AsyncGenerator[SendStreamingMessageResponse | SendMessageResponse, None]:
        """
        Send message to agent with automatic capability detection.

        This is the main entry point for sending messages. It will:
        1. Fetch the agent card
        2. Check if agent supports streaming
        3. Call send_message_streaming() OR send_message_sync() accordingly
        4. Always yield events in consistent format

        Args:
            agent_card: The card of the target agent
            message: A2A Message to send

        Yields:
            Dict events (TokenStreamingEvent, TaskUpdateStreamingEvent, etc.)
        """
        # Check agent capability and route to appropriate method
        if self.has_streaming_capability(agent_card):
            logger.debug(f"a2a_service: Agent supports streaming: {agent_card.url}")
            async for event in self.send_message_streaming(agent_card, message):
                yield event

        else:
            logger.debug(
                f"a2a_service: Agent doesn't support streaming, using sync: {agent_card.url}"
            )
            try:
                event = await self.send_message_sync(agent_card, message)
                yield event

            except Exception as e:
                raise A2AServiceError(str(e)) from e

    async def dry_send_message(
        self, a2a_client: A2AClient, aegnt_card: AgentCard, message_text: str
    ) -> InsepectionCenterConnectionValidationResponse:
        message = Message(
            role=Role.user,
            parts=[TextPart(text=str(message_text))],  # type: ignore[list-item]
            message_id=str(uuid4()),
            context_id=str(uuid4()),
        )

        payload = MessageSendParams(
            message=message,
            configuration=MessageSendConfiguration(acceptedOutputModes=["text/plain"]),
        )

        supports_streaming = (
            hasattr(aegnt_card.capabilities, "streaming")
            and aegnt_card.capabilities.streaming is True
        )

        try:
            if supports_streaming:
                stream_request = SendStreamingMessageRequest(
                    id=str(uuid4()),
                    method="message/stream",
                    jsonrpc="2.0",
                    params=payload,
                )
                response_stream = a2a_client.send_message_streaming(stream_request)

                async for stream_result in response_stream:
                    inspection_center_response = await self.validate_a2a_response(
                        stream_result
                    )
                    inspection_center_response.agent_url = aegnt_card.url
                    inspection_center_response.agent_card = aegnt_card
                return inspection_center_response
            else:
                send_message_request = SendMessageRequest(
                    id=str(uuid4()),
                    method="message/send",
                    jsonrpc="2.0",
                    params=payload,
                )
                send_result = await a2a_client.send_message(send_message_request)
                inspection_center_response = await self.validate_a2a_response(
                    send_result
                )
                inspection_center_response.agent_url = aegnt_card.url
                inspection_center_response.agent_card = aegnt_card
                return inspection_center_response

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return InsepectionCenterConnectionValidationResponse(
                agent_url=aegnt_card.url,
                agent_card=aegnt_card,
                result=[f"Failed to send message: {e}"],
                is_valid=False,
                status_code=500,
            )

    async def validate_a2a_response(
        self, result: SendMessageResponse | SendStreamingMessageResponse
    ) -> InsepectionCenterConnectionValidationResponse:
        """Validate a response from the A2A client."""
        if isinstance(result.root, JSONRPCErrorResponse):
            error_data = result.root.error.model_dump(exclude_none=True)

            return InsepectionCenterConnectionValidationResponse(
                agent_url="", result=[str(error_data)], is_valid=False, status_code=500
            )

        # Success case
        response_data = result.root.result

        logger.info(f"validate_a2a_response: response_data: {response_data}")

        response_data = response_data.model_dump(exclude_none=True)

        validation_errors = self.validate_message(response_data)

        return InsepectionCenterConnectionValidationResponse(
            agent_url="", result=validation_errors, is_valid=True, status_code=200
        )

    def validate_message(self, data: dict[str, Any]) -> list[str]:
        """Validate an incoming message from the agent based on its kind."""
        if "kind" not in data:
            return ["Response from agent is missing required 'kind' field."]

        kind = data.get("kind")
        validators = {
            "task": self._validate_task,
            "status-update": self._validate_status_update,
            "artifact-update": self._validate_artifact_update,
            "message": self._validate_message,
        }

        validator = validators.get(str(kind))
        if validator:
            return validator(data)

        return [f"Unknown message kind received: '{kind}'."]

    def _validate_task(self, data: dict[str, Any]) -> list[str]:
        errors = []
        if "id" not in data:
            errors.append("Task object missing required field: 'id'.")
        if "status" not in data or "state" not in data.get("status", {}):
            errors.append("Task object missing required field: 'status.state'.")
        return errors

    def _validate_status_update(self, data: dict[str, Any]) -> list[str]:
        errors = []
        if "status" not in data or "state" not in data.get("status", {}):
            errors.append("StatusUpdate object missing required field: 'status.state'.")
        return errors

    def _validate_artifact_update(self, data: dict[str, Any]) -> list[str]:
        errors = []
        if "artifact" not in data:
            errors.append("ArtifactUpdate object missing required field: 'artifact'.")
        elif (
            "parts" not in data.get("artifact", {})
            or not isinstance(data.get("artifact", {}).get("parts"), list)
            or not data.get("artifact", {}).get("parts")
        ):
            errors.append("Artifact object must have a non-empty 'parts' array.")
        return errors

    def _validate_message(self, data: dict[str, Any]) -> list[str]:
        errors = []
        if (
            "parts" not in data
            or not isinstance(data.get("parts"), list)
            or not data.get("parts")
        ):
            errors.append("Message object must have a non-empty 'parts' array.")
        if "role" not in data or data.get("role") != "agent":
            errors.append("Message from agent must have 'role' set to 'agent'.")
        return errors

    async def process_a2a_response(
        self, response: SendMessageResponse | SendStreamingMessageResponse
    ) -> Any:
        if isinstance(response.root, JSONRPCErrorResponse):
            raise A2AServiceError()

        # Success case
        logger.info(f"process_a2a_response: response: {response}")

        response_data = response.root.result

        logger.info(f"process_a2a_response: response_data: {response_data}")

        return response_data


a2a_service = A2AService()
