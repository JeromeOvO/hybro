from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from a2a_adapter.client_facade import (
    cancel_remote_task as adapter_cancel_remote_task,
)
from a2a_adapter.client_facade import (
    fetch_agent_card_with_fallback as adapter_fetch_card,
)
from a2a_adapter.client_facade import (
    send_hitl_reply as adapter_send_hitl_reply,
)
from a2a_adapter.client_facade import (
    send_message as adapter_send_message,
)
from a2a_adapter.client_facade import (
    stream_message as adapter_stream_message,
)
from a2a_adapter.translators import (
    coerce_parts as adapter_coerce_parts,
)
from a2a_adapter.translators import (
    facade_result_to_model,
    message_to_completed_task,
    resolve_accepted_output_modes,
)
from common.a2a_constants import (
    INTERACTIVE_STATES,
    SyntheticTaskId,
    is_terminal_state,
)
from common.types import (
    AgentCard,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from common.types import (
    MessageRole as Role,
)
from common.utils.logger import get_logger
from models.error import A2AServiceError, IllgalParameterError
from models.response import InsepectionCenterConnectionValidationResponse
from models.room import RoomAgentMessage

logger = get_logger(__name__)


@dataclass(frozen=True)
class A2ARuntimeConfig:
    webhook_base_url: str = ""
    agent_card_fetch_timeout: float = 30.0
    default_request_timeout: float = 600.0
    push_notification_timeout: float = 60.0


class A2AService:
    def __init__(self):
        self._task_db = None
        self._call_counter = None
        self._runtime_config = A2ARuntimeConfig()

    def bind_runtime_config(self, config: A2ARuntimeConfig) -> None:
        self._runtime_config = config

    def bind_task_db(self, task_db: Any, *, call_counter: Any | None = None) -> None:
        self._task_db = task_db
        if call_counter is not None:
            self._call_counter = call_counter
        elif hasattr(task_db, "increment_agent_call_count"):
            self._call_counter = task_db

    def _get_task_db(self) -> Any | None:
        return getattr(self, "_task_db", None)

    def _get_call_counter(self) -> Any | None:
        return getattr(self, "_call_counter", None)

    def _resolve_accepted_modes(self, agent_card: AgentCard) -> list[str]:
        """Intersect agent's output modes with platform capabilities."""
        return resolve_accepted_output_modes(agent_card)

    async def _fetch_agent_card_with_fallback(
        self, _client: Any, agent_url: str
    ) -> AgentCard:
        """Fetch an agent card through the SDK-confined adapter."""
        return AgentCard.model_validate(
            await adapter_fetch_card(
                agent_url,
                timeout=self._agent_card_fetch_timeout,
            )
        )

    @property
    def _webhook_base_url(self) -> str:
        return self._runtime_config.webhook_base_url.rstrip("/")

    @property
    def _agent_card_fetch_timeout(self) -> float:
        return self._runtime_config.agent_card_fetch_timeout

    @property
    def _default_request_timeout(self) -> float:
        return self._runtime_config.default_request_timeout

    @property
    def _push_notification_timeout(self) -> float:
        return self._runtime_config.push_notification_timeout

    async def get_agent_card_from_url(self, agent_url: str) -> AgentCard:
        if not agent_url:
            raise IllgalParameterError()

        try:
            return AgentCard.model_validate(
                await adapter_fetch_card(
                    agent_url,
                    timeout=self._agent_card_fetch_timeout,
                )
            )

        except Exception as e:
            logger.error(f"Failed to get agent card from url: {e}", exc_info=True)
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
        task_db = self._get_task_db()
        if task_db is None:
            raise A2AServiceError("Task DB is unavailable for task tracking")
        from common.a2a_constants import NON_TERMINAL_STATES
        from common.utils.time import utcnow

        room_id = current_message.room_id
        user_id = current_message.user_id or "unknown"
        message_id = current_message.message_id

        # Check task limits before creating
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]
        try:
            await task_db.check_task_limits(user_id, room_id, non_terminal_state_values)
        except ValueError as e:
            raise A2AServiceError(str(e)) from e

        if not message_id:
            raise A2AServiceError("message_id is required for task tracking")

        webhook_token = task_db.generate_webhook_token()
        webhook_token_hash = task_db.hash_webhook_token(webhook_token)

        # Create placeholder task
        context_id = message.context_id or str(uuid4())
        placeholder_task = Task(
            id=f"pending-{context_id}",
            context_id=context_id,
            status=TaskStatus(state=TaskState.submitted),
        )

        now = utcnow()

        # Enable task tracking on the existing room_agent_message
        update_success = await task_db.enable_task_tracking_on_message(
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

    async def _record_call(self, agent_id: str | None, *, success: bool) -> None:
        """Atomically increment call_count (and call_success_count) for an agent."""
        if not agent_id:
            return
        call_counter = self._get_call_counter()
        if call_counter is None:
            return

        increment_agent_call_count = None
        try:
            increment_agent_call_count = getattr(
                call_counter,
                "increment_agent_call_count",
                None,
            )
        except Exception:
            increment_agent_call_count = None

        if callable(increment_agent_call_count):
            try:
                await increment_agent_call_count(agent_id, success=success)
            except Exception as e:
                logger.warning("Failed to record agent call for %s: %s", agent_id, e)
            return

        logger.warning("Failed to record agent call for %s", agent_id)

    async def send_message_to_tracked_agent(
        self,
        agent_card: AgentCard,
        message: Message,
        message_id: str,
        webhook_token: str,
        context_id: str,
        room_id: str | None = None,
        agent_id: str | None = None,
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
        task_db = self._get_task_db()
        if task_db is None:
            raise A2AServiceError("Task DB is unavailable for tracked send")

        # Build request with push notification config.
        # Push notifications require a publicly reachable WEBHOOK_BASE_URL. When
        # that setting is absent (e.g. local dev), fall back to blocking=True so
        # the agent holds the connection and returns the result directly instead
        # of posting to an unreachable localhost URL.
        push_config = None
        has_capability = self.has_push_notification_capability(agent_card)
        webhook_url = self._webhook_base_url

        logger.info(
            f"Push notification check: has_capability={has_capability}, "
            f"webhook_url='{webhook_url}'"
        )

        if has_capability and webhook_url:
            push_config = {
                "id": message_id,
                "url": f"{webhook_url}/api/v1/webhooks/a2a/{message_id}",
                "token": webhook_token,
            }
            logger.info(
                f"Enabled push notifications for task {message_id} "
                f"(callback → {webhook_url}/api/v1/webhooks/a2a/{message_id})"
            )
        else:
            reason = (
                "Agent missing capability"
                if not has_capability
                else "WEBHOOK_BASE_URL not set — using blocking=True"
            )
            logger.warning(
                f"Push notifications DISABLED for task {message_id}. Reason: {reason}"
            )

        # blocking=False tells the agent to ack quickly and deliver via webhook;
        # blocking=True (no push_config) tells it to hold the connection until done.
        use_blocking = push_config is None

        logger.debug(
            f"MessageSendParams configuration: push_notification_config={push_config}, "
            f"blocking={use_blocking}"
        )

        # Push-notification agents should ack immediately (use short timeout).
        # Blocking agents hold the connection for the full task duration.
        try:
            dispatch_timeout = (
                self._push_notification_timeout
                if push_config
                else self._default_request_timeout
            )
            response = await adapter_send_message(
                agent_card,
                message,
                accepted_output_modes=self._resolve_accepted_modes(agent_card),
                push_notification_config=push_config,
                blocking=use_blocking,
                timeout=dispatch_timeout,
            )
        except Exception as e:
            await self._record_call(agent_id, success=False)
            # Mark task as failed IMMEDIATELY (don't wait for stale checker)
            failed_task = Task(
                id=SyntheticTaskId.FAILED,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        role=Role.AGENT,
                        parts=[
                            Part(
                                root=TextPart(
                                    text=f"Failed to contact agent: {str(e)}"
                                )
                            )
                        ],
                        message_id=str(uuid4()),
                    ),
                ),
            )
            await task_db.update_task_on_message(
                message_id, failed_task.model_dump(mode="json")
            )
            logger.error(f"Failed to send message to agent: {e}")
            raise A2AServiceError(str(e)) from e

        # Handle error response
        if response.get("kind") == "error":
            await self._record_call(agent_id, success=False)
            error_payload = response.get("error")
            error_msg = (
                error_payload.get("message")
                if isinstance(error_payload, dict)
                else str(error_payload)
            )
            failed_task = Task(
                id=SyntheticTaskId.FAILED,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.failed,
                    message=Message(
                        role=Role.AGENT,
                        parts=[Part(root=TextPart(text=f"Agent error: {error_msg}"))],
                        message_id=str(uuid4()),
                    ),
                ),
            )
            await task_db.update_task_on_message(
                message_id, failed_task.model_dump(mode="json")
            )
            raise A2AServiceError(error_msg)

        await self._record_call(agent_id, success=True)
        result = self._facade_result_to_model(response)

        # --- Diagnostic: log the raw response shape ---
        _art_count = (
            len(result.artifacts)
            if hasattr(result, "artifacts") and result.artifacts
            else 0
        )
        _parts_summary = ""
        if hasattr(result, "artifacts") and result.artifacts:
            _parts_summary = "; ".join(
                f"art[{i}]={len(a.parts) if a.parts else 0}p"
                for i, a in enumerate(result.artifacts)
            )
        elif hasattr(result, "parts") and result.parts:
            _parts_summary = f"msg_parts={len(result.parts)}"
        logger.info(
            "send_message_to_tracked_agent: response kind=%s, state=%s, "
            "artifacts=%d (%s) for message_id=%s",
            result.kind,
            getattr(result.status, "state", "N/A")
            if hasattr(result, "status")
            else "N/A",
            _art_count,
            _parts_summary or "none",
            message_id,
        )

        # Handle Message response (fast path)
        if result.kind == "message":
            # Create completed task with message as artifact
            completed_task = self._message_to_completed_task(result, context_id)
            from common.utils.a2a_helpers import convert_pydantic_artifacts_to_s3

            if completed_task.artifacts:
                await convert_pydantic_artifacts_to_s3(
                    completed_task.artifacts,
                    room_id=room_id or message_id,
                    message_id=message_id,
                )

            message_text = self._extract_text_from_message(result)
            persisted = await task_db.update_task_on_message(
                message_id,
                completed_task.model_dump(mode="json"),
                message_text=message_text or None,
            )

            from common.utils.a2a_helpers import extract_parts_from_artifacts

            extracted = (
                extract_parts_from_artifacts(completed_task.artifacts)
                if completed_task.artifacts
                else None
            )
            non_text_parts = None
            if extracted and extracted.has_non_text:
                non_text_parts = extracted.file_parts + extracted.data_parts

            resp = {
                "type": "message",
                "message_id": message_id,
                "content": message_text,
                "persisted": persisted,
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
                    result.artifacts,
                    room_id=room_id or message_id,
                    message_id=message_id,
                )

            task_text = (
                self._extract_text_from_task(result)
                if is_terminal_state(state)
                else None
            )

            # Update with real task from agent
            persisted = await task_db.update_task_on_message(
                message_id,
                result.model_dump(mode="json"),
                message_text=task_text or None,
            )

            # If already terminal, return content
            if is_terminal_state(state):
                from common.utils.a2a_helpers import extract_parts_from_artifacts

                extracted = (
                    extract_parts_from_artifacts(result.artifacts)
                    if result.artifacts
                    else None
                )
                non_text_parts = None
                if extracted and extracted.has_non_text:
                    non_text_parts = extracted.file_parts + extracted.data_parts

                resp = {
                    "type": "message",
                    "message_id": message_id,
                    "content": task_text,
                    "status": state.value if hasattr(state, "value") else str(state),
                    "persisted": persisted,
                }
                if non_text_parts:
                    resp["parts"] = non_text_parts
                # For non-completed terminal states, extract error from status.message
                if state != TaskState.completed:
                    error_text = self._extract_status_message(result)
                    if error_text:
                        resp["error"] = error_text
                    elif not task_text:
                        resp["error"] = f"Task {state.value}"
                elif not task_text:
                    status_text = self._extract_status_message(result)
                    if status_text:
                        resp["message"] = status_text
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
        return message_to_completed_task(
            message,
            context_id,
            task_id=str(uuid4()),
            artifact_id=str(uuid4()),
        )

    @staticmethod
    def _coerce_parts(parts: list[Any] | None) -> list[Part]:
        return adapter_coerce_parts(parts)

    def _facade_result_to_model(self, response: dict[str, Any]) -> Message | Task:
        try:
            return facade_result_to_model(response)
        except ValueError as exc:
            raise A2AServiceError(str(exc)) from exc

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
        agent_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Send message to agent using synchronous (non-streaming) endpoint.

        Args:
            agent_card: The card of the target agent
            message: A2A Message to send
            agent_id: Optional agent_id for call count tracking

        Returns:
            Task data as dict

        Raises:
            A2AServiceError: If sending fails
        """
        success = False
        try:
            logger.debug(f"a2a_service: Sending sync message to agent: {agent_card}")
            response = await adapter_send_message(
                agent_card,
                message,
                accepted_output_modes=self._resolve_accepted_modes(agent_card),
                blocking=True,
                timeout=self._default_request_timeout,
            )
            if response.get("kind") == "error":
                error_payload = response.get("error")
                error_msg = (
                    error_payload.get("message")
                    if isinstance(error_payload, dict)
                    else str(error_payload)
                )
                logger.error(f"a2a_service: Agent error: {error_msg}")
                raise A2AServiceError(error_msg)
            success = True
            return response

        except A2AServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to send sync message: {e}", exc_info=True)
            raise A2AServiceError(str(e)) from e
        finally:
            await self._record_call(agent_id, success=success)

    async def send_message_streaming(
        self,
        agent_card: AgentCard,
        message: Message,
        agent_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
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
            agent_id: Optional agent_id for call count tracking

        Yields:
            Dict events in our internal format (TokenStreamingEvent, TaskUpdateStreamingEvent, etc.)
        """
        success = False
        try:
            logger.debug(f"a2a_service: Starting streaming from agent: {agent_card}")
            async for response in adapter_stream_message(
                agent_card,
                message,
                accepted_output_modes=self._resolve_accepted_modes(agent_card),
                timeout=self._default_request_timeout,
            ):
                success = response.get("kind") != "error"
                yield response
        finally:
            await self._record_call(agent_id, success=success)

    async def send_message(
        self,
        agent_card: AgentCard,
        message: Message,
        agent_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
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
            agent_id: Optional agent_id for call count tracking

        Yields:
            Dict events (TokenStreamingEvent, TaskUpdateStreamingEvent, etc.)
        """
        # Check agent capability and route to appropriate method
        if self.has_streaming_capability(agent_card):
            logger.debug(f"a2a_service: Agent supports streaming: {agent_card.url}")
            async for event in self.send_message_streaming(
                agent_card, message, agent_id=agent_id
            ):
                yield event

        else:
            logger.debug(
                f"a2a_service: Agent doesn't support streaming, using sync: {agent_card.url}"
            )
            try:
                event = await self.send_message_sync(
                    agent_card, message, agent_id=agent_id
                )
                yield event

            except Exception as e:
                raise A2AServiceError(str(e)) from e

    async def dry_send_message(
        self, _a2a_client: Any, aegnt_card: AgentCard, message_text: str
    ) -> InsepectionCenterConnectionValidationResponse:
        message = Message(
            role=Role.USER,
            parts=[Part(root=TextPart(text=str(message_text)))],
            message_id=str(uuid4()),
            context_id=str(uuid4()),
        )
        try:
            response = await adapter_send_message(
                aegnt_card,
                message,
                accepted_output_modes=self._resolve_accepted_modes(aegnt_card),
                blocking=True,
                timeout=self._default_request_timeout,
            )
            inspection_center_response = await self.validate_a2a_response(response)
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
        self, result: dict[str, Any]
    ) -> InsepectionCenterConnectionValidationResponse:
        """Validate a response from the A2A client."""
        if result.get("kind") == "error":
            return InsepectionCenterConnectionValidationResponse(
                agent_url="",
                result=[str(result.get("error"))],
                is_valid=False,
                status_code=500,
            )

        response_data = result.get("result") or {}
        logger.info(f"validate_a2a_response: response_data: {response_data}")
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
        self, response: dict[str, Any]
    ) -> Any:
        if response.get("kind") == "error":
            raise A2AServiceError()

        logger.info(f"process_a2a_response: response: {response}")
        response_data = self._facade_result_to_model(response)
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
        acknowledged = await adapter_cancel_remote_task(
            agent_card,
            task_id,
            timeout=timeout,
        )
        if acknowledged:
            logger.info("a2a_service: Remote cancel acknowledged for task %s", task_id)
        else:
            logger.debug("a2a_service: Remote cancel failed for task %s", task_id)
        return acknowledged

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
        task_db = self._get_task_db()
        if task_db is None:
            raise ValueError("Task DB is unavailable for HITL reply handling")

        msg = await task_db.get_room_agent_message_by_message_id(message_id)
        if not msg:
            raise ValueError(f"Agent message {message_id} not found")

        agent_url = msg.agent_url
        if not agent_url:
            raise ValueError(f"Agent message {message_id} has no agent_url")

        # Generate a NEW webhook token (original plaintext was never stored)
        webhook_token = task_db.generate_webhook_token()
        webhook_token_hash = task_db.hash_webhook_token(webhook_token)
        token_updated = await task_db.update_webhook_token_hash_on_message(
            message_id, webhook_token_hash
        )
        if not token_updated:
            raise RuntimeError(
                f"Failed to rotate webhook token for message {message_id} — "
                "agent callback would fail verification; aborting reply"
            )

        # When WEBHOOK_BASE_URL is configured, use push-notification mode so the
        # agent can POST back asynchronously — but only if the agent advertises
        # push-notification capability (mirrors the check in send_message_to_tracked_agent).
        # Otherwise fall back to blocking=True.
        webhook_url = self._webhook_base_url

        has_capability = False
        if webhook_url and msg.agent_id:
            agent_record = await task_db.get_agent_by_agent_id(msg.agent_id)
            if agent_record and agent_record.agent_card:
                has_capability = self.has_push_notification_capability(
                    agent_record.agent_card
                )
            else:
                logger.warning(
                    "hitl: could not load agent card for agent %s — disabling push notifications",
                    msg.agent_id,
                )

        if has_capability and webhook_url:
            push_config = {
                "id": message_id,
                "url": f"{webhook_url}/api/v1/webhooks/a2a/{message_id}",
                "token": webhook_token,
            }
            logger.info(
                "hitl: push-notification mode for task %s (callback → %s)",
                message_id,
                push_config["url"],
            )
            hitl_blocking = False
            hitl_timeout = self._push_notification_timeout
        else:
            push_config = None
            hitl_blocking = True
            hitl_timeout = self._default_request_timeout
            reason = (
                "WEBHOOK_BASE_URL not set"
                if not webhook_url
                else "agent missing push-notification capability"
            )
            logger.warning(
                "hitl: %s — using blocking=True for task %s", reason, message_id
            )

        reply_message = {
            "kind": "message",
            "role": "user",
            "parts": [{"kind": "text", "text": user_input}],
            "messageId": str(uuid4()),
            "taskId": task_id,
            "contextId": context_id,
            "referenceTaskIds": [task_id],
        }

        response = await adapter_send_hitl_reply(
            agent_url,
            reply_message,
            push_notification_config=push_config,
            blocking=hitl_blocking,
            timeout=hitl_timeout,
        )

        # Extract response and persist task to DB in one shot so that
        # message_text is written atomically with the task.  Without this,
        # the terminal-state guard in update_task_on_message blocks the
        # subsequent update_task_state_on_message call in hitl_service,
        # leaving message_text empty.
        task_obj = None
        task_result = (
            self._facade_result_to_model(response)
            if response.get("kind") != "error"
            else None
        )

        # --- Extract response text from artifacts or message ---
        response_text: str | None = None
        if hasattr(task_result, "kind") and task_result.kind == "task":
            task_obj = task_result
            if task_obj.artifacts:
                from common.utils.a2a_helpers import extract_text_from_artifacts

                response_text = extract_text_from_artifacts(task_obj.artifacts)
        elif hasattr(task_result, "kind") and task_result.kind == "message":
            parts = getattr(task_result, "parts", []) or []
            for p in parts:
                if hasattr(p, "root") and hasattr(p.root, "text"):
                    response_text = p.root.text
                    break

        # Fallback: check task.status.message for the agent's follow-up prompt
        # (e.g. when task is input_required / auth_required)
        if (
            not response_text
            and task_obj
            and hasattr(task_obj, "status")
            and task_obj.status
        ):
            status_msg = getattr(task_obj.status, "message", None)
            if status_msg:
                parts = getattr(status_msg, "parts", []) or []
                for p in parts:
                    if hasattr(p, "root") and hasattr(p.root, "text"):
                        response_text = p.root.text
                        break
                    if hasattr(p, "text"):
                        response_text = p.text
                        break

        # --- Persist task + message_text together ---
        if task_obj:
            await task_db.update_task_on_message(
                message_id,
                task_obj.model_dump(mode="json"),
                message_text=response_text,
            )

        logger.info(
            "hitl_reply_to_task_sent",
            extra={
                "message_id": message_id,
                "task_id": task_id,
                "context_id": context_id,
            },
        )

        task_state: str | None = None
        if task_obj and hasattr(task_obj, "status") and task_obj.status:
            st = task_obj.status.state
            task_state = st.value if hasattr(st, "value") else str(st)

        return {
            "status": "sent",
            "blocking": hitl_blocking,
            "task_state": task_state,
            "response_text": response_text,
        }


a2a_service = A2AService()
