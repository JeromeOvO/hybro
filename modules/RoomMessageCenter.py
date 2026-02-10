import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from a2a.types import (
    AgentCard,
    GetTaskRequest,
    JSONRPCErrorResponse,
    Message,
    Part,
    Role,
    Task,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TextPart,
)

from common.utils.a2a_helpers import (
    extract_error_message,
    extract_text_from_artifacts,
    get_message_from_task,
    get_text_from_a2a_response,
    get_text_from_message,
)
from common.utils.context_utils import get_context_stats
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.agent import Agent, AgentStatus
from models.memory import MemoryContent, RoomMemory
from models.request import OrchestrationRequest, RoomCenterAgentMessageRequest
from models.response import OrchestrationResponse
from models.room import RoomAgentMessage
from services.a2a_constants import (
    TERMINAL_STATES,
    SSEProcessingStatus,
    is_failure_state,
    is_terminal_state,
)
from services.a2a_service import a2a_service
from services.database_service import db_service
from services.debate_service import debate_service
from services.memory_service import room_memory_service
from services.notification_service import notification_service
from services.rate_limit_service import rate_limit_service
from services.room_coordinator_service import room_coordinator_service
from services.room_services import room_services
from services.sse_services import sse_manager
from services.task_service import task_service

logger = get_logger(__name__)


class ProcessingStatus(Enum):
    """Status of message processing operations."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"  # Queue paused waiting for push notification task


@dataclass
class ProcessingResult:
    """Result of message processing with optional metadata."""

    status: ProcessingStatus
    response_text: str = ""
    # message_id is set when status is PAUSED (for push notification tasks)
    # This is used in webhook URLs for task tracking
    message_id: str | None = None


@dataclass
class MessageStreamingState:
    """Tracks mutable streaming state across sub-handlers during a single streaming session."""

    full_response_text: str = ""
    accumulated_parts: list[Part] = field(default_factory=list)
    agent_message_id: str | None = None
    message_added_to_history: bool = False


class RoomMessageCenter:
    """Room user message processing: agent communication,
    streaming/sync responses, queue management, and memory updates."""

    def __init__(self):
        self.a2a_service = a2a_service
        self.room_services = room_services
        self.room_memory_service = room_memory_service
        self.database_service = db_service
        self.debate_service = debate_service
        self.sse_manager = sse_manager
        self.room_coordinator_service = room_coordinator_service
        self.rate_limit_service = rate_limit_service
        self.task_service = task_service
        self.notification_service = notification_service

    async def _setup_task_tracking(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        prepared_message: Message,
        room_id: str,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> dict[str, Any] | None:
        try:
            logger.info(
                "RoomMessageCenter: Setting up task tracking for message %s (step %s/%s, agent: %s)",
                current_message.message_id,
                step_number,
                total_steps,
                agent_card.name,
            )
            task_info = await self.a2a_service.create_task_for_tracking(
                room_id,
                current_message.user_id or "unknown",
                agent_card,
                prepared_message,
                agent_id=current_message.agent_id,
                related_message_id=current_message.related_message_id,
                step_number=step_number,
                total_steps=total_steps,
                message_id=current_message.message_id,
            )
            created_at = task_info.get("created_at")

            # Extract task content from the message for frontend display
            task_content = None
            if current_message.message_content:
                task_content = current_message.message_content.message_text

            logger.info(
                "RoomMessageCenter: Sending task_submitted SSE for step %s/%s, task_content: %s",
                step_number,
                total_steps,
                task_content[:50] if task_content else "None",
            )

            # Send task_submitted SSE before agent call
            await self.sse_manager.send_task_submitted(
                room_id=room_id,
                message_id=current_message.message_id,
                task_id="pending",
                agent_name=agent_card.name,
                agent_id=current_message.agent_id,
                status="working",
                related_message_id=current_message.related_message_id,
                created_at=created_at,
                step_number=step_number,
                total_steps=total_steps,
                task_content=task_content,
            )
            return task_info
        except Exception as exc:
            logger.warning("Failed to setup task tracking: %s", exc)
            return None

    async def _poll_task_until_complete(
        self,
        agent_card: AgentCard,
        task_id: str,
        message_id: str,
        timeout_seconds: int = 120,
        initial_delay: float = 0.5,
        max_delay: float = 5.0,
    ) -> Task | None:
        """
        Poll an agent for task completion with exponential backoff.

        This is used for non-push-notification agents that return a "task" response
        but are expected to complete quickly. We poll until the task reaches a
        terminal state or the timeout is reached.

        Args:
            agent_card: The agent's card information
            task_id: The agent's task ID to poll
            message_id: The message ID (for logging)
            timeout_seconds: Maximum time to wait for completion (default: 120s)
            initial_delay: Initial delay between polls (default: 0.5s)
            max_delay: Maximum delay between polls (default: 5s)

        Returns:
            The completed Task if found, None if timeout or error
        """
        start_time = asyncio.get_event_loop().time()
        delay = initial_delay
        poll_count = 0

        logger.info(
            "RoomMessageCenter: Starting poll for task %s (agent task: %s), "
            "timeout: %ds",
            message_id,
            task_id,
            timeout_seconds,
        )

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_seconds:
                logger.warning(
                    "RoomMessageCenter: Poll timeout for task %s after %.1fs "
                    "(%d polls)",
                    message_id,
                    elapsed,
                    poll_count,
                )
                return None

            # Wait before polling (except first iteration)
            if poll_count > 0:
                await asyncio.sleep(delay)
                # Exponential backoff with cap
                delay = min(delay * 1.5, max_delay)

            poll_count += 1

            try:
                a2a_client = await self.a2a_service.create_a2a_client(agent_card)
                response = await a2a_client.get_task(
                    GetTaskRequest(id=task_id, params=TaskQueryParams(id=task_id))
                )

                if not response or isinstance(response.root, JSONRPCErrorResponse):
                    logger.warning(
                        "RoomMessageCenter: Poll %d for task %s returned error/empty",
                        poll_count,
                        message_id,
                    )
                    continue

                task = response.root.result
                if task is None:
                    logger.warning(
                        "RoomMessageCenter: Poll %d for task %s returned no result",
                        poll_count,
                        message_id,
                    )
                    continue

                state = task.status.state
                state_value = state.value if hasattr(state, "value") else str(state)

                if is_terminal_state(state):
                    logger.info(
                        "RoomMessageCenter: Task %s completed with state %s "
                        "after %.1fs (%d polls)",
                        message_id,
                        state_value,
                        asyncio.get_event_loop().time() - start_time,
                        poll_count,
                    )
                    return task

                logger.debug(
                    "RoomMessageCenter: Poll %d for task %s: state=%s",
                    poll_count,
                    message_id,
                    state_value,
                )

            except Exception as e:
                logger.warning(
                    "RoomMessageCenter: Poll %d for task %s failed: %s",
                    poll_count,
                    message_id,
                    e,
                )
                # Continue polling despite errors (might be transient)
                continue

    async def _handle_streaming_response_for_room(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        prepared_message: Message,
        room_id: str,
        user_message_id: str,
        send_sse: bool = False,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> tuple[ProcessingStatus, str]:
        """
        Handle streaming responses from an agent for a room message.

        This method:
        1. Creates a task record for tracking (for consistent UX)
        2. Sends task_submitted SSE before streaming starts
        3. Streams responses from the agent in real-time
        4. Processes each event type (message, task, status-update, artifact-update)
        5. Updates the database as events arrive
        6. Sends task_update SSE when streaming completes

        Args:
            current_message: The RoomAgentMessage being processed
            agent_card: The agent's card information
            prepared_message: The A2A message to send to the agent
            room_id: The room ID for SSE events
            user_message_id: The user message ID for cancellation checks
            send_sse: Whether to send SSE notifications to frontend (default: False)
            step_number: Current step number in the workflow (1-indexed)
            total_steps: Total number of steps in the workflow

        Returns:
            Tuple of (status: ProcessingStatus, full_response_text: str)
        """
        # Step 1: Create task record and emit task_submitted SSE
        task_info = await self._setup_task_tracking(
            current_message,
            agent_card,
            prepared_message,
            room_id,
            step_number=step_number,
            total_steps=total_steps,
        )
        # Use current_message.message_id for task updates (task_info["message_id"] is the same)
        # Only send task updates if task_info was successfully created
        created_at = task_info.get("created_at") if task_info else None

        # Track streaming state across sub-handlers
        message_streaming_state = MessageStreamingState()

        async for a2a_response in self.a2a_service.send_message_streaming(
            agent_card, prepared_message
        ):
            # Check for cancellation during streaming
            if self.sse_manager.is_cancelled(user_message_id):
                return await self._handle_streaming_cancellation(
                    room_id,
                    current_message,
                    agent_card,
                    user_message_id,
                    task_info,
                    created_at,
                    step_number,
                    total_steps,
                    message_streaming_state.full_response_text,
                )

            # Handle JSON-RPC errors
            if isinstance(a2a_response.root, JSONRPCErrorResponse):
                return await self._handle_streaming_error(
                    a2a_response,
                    room_id,
                    current_message,
                    agent_card,
                    task_info,
                    created_at,
                    send_sse,
                    step_number,
                    total_steps,
                    message_streaming_state.full_response_text,
                )

            # Extract result from response
            result = a2a_response.root.result
            data_kind = result.kind

            match data_kind:
                case "message":
                    await self._handle_stream_message_chunk(
                        result,
                        current_message,
                        message_streaming_state,
                        room_id,
                        send_sse,
                    )
                case "task":
                    await self._handle_stream_task_event(result)
                case "status-update":
                    await self._handle_stream_status_update(
                        result,
                        current_message,
                        agent_card,
                        message_streaming_state,
                        room_id,
                        task_info,
                        created_at,
                        send_sse,
                        step_number,
                        total_steps,
                    )
                case "artifact-update":
                    await self._handle_stream_artifact_update(
                        result,
                        current_message,
                        room_id,
                        send_sse,
                    )

        return await self._finalize_streaming(
            current_message,
            agent_card,
            message_streaming_state,
            room_id,
            task_info,
            created_at,
            step_number,
            total_steps,
        )

    # --- Streaming sub-handlers (Phase 2 decomposition) ---

    async def _handle_streaming_cancellation(
        self,
        room_id: str,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        user_message_id: str,
        task_info: dict | None,
        created_at: str | None,
        step_number: int | None,
        total_steps: int | None,
        full_response_text: str,
    ) -> tuple[ProcessingStatus, str]:
        """Handle cancellation during streaming."""
        logger.info(
            "RoomMessageCenter: Streaming cancelled for message %s, stopping all processing",
            user_message_id,
        )
        await self.notification_service.send_task_update(
            room_id=room_id,
            message_id=current_message.message_id if task_info else None,
            status="canceled",
            agent_card=agent_card,
            agent_id=current_message.agent_id,
            created_at=created_at,
            step_number=step_number,
            total_steps=total_steps,
        )
        await self.sse_manager.send_processing_status(
            room_id, SSEProcessingStatus.CANCELED, user_message_id
        )
        self.sse_manager.clear_cancellation(user_message_id)
        return ProcessingStatus.CANCELED, full_response_text

    async def _handle_streaming_error(
        self,
        a2a_response,
        room_id: str,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        task_info: dict | None,
        created_at: str | None,
        send_sse: bool,
        step_number: int | None,
        total_steps: int | None,
        full_response_text: str,
    ) -> tuple[ProcessingStatus, str]:
        """Handle JSON-RPC error during streaming."""
        error_message = a2a_response.root.error.model_dump_json()
        logger.error("RoomMessageCenter: Agent error: %s", error_message)
        await self.notification_service.send_task_update(
            room_id=room_id,
            message_id=current_message.message_id if task_info else None,
            status="failed",
            agent_card=agent_card,
            agent_id=current_message.agent_id,
            created_at=created_at,
            error=error_message,
            step_number=step_number,
            total_steps=total_steps,
        )
        if send_sse:
            await self.sse_manager.send_error(room_id, error_message)
        return ProcessingStatus.FAILED, full_response_text

    async def _handle_stream_message_chunk(
        self,
        result,
        current_message: RoomAgentMessage,
        message_streaming_state,
        room_id: str,
        send_sse: bool,
    ) -> None:
        """Handle a 'message' event during streaming: accumulate parts, save to DB, send SSE tokens."""
        message_list = result.parts

        message_streaming_state.accumulated_parts.extend(message_list)

        if message_streaming_state.agent_message_id is None:
            message_streaming_state.agent_message_id = result.message_id

        content = "".join(
            part.root.text if part.root and hasattr(part.root, "text") else ""
            for part in message_list
        )
        message_streaming_state.full_response_text += content

        logger.debug(
            "RoomMessageCenter: Full accumulated message for %s: %s",
            current_message.message_id,
            message_streaming_state.full_response_text,
        )

        if (
            current_message.message_content
            and current_message.message_content.message_task
        ):
            if current_message.message_content.message_task.history is None:
                current_message.message_content.message_task.history = []

            updated_message = Message(
                kind="message",
                role=result.role,
                message_id=message_streaming_state.agent_message_id,
                parts=message_streaming_state.accumulated_parts.copy(),
            )

            if not message_streaming_state.message_added_to_history:
                logger.debug(
                    "RoomMessageCenter: First message chunk, appending to history"
                )
                current_message.message_content.message_task.history.append(
                    updated_message
                )
                message_streaming_state.message_added_to_history = True
            else:
                logger.debug("RoomMessageCenter: Updating existing message in history")
                for i, msg in enumerate(
                    current_message.message_content.message_task.history
                ):
                    if (
                        hasattr(msg, "message_id")
                        and msg.message_id == message_streaming_state.agent_message_id
                    ):
                        current_message.message_content.message_task.history[i] = (
                            updated_message
                        )
                        logger.debug(
                            "RoomMessageCenter: Replaced message at index %d with %d parts",
                            i,
                            len(message_streaming_state.accumulated_parts),
                        )
                        break

            if current_message.message_content.message_task.history:
                for idx, hist_msg in enumerate(
                    current_message.message_content.message_task.history
                ):
                    part_count = (
                        len(hist_msg.parts) if hasattr(hist_msg, "parts") else 0
                    )
                    logger.debug(
                        "RoomMessageCenter: History[%d] has %d parts",
                        idx,
                        part_count,
                    )

            update_response = (
                await self.room_services.update_agent_message_by_message_id(
                    RoomCenterAgentMessageRequest(
                        message_id=current_message.message_id,
                        message=current_message,
                    )
                )
            )

            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update agent message incrementally: %s",
                    update_response.error,
                )
            else:
                logger.debug(
                    "RoomMessageCenter: Successfully saved message to database with %d total parts",
                    len(message_streaming_state.accumulated_parts),
                )

        if send_sse:
            await self.sse_manager.send_agent_token(
                room_id,
                current_message.message_id,
                current_message.agent_id,
                content,
            )

    async def _handle_stream_task_event(self, result) -> None:
        """Handle a 'task' event during streaming (log only)."""
        status = result.status
        logger.debug(
            "RoomMessageCenter: Task update for task %s: %s",
            result,
            status.state if status else "no status",
        )

    async def _handle_stream_status_update(
        self,
        result,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        message_streaming_state,
        room_id: str,
        task_info: dict | None,
        created_at: str | None,
        send_sse: bool,
        step_number: int | None,
        total_steps: int | None,
    ) -> None:
        """Handle a 'status-update' event during streaming."""
        state = result.status.state
        logger.info(
            "RoomMessageCenter: Status update for message %s: %s",
            current_message.message_id,
            state,
        )

        a2a_status_message_text: str | None = None
        if result.status.message:
            a2a_status_message_text = get_text_from_message(result.status.message)
            if a2a_status_message_text:
                logger.info(
                    "RoomMessageCenter: Agent status message for %s: %s",
                    current_message.message_id,
                    a2a_status_message_text[:100],
                )

        if (
            current_message.message_content
            and current_message.message_content.message_task
        ):
            if current_message.message_content.message_task.status is None:
                current_message.message_content.message_task.status = TaskStatus(
                    state=TaskState.submitted
                )
            current_message.message_content.message_task.status.state = state

            update_response = (
                await self.room_services.update_agent_message_by_message_id(
                    RoomCenterAgentMessageRequest(
                        message_id=current_message.message_id,
                        message=current_message,
                    )
                )
            )
            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update message status: %s",
                    update_response.error,
                )

        if state in TERMINAL_STATES:
            logger.info(
                "RoomMessageCenter: Final status for message %s: %s",
                current_message.message_id,
                state,
            )
            task = await self.task_service.get_task_from_agent(
                agent_card, result.task_id
            )
            if task is not None:
                message = get_message_from_task(task)
                await self._handle_a2a_response_for_room(current_message, message)
                fetched_text = get_text_from_a2a_response(message)
                if fetched_text:
                    if (
                        message_streaming_state.full_response_text
                        and fetched_text != message_streaming_state.full_response_text
                    ):
                        logger.warning(
                            "RoomMessageCenter: Fetched final text differs from accumulated "
                            "streaming text for message %s (accumulated len=%d, fetched len=%d)",
                            current_message.message_id,
                            len(message_streaming_state.full_response_text),
                            len(fetched_text),
                        )
                    message_streaming_state.full_response_text = fetched_text
                else:
                    logger.warning(
                        "RoomMessageCenter: Fetched task returned empty text for message %s, "
                        "keeping accumulated streaming text (len=%d)",
                        current_message.message_id,
                        len(message_streaming_state.full_response_text),
                    )
            else:
                logger.error(
                    "RoomMessageCenter: Failed to retrieve final task for task id %s",
                    result.task_id,
                )

        if send_sse:
            if a2a_status_message_text and state not in TERMINAL_STATES:
                await self.notification_service.send_task_update(
                    room_id=room_id,
                    message_id=current_message.message_id if task_info else None,
                    status=state,
                    agent_card=agent_card,
                    agent_id=current_message.agent_id,
                    created_at=created_at,
                    status_message=a2a_status_message_text,
                    step_number=step_number,
                    total_steps=total_steps,
                )
            await self.sse_manager.send_processing_status(
                room_id,
                state,
                current_message.message_id,
                details=f"Agent {current_message.agent_id} status: {state}",
            )

    async def _handle_stream_artifact_update(
        self,
        result,
        current_message: RoomAgentMessage,
        room_id: str,
        send_sse: bool,
    ) -> None:
        """Handle an 'artifact-update' event during streaming."""
        artifact_result = getattr(result, "artifact", None)
        append = result.append if hasattr(result, "append") else False
        last_chunk = result.last_chunk if hasattr(result, "last_chunk") else False

        if (
            artifact_result
            and current_message.message_content
            and current_message.message_content.message_task
        ):
            logger.debug(
                "RoomMessageCenter: Artifact update for message %s, append=%s, last_chunk=%s",
                current_message.message_id,
                append,
                last_chunk,
            )

            if current_message.message_content.message_task.artifacts is None:
                current_message.message_content.message_task.artifacts = []
            current_artifacts = current_message.message_content.message_task.artifacts

            artifact_id = getattr(artifact_result, "artifact_id", None)
            if append and artifact_id:
                existing_artifact = next(
                    (a for a in current_artifacts if a.artifact_id == artifact_id),
                    None,
                )
                if existing_artifact:
                    artifact_parts = getattr(artifact_result, "parts", None)
                    if artifact_parts:
                        existing_artifact.parts.extend(artifact_parts)
                else:
                    current_artifacts.append(artifact_result)
            else:
                current_artifacts.append(artifact_result)

            update_response = (
                await self.room_services.update_agent_message_by_message_id(
                    RoomCenterAgentMessageRequest(
                        message_id=current_message.message_id,
                        message=current_message,
                    )
                )
            )
            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update message artifacts: %s",
                    update_response.error,
                )

            if send_sse:
                await self.sse_manager.send_artifact_update(
                    room_id,
                    current_message.message_id,
                    current_message.agent_id,
                    artifact_result,
                    append=append,
                    last_chunk=last_chunk,
                )

    async def _finalize_streaming(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        message_streaming_state,
        room_id: str,
        task_info: dict | None,
        created_at: str | None,
        step_number: int | None,
        total_steps: int | None,
    ) -> tuple[ProcessingStatus, str]:
        """Finalize streaming: persist final state, send task_update SSE."""
        logger.info(
            "RoomMessageCenter: Streaming complete for message %s, "
            "total parts: %d, full text length: %d",
            current_message.message_id,
            len(message_streaming_state.accumulated_parts),
            len(message_streaming_state.full_response_text),
        )

        already_terminal = (
            current_message.message_content
            and current_message.message_content.message_task
            and current_message.message_content.message_task.status
            and is_terminal_state(
                current_message.message_content.message_task.status.state
            )
        )

        if (
            current_message.message_content
            and current_message.message_content.message_task
            and not already_terminal
        ):
            current_message.message_content.message_task.status = TaskStatus(
                state=TaskState.completed
            )
            if message_streaming_state.full_response_text:
                current_message.message_content.message_text = (
                    message_streaming_state.full_response_text
                )
            current_message.task_updated_at = utcnow()

            update_response = (
                await self.room_services.update_agent_message_by_message_id(
                    RoomCenterAgentMessageRequest(
                        message_id=current_message.message_id,
                        message=current_message,
                    )
                )
            )
            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update message status to completed: %s",
                    update_response.error,
                )

        if already_terminal:
            final_state = current_message.message_content.message_task.status.state
            final_state_value = (
                final_state.value if hasattr(final_state, "value") else str(final_state)
            )
            final_error = None
            if is_failure_state(final_state):
                if (
                    current_message.message_content.message_task.status.message
                    and current_message.message_content.message_task.status.message.parts
                ):
                    for part in current_message.message_content.message_task.status.message.parts:
                        part_root = getattr(part, "root", part)
                        if hasattr(part_root, "text"):
                            final_error = part_root.text
                            break
                if not final_error:
                    final_error = f"Task {final_state_value}"

            await self.notification_service.send_task_update(
                room_id=room_id,
                message_id=current_message.message_id if task_info else None,
                status=final_state_value,
                agent_card=agent_card,
                agent_id=current_message.agent_id,
                created_at=created_at,
                content=message_streaming_state.full_response_text
                if final_state_value == "completed"
                else None,
                error=final_error,
                step_number=step_number,
                total_steps=total_steps,
            )

            if is_failure_state(final_state):
                return (
                    ProcessingStatus.FAILED,
                    message_streaming_state.full_response_text,
                )
        else:
            await self.notification_service.send_task_update(
                room_id=room_id,
                message_id=current_message.message_id if task_info else None,
                status="completed",
                agent_card=agent_card,
                agent_id=current_message.agent_id,
                created_at=created_at,
                content=message_streaming_state.full_response_text,
                step_number=step_number,
                total_steps=total_steps,
            )

        return ProcessingStatus.SUCCESS, message_streaming_state.full_response_text

    async def _handle_a2a_response_for_room(
        self, room_agent_message: RoomAgentMessage, message_data: None | Task | Message
    ) -> bool:
        # Add null check for process_response
        if message_data is None:
            logger.error(
                "RoomMessageCenter: process_a2a_response returned None for agent message "
            )
            return False

        if message_data.kind == "task":
            room_agent_message.message_content.message_task = message_data
            update_response = (
                await self.room_services.update_agent_message_by_message_id(
                    RoomCenterAgentMessageRequest(
                        message_id=room_agent_message.message_id,
                        message=room_agent_message,
                    )
                )
            )
            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update agent message with task"
                )
                return False
            return True

        elif message_data.kind == "message":
            if (
                room_agent_message.message_content
                and room_agent_message.message_content.message_task
            ):
                if room_agent_message.message_content.message_task.history is None:
                    room_agent_message.message_content.message_task.history = []
                # append new message
                room_agent_message.message_content.message_task.history.append(
                    message_data
                )

            update_response = (
                await self.room_services.update_agent_message_by_message_id(
                    RoomCenterAgentMessageRequest(
                        message_id=room_agent_message.message_id,
                        message=room_agent_message,
                    )
                )
            )

            if not update_response.success:
                logger.error(
                    "RoomMessageCenter: Failed to update agent message with message: %s",
                    update_response.error,
                )
                return False
            return True
        # Neither task nor message
        logger.error(
            "RoomMessageCenter: Unexpected data kind in A2A response: %s",
            message_data.kind,
        )
        return False

    async def process_room_user_message(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """
        Process a room user message by executing all related agent messages in sequence.

        This method:
        1. Gets room memory context
        2. Queries all agent messages related to the user message
        3. Processes each agent message in order using streaming
        4. Updates room memory after all agents have responded
        5. Sends SSE events to the room for real-time updates

        Args:
            request: Contains room_id and room_user_message_id

        Returns:
            OrchestrationResponse with success status
        """
        logger.debug(
            "RoomMessageCenter: Starting to process room user message %s in room %s",
            request.room_user_message_id,
            request.room_id,
        )

        # Validate request
        validation_response = self._validate_room_message_request(request)
        if validation_response:
            return validation_response

        room_id = request.room_id
        room_user_message_id = request.room_user_message_id

        # Get user_id from the user message for rate limiting
        user_message = await self.database_service.get_room_user_message_by_message_id(
            room_user_message_id
        )
        user_id = user_message.user_id if user_message else None

        # Query agent messages to process
        query_response = (
            await self.room_services.inquiry_agent_messages_by_related_message_id(
                RoomCenterAgentMessageRequest(related_message_id=room_user_message_id)
            )
        )
        if not query_response.success:
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error=query_response.error,
                status_code=500,
            )

        # Process all agent messages in sequence
        message_queue = (
            deque(query_response.message_list)
            if query_response.message_list is not None
            else deque()
        )

        logger.debug(
            "RoomMessageCenter: Starting to process %d agent messages for room %s and user message %s",
            len(message_queue),
            room_id,
            room_user_message_id,
        )

        # Check for cancellation before processing agent messages
        if self.sse_manager.is_cancelled(room_user_message_id):
            logger.info(
                "RoomMessageCenter: Processing cancelled for message %s, stopping all processing",
                room_user_message_id,
            )
            await self.sse_manager.send_processing_status(
                room_id, SSEProcessingStatus.CANCELED, room_user_message_id
            )
            self.sse_manager.clear_cancellation(room_user_message_id)
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        success = await self._process_agent_message_queue(
            message_queue,
            room_id,
            room_user_message_id,
            request_user_id=user_id,
        )

        if not success:
            return OrchestrationResponse(
                success=False,
                error="Failed to process agent messages",
                status_code=500,
            )

        # Let the local room coordinator perform any post-processing logic
        # such as generating debate summaries. Coordination failures should
        # not break the main message processing flow.
        await self.room_coordinator_service.on_room_user_message_completed(
            room_id, room_user_message_id
        )

        # Send completion status
        await self.sse_manager.send_processing_status(
            room_id, SSEProcessingStatus.COMPLETED, room_user_message_id
        )

        # Update room memory with new content (fetch fresh from DB for logging)
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        await self._update_room_memory_after_processing(room_id, room_memory)

        return OrchestrationResponse(
            room_id=room_id, success=True, error=None, status_code=200
        )

    def _validate_room_message_request(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse | None:
        """Validate the room message request parameters."""
        if request.room_id is None:
            return OrchestrationResponse(
                success=False,
                error="Room id is required",
                status_code=400,
            )

        if request.room_user_message_id is None:
            return OrchestrationResponse(
                success=False,
                error="Room user message id is required",
                status_code=400,
            )

        return None

    async def _process_agent_message_queue(
        self,
        message_queue: deque,
        room_id: str,
        user_message_id: str,
        request_user_id: str | None = None,
    ) -> bool:
        """
        Process all messages in the queue sequentially.

        Args:
            message_queue: Queue of agent messages to process
            room_id: The room ID
            user_message_id: The user message ID for cancellation checks
            request_user_id: The ID of the user making the request (for rate limiting)

        Returns:
            True if processing completed successfully (or was paused for continuation)
        """
        logger.info(
            "RoomMessageCenter: Starting to process message queue with %d messages",
            len(message_queue),
        )
        while len(message_queue) > 0:
            current_message = message_queue.popleft()
            logger.info(
                "RoomMessageCenter: Processing message %s (step %s/%s), %d messages remaining in queue",
                current_message.message_id,
                current_message.step_number,
                current_message.total_steps,
                len(message_queue),
            )

            # Check for cancellation before processing each agent message
            if self.sse_manager.is_cancelled(user_message_id):
                logger.info(
                    "RoomMessageCenter: Message processing cancelled for %s, stopping all processing",
                    user_message_id,
                )
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.CANCELED, user_message_id
                )
                self.sse_manager.clear_cancellation(user_message_id)
                return True  # Return success to avoid error status

            # Assign agent if not already assigned
            if current_message.agent_id is None:
                agent = await self._assign_agent(current_message)
                if agent is None:
                    logger.error(
                        "RoomMessageCenter: Failed to assign agent for message %s",
                        current_message.message_id,
                    )
                    return False
            else:
                # Agent already assigned, fetch it and verify it's active
                agent = await self.database_service.get_agent_by_agent_id(
                    current_message.agent_id
                )
                if agent is None:
                    logger.error(
                        "RoomMessageCenter: Assigned agent %s not found for message %s",
                        current_message.agent_id,
                        current_message.message_id,
                    )
                    return False

                # Check if the assigned agent is still active
                if agent.agent_status != AgentStatus.active:
                    logger.warning(
                        "RoomMessageCenter: Assigned agent %s is not active (status=%s), re-assigning for message %s",
                        current_message.agent_id,
                        agent.agent_status,
                        current_message.message_id,
                    )
                    # Clear the agent_id and re-assign
                    current_message.agent_id = None
                    agent = await self._assign_agent(current_message)
                    if agent is None:
                        logger.error(
                            "RoomMessageCenter: Failed to re-assign agent for message %s after inactive agent",
                            current_message.message_id,
                        )
                        return False

            # Check rate limits before processing (only if user_id is available)
            if request_user_id:
                rate_limit_result = await self.rate_limit_service.check_rate_limit(
                    agent_id=agent.agent_id,
                    user_id=request_user_id,
                    rate_limit_per_user=agent.rate_limit_per_user_per_hour,
                    rate_limit_system=agent.rate_limit_system_per_hour,
                )

                if not rate_limit_result.allowed:
                    logger.warning(
                        "RoomMessageCenter: Rate limit exceeded for agent %s, user %s: %s",
                        agent.agent_id,
                        request_user_id,
                        rate_limit_result.reason,
                    )
                    # Send rate limit error via SSE with full details
                    await self.sse_manager.send_rate_limit_error(
                        room_id=room_id,
                        message_id=user_message_id,
                        agent_id=agent.agent_id,
                        reason=rate_limit_result.reason or "Rate limit exceeded",
                        retry_after_seconds=rate_limit_result.retry_after_seconds,
                        user_requests_used=rate_limit_result.user_requests_used,
                        user_requests_limit=rate_limit_result.user_requests_limit,
                        system_requests_used=rate_limit_result.system_requests_used,
                        system_requests_limit=rate_limit_result.system_requests_limit,
                    )
                    await self.sse_manager.send_processing_status(
                        room_id, SSEProcessingStatus.RATE_LIMITED, user_message_id
                    )
                    # Return True: rate limiting is expected behavior, not a server error
                    return True

            # Process the agent message
            # Step info comes from the RoomAgentMessage (set during task decomposition)
            # For direct chat (single agent, no debate), skip step progress UI
            is_direct_chat = bool(
                current_message.extend_info
                and current_message.extend_info.get("is_direct_chat")
            )
            result = await self._process_single_agent_message(
                current_message,
                room_id,
                agent,
                user_message_id,
                step_number=None if is_direct_chat else current_message.step_number,
                total_steps=None if is_direct_chat else current_message.total_steps,
            )

            if result.status == ProcessingStatus.FAILED:
                return False
            elif result.status == ProcessingStatus.CANCELED:
                # Graceful cancellation - don't treat as error
                return True
            elif result.status == ProcessingStatus.PAUSED:
                # Push notification task submitted - save continuation state
                # First, queue up next messages so they're included in continuation
                if not is_direct_chat:
                    await self._queue_next_messages(
                        current_message, message_queue, room_id
                    )

                if result.message_id and len(message_queue) > 0:
                    await self._save_queue_continuation(
                        message_id=result.message_id,
                        message_queue=message_queue,
                        room_id=room_id,
                        user_message_id=user_message_id,
                        request_user_id=request_user_id,
                        current_agent=agent,
                    )
                    logger.info(
                        "RoomMessageCenter: Queue paused for message %s with %d remaining messages",
                        result.message_id,
                        len(message_queue),
                    )
                return True  # Successfully paused, webhook will resume

            # Record the request for rate limiting (only if user_id is available)
            if request_user_id:
                await self.rate_limit_service.record_request(
                    agent_id=agent.agent_id,
                    user_id=request_user_id,
                )

            # Store agent response in conversation history (ChatGPT/Claude style)
            if result.response_text:
                await self.room_memory_service.add_agent_response_to_memory(
                    room_id=room_id,
                    agent_id=current_message.agent_id,
                    agent_name=agent.agent_card.name if agent else "Agent",
                    response_text=result.response_text,
                )

            # Queue up next messages in the chain (skip for direct chat)
            if not is_direct_chat:
                await self._queue_next_messages(current_message, message_queue, room_id)

        logger.info("RoomMessageCenter: Finished processing message queue")
        return True

    async def _save_queue_continuation(
        self,
        message_id: str,
        message_queue: deque,
        room_id: str,
        user_message_id: str,
        request_user_id: str | None,
        current_agent: Agent,
    ) -> None:
        """
        Save queue continuation state for a push notification task.

        This allows the queue to be resumed when the task completes via webhook.

        Args:
            message_id: The message ID (used in webhook URLs for task tracking)
        """
        # Serialize the remaining messages in the queue
        # Each message carries its own step_number and total_steps from decomposition
        serialized_queue = [msg.model_dump(mode="json") for msg in message_queue]

        # Note: room_memory_content is NOT saved here - it will be fetched fresh
        # from the database when the queue resumes (single source of truth)
        continuation_data = {
            "remaining_queue": serialized_queue,
            "room_id": room_id,
            "user_message_id": user_message_id,
            "request_user_id": request_user_id,
            "current_agent_id": current_agent.agent_id,
            "current_agent_name": current_agent.agent_card.name,
        }

        success = await self.database_service.save_continuation_on_message(
            message_id, continuation_data
        )

        if not success:
            logger.error(
                "RoomMessageCenter: Failed to save continuation for message %s",
                message_id,
            )

    async def resume_queue_from_continuation(
        self,
        message_id: str,
        task_result_text: str | None = None,
    ) -> bool:
        """
        Resume queue processing after a push notification task completes.

        This is called from the webhook handler when a task reaches a terminal state.

        Args:
            message_id: The message ID (used in webhook URLs for task tracking)
            task_result_text: The result text from the completed task (for context)

        Returns:
            True if queue was resumed successfully
        """
        continuation = (
            await self.database_service.get_and_clear_continuation_on_message(
                message_id
            )
        )

        if not continuation:
            logger.debug(
                "RoomMessageCenter: No continuation found for message %s",
                message_id,
            )
            return False

        logger.info(
            "RoomMessageCenter: Resuming queue for message %s with %d remaining messages",
            message_id,
            len(continuation.get("remaining_queue", [])),
        )

        # Deserialize the queue (room_memory_content is fetched fresh from DB
        # in _process_single_agent_message - single source of truth)
        remaining_queue = deque()
        for msg_data in continuation.get("remaining_queue", []):
            remaining_queue.append(RoomAgentMessage.model_validate(msg_data))

        room_id = continuation.get("room_id")
        user_message_id = continuation.get("user_message_id")
        request_user_id = continuation.get("request_user_id")

        if not room_id or not user_message_id:
            logger.error(
                "RoomMessageCenter: Invalid continuation data for message %s",
                message_id,
            )
            return False

        # Add the completed task's result to memory if available
        if task_result_text:
            current_agent_id = continuation.get("current_agent_id")
            current_agent_name = continuation.get("current_agent_name", "Agent")
            await self.room_memory_service.add_agent_response_to_memory(
                room_id=room_id,
                agent_id=current_agent_id,
                agent_name=current_agent_name,
                response_text=task_result_text,
            )

        # Resume processing the remaining queue
        if len(remaining_queue) > 0:
            success = await self._process_agent_message_queue(
                remaining_queue,
                room_id,
                user_message_id,
                request_user_id,
            )

            if success:
                # Let the local room coordinator perform any post-processing logic
                # such as generating debate summaries. Coordination failures should
                # not break the main message processing flow.
                await self.room_coordinator_service.on_room_user_message_completed(
                    room_id, user_message_id
                )

                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.COMPLETED, user_message_id
                )

                # Update room memory with new content (fetch fresh from DB for logging)
                room_memory = await self.database_service.get_room_memory_by_room_id(
                    room_id
                )
                await self._update_room_memory_after_processing(room_id, room_memory)
            else:
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.ERROR, user_message_id
                )

            return success

        return True

    async def _assign_agent(self, current_message: RoomAgentMessage) -> Agent | None:
        """Assign an agent to the message by inferring from content, scoped to allowed IDs when provided.

        Only active agents will be assigned. If no active agents are found, returns None.
        """
        # Gather any scoped agent list from extend_info (mentions/room) and merge with group agents for future group mentions
        allowed_agent_ids: list[str] = []
        target_group = None
        if isinstance(current_message.extend_info, dict):
            allowed_agent_ids = (
                current_message.extend_info.get("allowed_agent_ids") or []
            )
            target_group = current_message.extend_info.get("target_group")

        # Normalize target_group into a list (support multiple groups)
        target_groups: list[str] = []
        if isinstance(target_group, list | tuple):
            target_groups = [str(g) for g in target_group]
        elif isinstance(target_group, str) and target_group:
            target_groups = [target_group]

        # If target groups exist, merge their agents into the allowed list
        merged_ids = set(str(aid) for aid in allowed_agent_ids)
        for tg in target_groups:
            if tg in ["all_agents", "room_team"]:
                continue
            try:
                group = await self.database_service.get_agent_group_by_id(tg)
                if group and group.agents:
                    merged_ids |= set(str(aid) for aid in group.agents)
                    logger.info(
                        "RoomMessageCenter: Merged %d agents from group %s (total allowed=%d) for message %s",
                        len(group.agents),
                        tg,
                        len(merged_ids),
                        current_message.message_id,
                    )
            except Exception as e:
                logger.error(
                    "RoomMessageCenter: Failed to load agents for group %s: %s",
                    tg,
                    e,
                )

        allowed_agent_ids = list(merged_ids)

        # Safely extract text content from message for logging and agent inference
        content = ""
        user_input = ""
        try:
            if (
                current_message.message_content
                and current_message.message_content.message_task
                and current_message.message_content.message_task.history
                and len(current_message.message_content.message_task.history) > 0
                and current_message.message_content.message_task.history[0].parts
                and len(current_message.message_content.message_task.history[0].parts)
                > 0
            ):
                parts = current_message.message_content.message_task.history[0].parts
                content = "".join(
                    part.root.text if part.root and hasattr(part.root, "text") else ""
                    for part in parts
                )
                first_part = parts[0]
                if first_part.root and hasattr(first_part.root, "text"):
                    user_input = first_part.root.text or ""
        except (IndexError, AttributeError) as e:
            logger.warning(
                "RoomMessageCenter: Failed to extract content from message %s: %s",
                current_message.message_id,
                e,
            )

        if not user_input:
            logger.error(
                "RoomMessageCenter: No user input found in message %s, cannot infer agent",
                current_message.message_id,
            )
            return None

        logger.info(
            "RoomMessageCenter: Inferring agent for message %s from content (length: %d chars) scoped_ids=%d target_group=%s",
            current_message.message_id,
            len(content),
            len(allowed_agent_ids),
            target_group,
        )

        # Query similar agents - this already filters for active agents by default
        matched_agents = await self.database_service.query_similar_agents(
            user_input,
            allowed_agent_ids=allowed_agent_ids if allowed_agent_ids else None,
            active_only=True,  # Only get active agents
        )

        if len(matched_agents) == 0:
            logger.error(
                "RoomMessageCenter: No active agent found for message %s (allowed_ids=%d)",
                current_message.message_id,
                len(allowed_agent_ids),
            )
            return None

        # Find the first active agent (double-check status as safety)
        agent = None
        for candidate in matched_agents:
            if candidate.agent_status == AgentStatus.active:
                agent = candidate
                break
            else:
                logger.warning(
                    "RoomMessageCenter: Skipping inactive agent %s (status=%s) for message %s",
                    candidate.agent_id,
                    candidate.agent_status,
                    current_message.message_id,
                )

        if agent is None:
            logger.error(
                "RoomMessageCenter: No active agent available for message %s after filtering",
                current_message.message_id,
            )
            return None

        current_message.agent_id = agent.agent_id

        update_success = (
            await self.database_service.update_room_agent_message_by_message_id(
                message_id=current_message.message_id,
                room_agent_message=current_message,
            )
        )

        if not update_success:
            logger.error(
                "RoomMessageCenter: Failed to update agent assignment for message %s",
                current_message.message_id,
            )
            return None

        logger.info(
            "RoomMessageCenter: Successfully assigned active agent %s to message %s",
            agent.agent_id,
            current_message.message_id,
        )
        return agent

    async def _process_single_agent_message(
        self,
        current_message: RoomAgentMessage,
        room_id: str,
        agent: Agent,
        user_message_id: str,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> ProcessingResult:
        """
        Process a single agent message with streaming support.

        Args:
            current_message: The agent message to process
            room_id: The room ID
            agent: The agent to process the message
            user_message_id: User message ID for cancellation checks
            step_number: Current step number in the workflow (1-indexed)
            total_steps: Total number of steps in the workflow

        Returns:
            ProcessingResult with:
                - status: SUCCESS, FAILED, CANCELLED, or PAUSED
                - response_text: The agent's response text (for storing in history)
                - message_id: Set when status is PAUSED (push notification task)
        """
        # Fetch room_memory_content fresh from database to ensure we have
        # the latest conversation history (single source of truth)
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        room_memory_content = (
            room_memory.memory_content if room_memory else MemoryContent()
        )

        # Prepare the agent message with context (ChatGPT/Claude style)
        process_response = await self.room_services.process_agent_message(
            RoomCenterAgentMessageRequest(message=current_message),
            room_memory_content,
        )

        if not process_response.success:
            return ProcessingResult(ProcessingStatus.FAILED)

        prepared_message = process_response.a2a_message
        if prepared_message is None:
            return ProcessingResult(ProcessingStatus.FAILED)

        # Stream or sync send based on agent capabilities
        support_streaming = self.a2a_service.has_streaming_capability(
            agent_card=agent.agent_card
        )

        full_response_text = ""
        paused_message_id = None
        if support_streaming:
            try:
                (
                    status,
                    full_response_text,
                ) = await self._handle_streaming_response_for_room(
                    current_message,
                    agent.agent_card,
                    prepared_message,
                    room_id,
                    user_message_id,
                    send_sse=True,
                    step_number=step_number,
                    total_steps=total_steps,
                )
            except Exception as exc:
                logger.error(
                    "RoomMessageCenter: Unhandled exception in streaming for message %s: %s",
                    current_message.message_id,
                    exc,
                    exc_info=True,
                )
                # Mark the task as failed in DB so it doesn't stay in "working" forever
                error_text = f"Agent streaming failed: {exc}"
                if (
                    current_message.message_content
                    and current_message.message_content.message_task
                ):
                    current_message.message_content.message_task.status = TaskStatus(
                        state=TaskState.failed,
                        message=Message(
                            role=Role.agent,
                            parts=[TextPart(text=error_text)],
                        ),
                    )
                    current_message.task_updated_at = utcnow()
                    await self.room_services.update_agent_message_by_message_id(
                        RoomCenterAgentMessageRequest(
                            message_id=current_message.message_id,
                            message=current_message,
                        )
                    )
                # Notify frontend via SSE
                await self.notification_service.send_task_update(
                    room_id=room_id,
                    message_id=current_message.message_id,
                    status="failed",
                    agent_card=agent.agent_card,
                    agent_id=current_message.agent_id,
                    created_at=None,
                    error=error_text,
                    step_number=step_number,
                    total_steps=total_steps,
                )
                return ProcessingResult(ProcessingStatus.FAILED, "")
            if status != ProcessingStatus.SUCCESS:
                return ProcessingResult(status, full_response_text)
        else:
            (
                success,
                full_response_text,
                paused_message_id,
            ) = await self._handle_sync_response_for_room(
                current_message,
                agent.agent_card,
                prepared_message,
                room_id,
                current_message.user_id,
                step_number=step_number,
                total_steps=total_steps,
            )
            if not success:
                return ProcessingResult(ProcessingStatus.FAILED)

        # Check if this is a push notification task that requires queue pausing
        if full_response_text is None and paused_message_id:
            logger.info(
                "RoomMessageCenter: Push notification task submitted for message %s; "
                "queue will be paused until task completes",
                paused_message_id,
            )
            return ProcessingResult(
                ProcessingStatus.PAUSED,
                response_text="",
                message_id=paused_message_id,
            )

        if full_response_text is None:
            logger.info(
                "RoomMessageCenter: Async task submitted for message %s; "
                "skipping immediate agent response",
                current_message.message_id,
            )
            return ProcessingResult(ProcessingStatus.SUCCESS)

        # Get updated message from database
        current_message = (
            await self.database_service.get_room_agent_message_by_message_id(
                current_message.message_id
            )
        )

        if current_message is None:
            return ProcessingResult(ProcessingStatus.FAILED, full_response_text)

        # Note: We no longer send agent_response here since task_update already
        # delivers the content with message_id for consistent frontend tracking.
        # The task_update event is sent at the end of streaming (line ~1954) or
        # sync response (line ~2852) with status="completed".

        return ProcessingResult(ProcessingStatus.SUCCESS, full_response_text)

    # ------------------------------------------------------------------
    # Sync-path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sync_fallback_response(
        raw_response,
        message_id: str,
    ) -> dict[str, Any]:
        """Parse a raw ``send_message_sync`` response into the dict format
        expected by ``_handle_sync_response_for_room``.

        This is only used when task tracking setup failed and we fell back to
        the plain (untracked) send path.  The returned dict mirrors the shape
        produced by ``A2AService.send_message_to_tracked_agent``.
        """
        if raw_response is None:
            return {"type": "message", "message_id": message_id, "content": ""}

        # raw_response is a SendMessageResponse
        if isinstance(raw_response.root, JSONRPCErrorResponse):
            from services.a2a_service import A2AServiceError
            raise A2AServiceError(str(raw_response.root.error.message))

        result = raw_response.root.result

        if result.kind == "message":
            # Extract text from message parts
            texts = []
            for part in result.parts or []:
                if hasattr(part, "text") and part.text:
                    texts.append(part.text)
                elif hasattr(part, "root") and hasattr(part.root, "text"):
                    texts.append(part.root.text)
            return {
                "type": "message",
                "message_id": message_id,
                "content": "".join(texts),
            }

        if result.kind == "task":
            state = result.status.state
            state_value = state.value if hasattr(state, "value") else str(state)
            return {
                "type": "task",
                "message_id": message_id,
                "task_id": result.id,
                "status": state_value,
            }

        return {"type": "message", "message_id": message_id, "content": ""}

    async def _handle_sync_response_for_room(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        prepared_message: Message,
        room_id: str,
        _user_id: str | None,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """Handle synchronous (non-streaming) response from an agent.

        All agents now use task tracking for consistent UX (task status bubbles)
        and SSE reconnection support.

        Returns:
            Tuple of (success, response_text, message_id):
                - success: Whether the operation succeeded
                - response_text: The response text (None for async tasks)
                - message_id: The message ID if this is a push notification task
                  that requires queue pausing (None otherwise). This is used
                  in webhook URLs for task tracking.
        """
        # Step 1: Create task record and emit task_submitted SSE
        task_info = await self._setup_task_tracking(
            current_message,
            agent_card,
            prepared_message,
            room_id,
            step_number=step_number,
            total_steps=total_steps,
        )
        if not task_info:
            logger.warning(
                "RoomMessageCenter: task tracking setup failed for message %s "
                "(agent: %s) — continuing without tracking (degraded mode)",
                current_message.message_id,
                agent_card.name,
            )

        created_at = task_info.get("created_at") if task_info else None
        message_id = current_message.message_id

        # Step 3: Call the agent (this blocks until response)
        try:
            if task_info:
                response = await self.a2a_service.send_message_to_tracked_agent(
                    agent_card=agent_card,
                    message=prepared_message,
                    message_id=message_id,
                    webhook_token=task_info["webhook_token"],
                    context_id=task_info["context_id"],
                )
            else:
                # Fallback: send without tracking (no push notifications / DB updates)
                raw_response = await self.a2a_service.send_message_sync(
                    agent_card=agent_card,
                    message=prepared_message,
                )
                response = self._parse_sync_fallback_response(raw_response, message_id)
        except Exception as exc:
            logger.error("Agent error: %s", exc, exc_info=True)
            # Send task_update with failed status
            if task_info:
                await self.notification_service.send_task_update(
                    room_id=room_id,
                    message_id=message_id,
                    status="failed",
                    agent_card=agent_card,
                    agent_id=current_message.agent_id,
                    created_at=created_at,
                    error=str(exc),
                    step_number=step_number,
                    total_steps=total_steps,
                )
            await self.sse_manager.send_error(room_id, str(exc))
            return False, "", None

        # Note: Task data is already updated in the database by send_message_to_tracked_agent
        # via update_task_on_message. We don't need to call _handle_a2a_response_for_room here
        # as it could potentially overwrite the updated task with stale data from current_message.

        # Step 5: Handle "message" response (fast path - agent returned immediately)
        if response.get("type") == "message":
            full_response_text = response.get("content") or ""

            # Update task status to completed in database
            # This ensures the status is persisted for page reloads (SSE only notifies live clients)
            if (
                current_message.message_content
                and current_message.message_content.message_task
            ):
                current_message.message_content.message_task.status = TaskStatus(
                    state=TaskState.completed
                )
                # Also update message_text with the response content for display
                if full_response_text:
                    current_message.message_content.message_text = full_response_text
                # Update task_updated_at for staleness detection on page reload
                current_message.task_updated_at = utcnow()

                update_response = (
                    await self.room_services.update_agent_message_by_message_id(
                        RoomCenterAgentMessageRequest(
                            message_id=current_message.message_id,
                            message=current_message,
                        )
                    )
                )
                if not update_response.success:
                    logger.error(
                        "RoomMessageCenter: Failed to update sync message status to completed: %s",
                        update_response.error,
                    )

            # Send task_update with completed status
            if task_info:
                await self.notification_service.send_task_update(
                    room_id=room_id,
                    message_id=message_id,
                    status="completed",
                    agent_card=agent_card,
                    agent_id=current_message.agent_id,
                    created_at=created_at,
                    content=full_response_text,
                    step_number=step_number,
                    total_steps=total_steps,
                )

            return True, full_response_text, None

        # Step 6: Handle "task" response (async path - agent is still working)
        if response.get("type") == "task":
            status = response.get("status") or "working"

            # Send task_update with current status (may be working, input_required, etc.)
            if task_info:
                await self.notification_service.send_task_update(
                    room_id=room_id,
                    message_id=message_id,
                    status=status,
                    agent_card=agent_card,
                    agent_id=current_message.agent_id,
                    created_at=created_at,
                    requires_input=response.get("requires_input", False),
                    requires_auth=response.get("requires_auth", False),
                    status_message=response.get("message"),
                    step_number=step_number,
                    total_steps=total_steps,
                )

            # Only pause queue for push notification agents
            # Non-push agents complete synchronously, no need to pause
            if self.a2a_service.has_push_notification_capability(agent_card):
                return True, None, message_id
            else:
                # Non-push agent returned a task response - poll for completion
                # These agents don't support webhooks, so we need to poll
                agent_task_id = response.get("task_id")
                if not agent_task_id:
                    logger.warning(
                        "RoomMessageCenter: Non-push agent returned task response "
                        "without task_id, cannot poll for completion"
                    )
                    return True, None, None

                logger.info(
                    "RoomMessageCenter: Non-push agent returned task response, "
                    "polling for completion (task_id: %s)",
                    agent_task_id,
                )

                # Poll until task completes (with 2 minute timeout)
                completed_task = await self._poll_task_until_complete(
                    agent_card=agent_card,
                    task_id=agent_task_id,
                    message_id=message_id,
                    timeout_seconds=120,
                )

                if completed_task:
                    state = completed_task.status.state
                    state_value = state.value if hasattr(state, "value") else str(state)

                    # Update database with final task state
                    if task_info:
                        await self.database_service.update_task_on_message(
                            message_id, completed_task.model_dump(mode="json")
                        )

                    # Extract content or error based on final state
                    final_content = None
                    final_error = None

                    if state == TaskState.completed and completed_task.artifacts:
                        final_content = extract_text_from_artifacts(
                            completed_task.artifacts
                        )
                    elif is_failure_state(state):
                        final_error = extract_error_message(completed_task)
                        if not final_error:
                            final_error = f"Task {state_value}"

                    # Send final task_update SSE
                    if task_info:
                        await self.notification_service.send_task_update(
                            room_id=room_id,
                            message_id=message_id,
                            status=state_value,
                            agent_card=agent_card,
                            agent_id=current_message.agent_id,
                            created_at=created_at,
                            content=final_content,
                            error=final_error,
                            step_number=step_number,
                            total_steps=total_steps,
                        )

                    # Return the content for storing in conversation history
                    return True, final_content, None
                else:
                    # Polling timed out - task will be handled by stale task checker
                    logger.warning(
                        "RoomMessageCenter: Polling timed out for task %s, "
                        "will be handled by stale task checker",
                        message_id,
                    )
                    return True, None, None

        logger.error("Unexpected response type from task tracking: %s", response)
        return False, "", None

    async def _queue_next_messages(
        self, current_message: RoomAgentMessage, message_queue: deque, room_id: str
    ) -> None:
        """Queue up next messages in the chain after processing current message.

        Args:
            current_message: The message that was just processed
            message_queue: The queue to add next messages to
            room_id: The room ID to check for debate mode
        """
        logger.info(
            "RoomMessageCenter: Looking for next messages related to %s (step %s/%s)",
            current_message.message_id,
            current_message.step_number,
            current_message.total_steps,
        )
        next_messages = (
            await self.database_service.get_room_agent_messages_by_related_message_id(
                current_message.message_id
            )
        )
        logger.info(
            "RoomMessageCenter: Found %d next messages for message %s",
            len(next_messages),
            current_message.message_id,
        )

        # Check if room is in debate mode
        is_debate_mode = False
        room = await self.database_service.get_room_by_room_id(room_id)
        if room and room.extend_info and isinstance(room.extend_info, dict):
            is_debate_mode = bool(room.extend_info.get("debateMode", False))

        for next_message in next_messages:
            logger.info(
                "RoomMessageCenter: Queueing next message %s (step %s/%s, task_content: %s)",
                next_message.message_id,
                next_message.step_number,
                next_message.total_steps,
                next_message.message_content.message_text[:50]
                if next_message.message_content
                and next_message.message_content.message_text
                else "None",
            )

            # Only inject debate prompt if room is in debate mode
            if is_debate_mode:
                new_agent_message = (
                    await self.debate_service.inject_short_debate_for_agent_message(
                        next_message
                    )
                )
                if new_agent_message is None:
                    logger.warning(
                        "RoomMessageCenter: inject_short_debate_for_agent_message returned None for message %s",
                        next_message.message_id,
                    )
                    continue
                message_queue.append(new_agent_message)
            else:
                # Non-debate mode: queue message directly without debate prompt injection
                message_queue.append(next_message)

    async def _update_room_memory_after_processing(
        self,
        room_id: str,
        room_memory: RoomMemory,
    ) -> None:
        """
        Post-processing hook after all agent messages are processed.

        Note: With the new ChatGPT/Claude-style context management, agent responses
        are stored incrementally in conversation history via add_agent_response_to_memory()
        during _process_agent_message_queue(). This method is kept for:
        1. Logging/debugging
        2. Future enhancements (e.g., periodic LLM summarization of long conversations)
        """

        # Get current context stats for logging
        if room_memory and room_memory.memory_content:
            stats = get_context_stats(room_memory.memory_content)
            logger.info(
                "RoomMessageCenter: Room %s memory updated - %d turns in history, "
                "summary=%s, total_chars=%d",
                room_id,
                stats.get("history_turns", 0),
                "yes" if stats.get("has_summary") else "no",
                stats.get("total_chars", 0),
            )
        else:
            logger.info(
                "RoomMessageCenter: Room %s - no memory content to update",
                room_id,
            )


# Module-level singleton — all callers should import this instance
# rather than creating their own RoomMessageCenter().
room_message_center = RoomMessageCenter()
