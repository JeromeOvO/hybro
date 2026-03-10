import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.client.errors import A2AClientHTTPError
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
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
    TaskIdParams,
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
from models.room import RoomAgentMessage
from services.a2a_constants import (
    INTERACTIVE_STATES,
    SyntheticTaskId,
    is_terminal_state,
)

logger = get_logger(__name__)


PLATFORM_SUPPORTED_MODES = {
    "text/plain",
    "text/markdown",
    "text/html",
    "text/csv",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "audio/wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/webm",
    "video/mp4",
    "video/webm",
    "application/json",
    "application/pdf",
    "application/xml",
    "application/zip",
}

MODE_TO_MIMES: dict[str, set[str]] = {
    "text": {"text/plain"},
    "image": {"image/png", "image/jpeg", "image/gif", "image/webp"},
    "audio": {"audio/wav", "audio/mpeg", "audio/mp4", "audio/webm"},
    "video": {"video/mp4", "video/webm"},
    "json": {"application/json"},
    "form": {"text/plain"},
    "markdown": {"text/markdown", "text/plain"},
}


class A2AService:
    def __init__(self):
        pass

    def _resolve_accepted_modes(self, agent_card: AgentCard) -> list[str]:
        """Intersect agent's output modes with platform capabilities."""
        raw_modes = getattr(agent_card, "default_output_modes", None)
        agent_modes = set(raw_modes if raw_modes is not None else ["text"])

        agent_mime_modes: set[str] = set()
        for mode in agent_modes:
            if "/" in mode:
                agent_mime_modes.add(mode)
            elif mode in MODE_TO_MIMES:
                agent_mime_modes.update(MODE_TO_MIMES[mode])
            else:
                agent_mime_modes.add("text/plain")

        accepted = agent_mime_modes & PLATFORM_SUPPORTED_MODES
        if not accepted:
            accepted = {"text/plain"}
        return sorted(accepted)

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

    AGENT_CARD_FETCH_TIMEOUT = 30.0
    DEFAULT_REQUEST_TIMEOUT = 600.0
    PUSH_NOTIFICATION_TIMEOUT = 60.0

    async def get_agent_card_from_url(self, agent_url: str) -> AgentCard:
        if not agent_url:
            raise IllgalParameterError()

        try:
            httpx_client = httpx.AsyncClient(timeout=self.AGENT_CARD_FETCH_TIMEOUT)
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
            httpx_client = httpx.AsyncClient(timeout=self.AGENT_CARD_FETCH_TIMEOUT)
            card = await self._fetch_agent_card_with_fallback(httpx_client, agent_url)
            a2a_client = A2AClient(httpx_client, agent_card=card)

            return a2a_client

        except Exception as e:
            logger.error(f"Failed to initialize a2a client: {e}", exc_info=True)
            raise A2AServiceError() from e

    @asynccontextmanager
    async def create_a2a_client(
        self, agent_card: AgentCard, timeout: float = DEFAULT_REQUEST_TIMEOUT
    ):
        httpx_client = httpx.AsyncClient(timeout=timeout)
        try:
            yield A2AClient(httpx_client, agent_card=agent_card)
        finally:
            await httpx_client.aclose()

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

    async def create_task_for_tracking(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        message: Message,
        *,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> dict[str, Any]:
        """
        Create task tracking fields for a room agent message.

        This sets up task tracking on an existing room_agent_message, allowing
        callers to send SSE events before the blocking agent call.

        The in-memory ``current_message`` is updated atomically with the DB
        write: ``message_content.message_task`` is set to the placeholder
        Task and ``has_task_tracking`` is set to True.

        Args:
            current_message: The RoomAgentMessage to enable tracking on
            agent_card: The agent's card
            message: A2A Message to send
            step_number: Current step number in the workflow (1-indexed)
            total_steps: Total number of steps in the workflow

        Returns:
            Dict with message_id, created_at, context_id, webhook_token, step_number, total_steps
        """
        from common.utils.time import utcnow
        from services.a2a_constants import NON_TERMINAL_STATES
        from services.database_service import db_service

        room_id = current_message.room_id
        user_id = current_message.user_id or "unknown"
        message_id = current_message.message_id

        # Check task limits before creating
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]
        try:
            await db_service.check_task_limits(
                user_id, room_id, non_terminal_state_values
            )
        except ValueError as e:
            raise A2AServiceError(str(e)) from e

        if not message_id:
            raise A2AServiceError("message_id is required for task tracking")

        webhook_token = db_service.generate_webhook_token()
        webhook_token_hash = db_service.hash_webhook_token(webhook_token)

        # Create placeholder task
        context_id = message.context_id or str(uuid4())
        placeholder_task = Task(
            id=f"pending-{context_id}",
            context_id=context_id,
            status=TaskStatus(state=TaskState.submitted),
        )

        now = utcnow()

        # Enable task tracking on the existing room_agent_message
        update_success = await db_service.enable_task_tracking_on_message(
            message_id=message_id,
            webhook_token_hash=webhook_token_hash,
            agent_url=agent_card.url,
            task_created_at=now,
            task_updated_at=now,
            task_data=placeholder_task.model_dump(mode="json"),
        )
        if not update_success:
            logger.error(
                "create_task_for_tracking: Failed to enable task tracking for message_id=%s "
                "- document may not exist",
                message_id,
            )
            raise A2AServiceError(
                f"Failed to persist task tracking for message {message_id}. "
                "The message document may not exist."
            )

        # Keep the in-memory object in sync with what was written to the DB.
        if current_message.message_content:
            current_message.message_content.message_task = placeholder_task
        current_message.has_task_tracking = True

        return {
            "message_id": message_id,
            "webhook_token": webhook_token,
            "context_id": context_id,
            "created_at": now.isoformat(),
            "step_number": step_number,
            "total_steps": total_steps,
        }

    async def send_message_to_tracked_agent(
        self,
        agent_card: AgentCard,
        message: Message,
        message_id: str,
        webhook_token: str,
        context_id: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Send message to agent with an existing task record.

        This is the second part of the split task tracking flow, called
        after SSE events have been sent.

        Args:
            agent_card: The agent's card
            message: A2A Message to send
            message_id: The message ID from create_task_for_tracking
            webhook_token: The webhook token from create_task_for_tracking
            context_id: The context ID from create_task_for_tracking

        Returns:
            For Message response: {"type": "message", "content": "..."}
            For Task response: {"type": "task", "message_id": "...", "status": "..."}
            For Interactive states: {"type": "task", "status": "input_required", ...}
        """
        from services.database_service import db_service

        # Build request with push notification config
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
                id=message_id,
                url=f"{webhook_url}/api/v1/webhooks/a2a/{message_id}",
                token=webhook_token,
            )
            logger.info(f"Enabled push notifications for task {message_id}")
        else:
            logger.warning(
                f"Push notifications DISABLED for task {message_id}. "
                f"Reason: {'Agent missing capability' if not has_capability else 'Missing WEBHOOK_BASE_URL setting'}"
            )

        payload = MessageSendParams(
            message=message,
            configuration=MessageSendConfiguration(
                accepted_output_modes=self._resolve_accepted_modes(agent_card),
                push_notification_config=push_config,
                blocking=False if push_config else None,
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

        # Send to agent — use a shorter timeout for push-notification agents
        # since they should acknowledge immediately and deliver results via webhook.
        try:
            dispatch_timeout = (
                self.PUSH_NOTIFICATION_TIMEOUT if push_config
                else self.DEFAULT_REQUEST_TIMEOUT
            )
            async with self.create_a2a_client(agent_card, timeout=dispatch_timeout) as a2a_client:
                response = await a2a_client.send_message(send_message_request)
        except Exception as e:
            # Mark task as failed IMMEDIATELY (don't wait for stale checker)
            failed_task = Task(
                id=SyntheticTaskId.FAILED,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        role=Role.agent,
                        parts=[TextPart(text=f"Failed to contact agent: {str(e)}")],
                        message_id=str(uuid4()),
                    ),
                ),
            )
            await db_service.update_task_on_message(
                message_id, failed_task.model_dump(mode="json")
            )
            logger.error(f"Failed to send message to agent: {e}")
            raise A2AServiceError(str(e)) from e

        # Handle error response
        if isinstance(response.root, JSONRPCErrorResponse):
            error_msg = str(response.root.error.message)
            failed_task = Task(
                id=SyntheticTaskId.FAILED,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        role=Role.agent,
                        parts=[TextPart(text=f"Agent error: {error_msg}")],
                        message_id=str(uuid4()),
                    ),
                ),
            )
            await db_service.update_task_on_message(
                message_id, failed_task.model_dump(mode="json")
            )
            raise A2AServiceError(error_msg)

        result = response.root.result

        # Handle Message response (fast path)
        if result.kind == "message":
            # Create completed task with message as artifact
            completed_task = self._message_to_completed_task(result, context_id)
            from common.utils.a2a_helpers import convert_pydantic_artifacts_to_s3
            if completed_task.artifacts:
                await convert_pydantic_artifacts_to_s3(
                    completed_task.artifacts, room_id=room_id or message_id, message_id=message_id,
                )

            message_text = self._extract_text_from_message(result)
            await db_service.update_task_on_message(
                message_id,
                completed_task.model_dump(mode="json"),
                message_text=message_text or None,
            )

            from common.utils.a2a_helpers import extract_parts_from_artifacts

            extracted = extract_parts_from_artifacts(completed_task.artifacts) if completed_task.artifacts else None
            non_text_parts = None
            if extracted and extracted.has_non_text:
                non_text_parts = extracted.file_parts + extracted.data_parts

            resp = {
                "type": "message",
                "message_id": message_id,
                "content": message_text,
            }
            if non_text_parts:
                resp["parts"] = non_text_parts
            return resp

        # Handle Task response (async path)
        if result.kind == "task":
            state = result.status.state
            # If already terminal, convert artifacts to S3 before persisting
            if is_terminal_state(state) and result.artifacts:
                from common.utils.a2a_helpers import convert_pydantic_artifacts_to_s3
                await convert_pydantic_artifacts_to_s3(
                    result.artifacts, room_id=room_id or message_id, message_id=message_id,
                )

            task_text = self._extract_text_from_task(result) if is_terminal_state(state) else None

            # Update with real task from agent
            await db_service.update_task_on_message(
                message_id,
                result.model_dump(mode="json"),
                message_text=task_text or None,
            )

            # If already terminal, return content
            if is_terminal_state(state):
                from common.utils.a2a_helpers import extract_parts_from_artifacts

                extracted = extract_parts_from_artifacts(result.artifacts) if result.artifacts else None
                non_text_parts = None
                if extracted and extracted.has_non_text:
                    non_text_parts = extracted.file_parts + extracted.data_parts

                resp = {
                    "type": "message",
                    "message_id": message_id,
                    "content": task_text,
                    "status": state.value if hasattr(state, "value") else str(state),
                }
                if non_text_parts:
                    resp["parts"] = non_text_parts
                return resp

            # Handle interactive states
            if state in INTERACTIVE_STATES:
                return {
                    "type": "task",
                    "message_id": message_id,
                    "task_id": result.id,
                    "status": state.value if hasattr(state, "value") else str(state),
                    "requires_input": state == TaskState.input_required,
                    "requires_auth": state == TaskState.auth_required,
                    "message": self._extract_status_message(result),
                }

            # Still processing - client should wait for webhook/SSE
            return {
                "type": "task",
                "message_id": message_id,
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
            async with self.create_a2a_client(agent_card) as a2a_client:
                payload = MessageSendParams(
                    message=message,
                    configuration=MessageSendConfiguration(
                        accepted_output_modes=self._resolve_accepted_modes(agent_card)
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
        async with self.create_a2a_client(agent_card) as a2a_client:
            payload = MessageSendParams(
                message=message,
                configuration=MessageSendConfiguration(
                    accepted_output_modes=self._resolve_accepted_modes(agent_card)
                ),
            )

            stream_request = SendStreamingMessageRequest(
                id=str(uuid4()),
                method="message/stream",
                jsonrpc="2.0",
                params=payload,
            )

            logger.debug(f"a2a_service: Starting streaming from agent: {agent_card}")
            response_stream = a2a_client.send_message_streaming(stream_request)
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
            configuration=MessageSendConfiguration(
                accepted_output_modes=self._resolve_accepted_modes(aegnt_card)
            ),
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

    async def cancel_remote_task(
        self,
        agent_card: AgentCard,
        task_id: str,
        *,
        timeout: float = 5.0,
    ) -> bool:
        """Send a best-effort cancel request to a remote A2A agent.

        This is fire-and-forget: if the agent doesn't support cancellation or
        returns an error, we log and continue — the local cancellation must
        not depend on the remote agent cooperating.

        Args:
            agent_card: The agent's card information.
            task_id: The remote task ID to cancel.
            timeout: Maximum seconds to wait for the cancel round-trip.
                Defaults to 5 s — long enough for a healthy agent but short
                enough to avoid blocking callers.

        Returns True if the cancel request was acknowledged, False otherwise.
        """
        try:
            async with self.create_a2a_client(agent_card) as a2a_client:
                cancel_request = CancelTaskRequest(
                    id=str(uuid4()),
                    params=TaskIdParams(id=task_id),
                )
                response = await asyncio.wait_for(
                    a2a_client.cancel_task(cancel_request),
                    timeout=timeout,
                )
                if isinstance(response.root, JSONRPCErrorResponse):
                    logger.debug(
                        "a2a_service: Remote cancel rejected for task %s: %s",
                        task_id,
                        response.root.error.message,
                    )
                    return False
                logger.info(
                    "a2a_service: Remote cancel acknowledged for task %s",
                    task_id,
                )
                return True
        except TimeoutError:
            logger.debug(
                "a2a_service: Remote cancel timed out for task %s after %.1fs",
                task_id,
                timeout,
            )
            return False
        except Exception as e:
            logger.debug(
                "a2a_service: Remote cancel failed for task %s: %s",
                task_id,
                e,
            )
            return False

    # ------------------------------------------------------------------
    # HITL: Reply to an existing task (input_required continuation)
    # ------------------------------------------------------------------

    async def reply_to_task(
        self,
        message_id: str,
        task_id: str,
        context_id: str,
        user_input: str,
    ) -> dict:
        """Send a follow-up message to an existing A2A task (for HITL replies).

        Uses the same task_id and context_id to continue the conversation
        rather than starting a new task.
        """
        from services.database_service import db_service

        msg = await db_service.get_room_agent_message_by_message_id(message_id)
        if not msg:
            raise ValueError(f"Agent message {message_id} not found")

        agent_url = msg.agent_url
        if not agent_url:
            raise ValueError(
                f"Agent message {message_id} has no agent_url"
            )

        # Generate a NEW webhook token (original plaintext was never stored)
        webhook_token = db_service.generate_webhook_token()
        webhook_token_hash = db_service.hash_webhook_token(webhook_token)
        token_updated = await db_service.update_webhook_token_hash_on_message(
            message_id, webhook_token_hash
        )
        if not token_updated:
            raise RuntimeError(
                f"Failed to rotate webhook token for message {message_id} — "
                "agent callback would fail verification; aborting reply"
            )

        webhook_url = settings.webhook_base_url or "http://localhost:8000"
        push_config = PushNotificationConfig(
            id=message_id,
            url=f"{webhook_url}/api/v1/webhooks/a2a/{message_id}",
            token=webhook_token,
        )

        # Build message continuing the existing task
        reply_message = Message(
            role=Role.user,
            parts=[TextPart(text=user_input)],
            message_id=str(uuid4()),
            task_id=task_id,
            context_id=context_id,
            reference_task_ids=[task_id],
        )

        params = MessageSendParams(
            message=reply_message,
            configuration=MessageSendConfiguration(
                push_notification_config=push_config,
                blocking=False,
            ),
        )

        # Use a scoped httpx client with `async with` to ensure cleanup.
        # Unlike get_a2a_client() which leaks connections, this properly
        # closes the transport. Agent card resolution is skipped since we
        # already have the agent_url from the stored RoomAgentMessage.
        async with httpx.AsyncClient(timeout=120.0) as client:
            a2a_client = A2AClient(
                httpx_client=client,
                url=agent_url,
            )
            request = SendMessageRequest(
                id=str(uuid4()),
                method="message/send",
                jsonrpc="2.0",
                params=params,
            )
            response = await a2a_client.send_message(request)

        # Update task status locally if a Task was returned
        result = getattr(response, "root", None)
        if result and hasattr(result, "result"):
            task_result = result.result
            if hasattr(task_result, "kind") and task_result.kind == "task":
                await db_service.update_task_on_message(
                    message_id, task_result.model_dump(mode="json")
                )

        logger.info(
            "hitl_reply_to_task_sent",
            extra={
                "message_id": message_id,
                "task_id": task_id,
                "context_id": context_id,
            },
        )
        return {"status": "sent"}


a2a_service = A2AService()
