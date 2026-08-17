from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Protocol
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
from a2a_adapter.inspection import (
    validate_message_data as adapter_validate_message_data,
)
from a2a_adapter.inspection import (
    validate_response_data as adapter_validate_response_data,
)
from a2a_adapter.translators import (
    coerce_parts as adapter_coerce_parts,
)
from a2a_adapter.translators import (
    facade_result_to_model,
    message_to_completed_task,
    resolve_accepted_output_modes,
)
from common.types import (
    AgentCard,
    Message,
    Part,
    Task,
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


RecordCall = Callable[[str | None], Awaitable[None]]
AdapterCall = Callable[..., Awaitable[dict[str, Any]]]


class A2ATaskTrackingPort(Protocol):
    async def create_task_for_tracking(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        message: Message,
        *,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> dict[str, Any]: ...

    async def send_message_to_tracked_agent(
        self,
        *,
        agent_card: AgentCard,
        message: Message,
        message_id: str,
        webhook_token: str,
        context_id: str,
        room_id: str | None,
        agent_id: str | None,
        webhook_base_url: str,
        push_notification_timeout: float,
        default_request_timeout: float,
        accepted_output_modes: Sequence[str] | None,
        record_success: RecordCall,
        record_failure: RecordCall,
        send_message: AdapterCall,
    ) -> dict[str, Any]: ...

    async def reply_to_task(
        self,
        *,
        message_id: str,
        task_id: str,
        context_id: str,
        user_input: str,
        webhook_base_url: str,
        push_notification_timeout: float,
        default_request_timeout: float,
        send_hitl_reply: AdapterCall,
        outbound_message_id: str | None = None,
    ) -> dict[str, Any]: ...


class A2AService:
    def __init__(self):
        self._task_tracking: A2ATaskTrackingPort | None = None
        self._call_counter: Any | None = None
        self._runtime_config = A2ARuntimeConfig()

    def bind_runtime_config(self, config: A2ARuntimeConfig) -> None:
        self._runtime_config = config

    def bind_task_tracking(self, task_tracking: A2ATaskTrackingPort) -> None:
        self._task_tracking = task_tracking

    def bind_call_counter(self, call_counter: Any) -> None:
        self._call_counter = call_counter

    def _require_task_tracking(self) -> A2ATaskTrackingPort:
        task_tracking = getattr(self, "_task_tracking", None)
        if task_tracking is None:
            raise RuntimeError(
                "A2AService.bind_task_tracking() not called - startup incomplete"
            )
        return task_tracking

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

        except Exception as exc:
            logger.error(
                "agent_card_fetch_failed",
                extra={
                    "agent_url": agent_url,
                    "error_type": type(exc).__name__,
                },
            )
            raise A2AServiceError() from exc

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
        task_tracking = self._require_task_tracking()
        return await task_tracking.create_task_for_tracking(
            current_message,
            agent_card,
            message,
            step_number=step_number,
            total_steps=total_steps,
        )

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
        task_tracking = self._require_task_tracking()
        return await task_tracking.send_message_to_tracked_agent(
            agent_card=agent_card,
            message=message,
            message_id=message_id,
            webhook_token=webhook_token,
            context_id=context_id,
            room_id=room_id,
            agent_id=agent_id,
            webhook_base_url=self._webhook_base_url,
            push_notification_timeout=self._push_notification_timeout,
            default_request_timeout=self._default_request_timeout,
            accepted_output_modes=self._resolve_accepted_modes(agent_card),
            record_success=lambda aid: self._record_call(aid, success=True),
            record_failure=lambda aid: self._record_call(aid, success=False),
            send_message=adapter_send_message,
        )

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
            logger.debug(
                "a2a_sync_send_started",
                extra={
                    "agent": agent_card.name,
                    "agent_url": agent_card.url,
                    "operation": "message_send",
                },
            )
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
                logger.error(
                    "a2a_sync_send_rejected",
                    extra={
                        "agent": agent_card.name,
                        "operation": "message_send",
                        "outcome": "error",
                    },
                )
                raise A2AServiceError(error_msg)
            success = True
            return response

        except A2AServiceError:
            raise
        except Exception as exc:
            logger.error(
                "a2a_sync_send_failed",
                extra={
                    "agent": agent_card.name,
                    "operation": "message_send",
                    "outcome": "error",
                    "error_type": type(exc).__name__,
                },
            )
            raise A2AServiceError(str(exc)) from exc
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
            logger.debug(
                "a2a_stream_started",
                extra={
                    "agent": agent_card.name,
                    "agent_url": agent_card.url,
                    "operation": "message_stream",
                },
            )
            async with aclosing(
                adapter_stream_message(
                    agent_card,
                    message,
                    accepted_output_modes=self._resolve_accepted_modes(agent_card),
                    timeout=self._default_request_timeout,
                )
            ) as response_stream:
                async for response in response_stream:
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
            logger.debug(
                "a2a_streaming_transport_selected",
                extra={"agent_url": agent_card.url},
            )
            async with aclosing(
                self.send_message_streaming(
                    agent_card,
                    message,
                    agent_id=agent_id,
                )
            ) as event_stream:
                async for event in event_stream:
                    yield event

        else:
            logger.debug(
                "a2a_sync_transport_selected",
                extra={"agent_url": agent_card.url},
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
            logger.error(
                "a2a_inspection_send_failed",
                extra={"error_type": type(e).__name__},
            )
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
        validation_errors, is_transport_error = adapter_validate_response_data(result)
        if is_transport_error:
            return InsepectionCenterConnectionValidationResponse(
                agent_url="",
                result=validation_errors,
                is_valid=False,
                status_code=500,
            )

        return InsepectionCenterConnectionValidationResponse(
            agent_url="", result=validation_errors, is_valid=True, status_code=200
        )

    def validate_message(self, data: dict[str, Any]) -> list[str]:
        """Validate an incoming message from the agent based on its kind."""
        return adapter_validate_message_data(data)

    async def process_a2a_response(self, response: dict[str, Any]) -> Any:
        if response.get("kind") == "error":
            raise A2AServiceError()

        response_data = self._facade_result_to_model(response)
        logger.debug(
            "a2a_response_parsed",
            extra={
                "response_kind": response.get("kind"),
                "result_type": (
                    type(response_data).__name__ if response_data is not None else None
                ),
                "outcome": "success" if response_data is not None else "empty",
            },
        )
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
        outbound_message_id: str | None = None,
    ) -> dict:
        """Send a follow-up message to an existing A2A task (for HITL replies).

        Uses the same task_id and context_id to continue the conversation
        rather than starting a new task.
        """
        task_tracking = self._require_task_tracking()
        return await task_tracking.reply_to_task(
            message_id=message_id,
            task_id=task_id,
            context_id=context_id,
            user_input=user_input,
            webhook_base_url=self._webhook_base_url,
            push_notification_timeout=self._push_notification_timeout,
            default_request_timeout=self._default_request_timeout,
            send_hitl_reply=adapter_send_hitl_reply,
            outbound_message_id=outbound_message_id,
        )


a2a_service = A2AService()
