from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum

from a2a.types import (
    TaskState,
)

from common.utils.cancellation import CancellationToken
from common.utils.context_utils import get_context_stats
from common.utils.logger import get_logger
from models.agent import Agent, AgentStatus
from models.memory import MemoryContent
from models.request import OrchestrationRequest, RoomCenterAgentMessageRequest
from models.response import OrchestrationResponse
from models.room import RoomAgentMessage
from modules.ResponseProcessor import ProcessingStatus, ResponseProcessor
from modules.TaskStateManager import (
    TaskStateManager,
    get_task,
)
from services.a2a_constants import SSEProcessingStatus
from services.a2a_service import a2a_service
from services.agent_resolver_service import agent_resolver_service
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


class QueueResult(str, Enum):
    """Result of processing the agent message queue."""

    COMPLETED = "completed"  # All agents finished
    FAILED = "failed"  # An agent failed
    PAUSED = "paused"  # Queue paused; webhook will resume
    CANCELED = "canceled"  # User canceled or rate-limited


@dataclass
class ProcessingResult:
    """Result of message processing with optional metadata."""

    status: ProcessingStatus
    response_text: str = ""
    # message_id is set when status is PAUSED (for push notification tasks)
    # This is used in webhook URLs for task tracking
    message_id: str | None = None


@dataclass
class AssignResult:
    """Result of ``_assign_agent``.

    Replaces the old ``self._last_resolve_failure`` pattern which stored the
    failure reason on the singleton instance — a concurrency hazard when
    multiple asyncio tasks process different rooms simultaneously (Issue 16).
    """

    agent: Agent | None
    failure_reason: str | None = None


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
        self.agent_resolver = agent_resolver_service
        self.tsm = TaskStateManager(room_services, notification_service)
        self.response_processor = ResponseProcessor(
            tsm=self.tsm,
            sse_manager=self.sse_manager,
            a2a_service=self.a2a_service,
            task_service=self.task_service,
            database_service=self.database_service,
        )

    # ------------------------------------------------------------------
    # Shared helpers (reduce repeated boilerplate)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _managed_queue(self, message_queue: deque):
        """Context manager that cancels remaining items when the queue is
        abandoned (failure, cancellation, or unhandled exception).

        Usage::

            async with self._managed_queue(queue) as q:
                while len(q) > 0:
                    current = q.popleft()
                    ...
                    if something_failed:
                        return QueueResult.FAILED  # cleanup runs automatically

        On normal completion (queue drained) the ``finally`` block is a no-op
        because nothing remains.  On early exit the ``finally`` block cancels
        every message still in the deque via ``_cancel_remaining_queue``.

        **Important:** Messages that have already been ``popleft``-ed from the
        deque are *not* visible to this cleanup.  The caller must transition
        the *current* message itself before returning (e.g., via
        ``_fail_task_and_notify`` or ``_transition_task``).
        """
        try:
            yield message_queue
        finally:
            if len(message_queue) > 0:
                await self.tsm.cancel_remaining_queue(message_queue)

    # ------------------------------------------------------------------

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

        # Get user_id from the user message for rate limiting.
        # Fall back to the request-level user_id (from auth) if the stored
        # message is missing or has no user_id.
        user_message = await self.database_service.get_room_user_message_by_message_id(
            room_user_message_id
        )
        user_id = (
            (user_message.user_id if user_message else None) or request.user_id
        )

        # Extract quoted context from user message extend_info (set when user quotes text)
        quoted_text: str | None = None
        if user_message and isinstance(user_message.extend_info, dict):
            quoted_text = user_message.extend_info.get("quoted_text") or None

        # Create a CancellationToken for this message pipeline (A-3).
        # The token is pre-signalled if cancel_message() was called before
        # processing started — no race window.
        # If a token was already created (e.g. by send_message_to_room for
        # the parsing phase), reuse it so the entire pipeline shares one token.
        token = self.sse_manager.get_token(room_user_message_id)
        if token is None:
            token = self.sse_manager.create_token(room_user_message_id)

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
        if token.is_cancelled:
            logger.info(
                "RoomMessageCenter: Processing cancelled for message %s, stopping all processing",
                room_user_message_id,
            )
            # Persist canceled status for all queued steps
            await self.tsm.cancel_remaining_queue(message_queue)
            await self.sse_manager.send_processing_status(
                room_id, SSEProcessingStatus.CANCELED, room_user_message_id
            )
            self.sse_manager.clear_cancellation(room_user_message_id)
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        queue_result = await self._process_agent_message_queue(
            message_queue,
            room_id,
            room_user_message_id,
            token=token,
            request_user_id=user_id,
            quoted_text=quoted_text,
        )

        if queue_result == QueueResult.FAILED:
            await self.sse_manager.send_processing_status(
                room_id,
                SSEProcessingStatus.FAILED,
                room_user_message_id,
                details="Failed to process agent messages",
            )
            return OrchestrationResponse(
                success=False,
                error="Failed to process agent messages",
                status_code=500,
            )

        if queue_result == QueueResult.PAUSED:
            # Queue paused for push notification — do NOT trigger summary or
            # COMPLETED yet. The webhook handler will resume and trigger
            # summary when the agent finishes.
            return OrchestrationResponse(
                room_id=room_id, success=True, error=None, status_code=200
            )

        if queue_result == QueueResult.CANCELED:
            # CANCELED status was already sent to the frontend inside the queue
            # processor. Return early — do NOT send COMPLETED or trigger summary.
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        # QueueResult.COMPLETED — proceed with summary + completion.
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

        # Log room memory stats (debug/monitoring)
        await self._log_room_memory_stats(room_id)

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
        *,
        token: CancellationToken | None = None,
        request_user_id: str | None = None,
        quoted_text: str | None = None,
    ) -> QueueResult:
        """
        Process all messages in the queue sequentially.

        Args:
            message_queue: Queue of agent messages to process
            room_id: The room ID
            user_message_id: The user message ID for cancellation checks
            token: CancellationToken for cooperative cancellation (A-3)
            request_user_id: The ID of the user making the request (for rate limiting)
            quoted_text: Text the user highlighted and quoted from a previous message

        Returns:
            QueueResult indicating whether the queue completed, failed, was
            paused (waiting for a webhook), or was canceled.
        """
        logger.info(
            "RoomMessageCenter: Starting to process message queue with %d messages",
            len(message_queue),
        )
        async with self._managed_queue(message_queue):
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
                if token and token.is_cancelled:
                    logger.info(
                        "RoomMessageCenter: Message processing cancelled for %s, stopping all processing",
                        user_message_id,
                    )
                    # current_message was already popped — cancel it directly;
                    # the context manager's finally block handles the rest.
                    await self.tsm.transition_task(
                        current_message, TaskState.canceled, persist=True, notify=False
                    )
                    await self.sse_manager.send_processing_status(
                        room_id, SSEProcessingStatus.CANCELED, user_message_id
                    )
                    self.sse_manager.clear_cancellation(user_message_id)
                    return QueueResult.CANCELED

                # Assign agent if not already assigned
                if current_message.agent_id is None:
                    assign_result = await self._assign_agent(current_message)
                    if assign_result.agent is None:
                        logger.error(
                            "RoomMessageCenter: Failed to assign agent for message %s",
                            current_message.message_id,
                        )
                        error_text = (
                            assign_result.failure_reason
                            or "No available agent could be found for your request."
                        )
                        # Try to identify the intended agent from allowed_agent_ids
                        intended_agent_id = None
                        if isinstance(current_message.extend_info, dict):
                            allowed = (
                                current_message.extend_info.get("allowed_agent_ids") or []
                            )
                            if len(allowed) == 1:
                                intended_agent_id = allowed[0]
                        # Persist the intended agent_id so the frontend can show the right name
                        if intended_agent_id:
                            current_message.agent_id = intended_agent_id
                        await self.tsm.fail_task_and_notify(
                            room_id=room_id,
                            message=current_message,
                            error_text=error_text,
                            agent_id=intended_agent_id,
                        )
                        return QueueResult.FAILED
                    agent = assign_result.agent
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
                        await self.tsm.fail_task_and_notify(
                            room_id=room_id,
                            message=current_message,
                            error_text="The assigned agent could not be found.",
                            agent_id=current_message.agent_id,
                        )
                        return QueueResult.FAILED

                    # Check if the assigned agent is still active
                    if agent.agent_status != AgentStatus.active:
                        logger.warning(
                            "RoomMessageCenter: Assigned agent %s is not active (status=%s), re-assigning for message %s",
                            current_message.agent_id,
                            agent.agent_status,
                            current_message.message_id,
                        )
                        # Save original agent_id before clearing for the error notification
                        original_agent_id = current_message.agent_id
                        # Clear the agent_id and re-assign
                        current_message.agent_id = None
                        reassign_result = await self._assign_agent(current_message)
                        if reassign_result.agent is None:
                            logger.error(
                                "RoomMessageCenter: Failed to re-assign agent for message %s after inactive agent",
                                current_message.message_id,
                            )
                            error_text = (
                                reassign_result.failure_reason
                                or "The assigned agent is no longer available and no alternative agent could be found."
                            )
                            # Restore original agent_id so the frontend can show the right name
                            current_message.agent_id = original_agent_id
                            await self.tsm.fail_task_and_notify(
                                room_id=room_id,
                                message=current_message,
                                error_text=error_text,
                                agent_id=original_agent_id,
                            )
                            return QueueResult.FAILED
                        agent = reassign_result.agent

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
                        # current_message was already popped — cancel it directly;
                        # the context manager's finally block handles the rest.
                        await self.tsm.transition_task(
                            current_message, TaskState.canceled, persist=True, notify=False
                        )
                        return QueueResult.CANCELED

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
                    token=token,
                    step_number=None if is_direct_chat else current_message.step_number,
                    total_steps=None if is_direct_chat else current_message.total_steps,
                    quoted_text=quoted_text,
                )

                if result.status == ProcessingStatus.FAILED:
                    return QueueResult.FAILED
                elif result.status == ProcessingStatus.CANCELED:
                    return QueueResult.CANCELED
                elif result.status == ProcessingStatus.PAUSED:
                    # Push notification task submitted - save continuation state
                    # First, queue up next messages so they're included in continuation
                    if not is_direct_chat:
                        await self._queue_next_messages(
                            current_message, message_queue, room_id
                        )

                    if result.message_id:
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
                    # Items have been serialized into the continuation — clear the
                    # deque so _managed_queue's finally block doesn't cancel them.
                    message_queue.clear()
                    return QueueResult.PAUSED  # Successfully paused, webhook will resume

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
        return QueueResult.COMPLETED

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
            # Create (or retrieve) a CancellationToken so the resumed queue
            # can detect cancellation via the token (A-3).  The original
            # token from the first run may have been evicted from the TTL
            # cache while the queue was paused, so always ensure one exists.
            token = self.sse_manager.get_token(user_message_id)
            if token is None:
                token = self.sse_manager.create_token(user_message_id)

            queue_result = await self._process_agent_message_queue(
                remaining_queue,
                room_id,
                user_message_id,
                token=token,
                request_user_id=request_user_id,
            )

            if queue_result == QueueResult.PAUSED:
                # Another agent paused mid-resumed-queue — webhook will resume again
                return True
            if queue_result == QueueResult.FAILED:
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.ERROR, user_message_id
                )
                return False
            if queue_result == QueueResult.CANCELED:
                # CANCELED status was already sent inside the queue processor.
                # Return early — do NOT send COMPLETED or trigger summary.
                return True
            # COMPLETED — fall through to summary

        # Trigger summary + completion (covers both empty-queue and
        # completed-queue cases).  An empty remaining_queue means the
        # paused agent was the last one — its result was already added
        # to memory above, so the summary can now include it.
        await self.room_coordinator_service.on_room_user_message_completed(
            room_id, user_message_id
        )

        await self.sse_manager.send_processing_status(
            room_id, SSEProcessingStatus.COMPLETED, user_message_id
        )

        await self._log_room_memory_stats(room_id)
        return True

    async def _resolve_allowed_agent_ids(
        self,
        current_message: RoomAgentMessage,
    ) -> list[str]:
        """Resolve allowed agent IDs from extend_info, merging group members."""
        if not isinstance(current_message.extend_info, dict):
            return []

        allowed_agent_ids = current_message.extend_info.get("allowed_agent_ids") or []
        target_group = current_message.extend_info.get("target_group")

        # Normalize target_group into a list
        target_groups: list[str] = []
        if isinstance(target_group, list | tuple):
            target_groups = [str(g) for g in target_group]
        elif isinstance(target_group, str) and target_group:
            target_groups = [target_group]

        merged_ids = set(str(aid) for aid in allowed_agent_ids)
        for tg in target_groups:
            if tg in ["all_agents", "room_team"]:
                continue
            try:
                group = await self.database_service.get_agent_group_by_id(tg)
                if group and group.agents:
                    merged_ids |= set(str(aid) for aid in group.agents)
            except Exception as e:
                logger.error(
                    "RoomMessageCenter: Failed to load agents for group %s: %s", tg, e
                )

        return list(merged_ids)

    @staticmethod
    def _extract_user_input(current_message: RoomAgentMessage) -> str:
        """Extract the user's text input from the message's first history entry."""
        try:
            task = get_task(current_message)
            if (
                task
                and task.history
                and len(task.history) > 0
                and task.history[0].parts
                and len(task.history[0].parts) > 0
            ):
                first_part = task.history[0].parts[0]
                if first_part.root and hasattr(first_part.root, "text"):
                    return first_part.root.text or ""
        except (IndexError, AttributeError) as e:
            logger.warning(
                "RoomMessageCenter: Failed to extract content from message %s: %s",
                current_message.message_id,
                e,
            )
        return ""

    async def _assign_agent(self, current_message: RoomAgentMessage) -> AssignResult:
        """Assign an agent to the message by inferring from content.

        Uses the AgentResolverService to find the best accessible agent from
        the allowed agent list.  Returns an ``AssignResult`` with the chosen
        agent or a human-readable ``failure_reason`` when no agent is available.
        """
        allowed_agent_ids = await self._resolve_allowed_agent_ids(current_message)
        user_input = self._extract_user_input(current_message)

        if not user_input:
            logger.error(
                "RoomMessageCenter: No user input in message %s, cannot infer agent",
                current_message.message_id,
            )
            return AssignResult(
                agent=None,
                failure_reason="Unable to determine what to ask an agent — the message appears to be empty.",
            )

        logger.info(
            "RoomMessageCenter: Inferring agent for message %s (input length: %d, scoped_ids=%d)",
            current_message.message_id,
            len(user_input),
            len(allowed_agent_ids),
        )

        result = await self.agent_resolver.resolve(
            user_input,
            allowed_agent_ids=allowed_agent_ids if allowed_agent_ids else None,
            user_id=current_message.user_id,
        )

        if result.agent is None:
            logger.error(
                "RoomMessageCenter: No accessible agent for message %s: %s",
                current_message.message_id,
                result.failure_reason,
            )
            return AssignResult(agent=None, failure_reason=result.failure_reason)

        agent = result.agent
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
            return AssignResult(
                agent=None,
                failure_reason="Internal error: failed to persist agent assignment.",
            )

        logger.info(
            "RoomMessageCenter: Assigned agent %s to message %s",
            agent.agent_id,
            current_message.message_id,
        )
        return AssignResult(agent=agent)

    async def _process_single_agent_message(
        self,
        current_message: RoomAgentMessage,
        room_id: str,
        agent: Agent,
        user_message_id: str,
        *,
        token: CancellationToken | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        quoted_text: str | None = None,
    ) -> ProcessingResult:
        """
        Process a single agent message with streaming support.

        Delegates the actual agent communication (streaming/sync) to the
        ResponseProcessor, keeping orchestration logic here.
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
            quoted_text=quoted_text,
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

        rp = self.response_processor
        full_response_text = ""
        paused_message_id = None
        if support_streaming:
            try:
                (
                    status,
                    full_response_text,
                ) = await rp.handle_streaming_response(
                    current_message,
                    agent.agent_card,
                    prepared_message,
                    room_id,
                    user_message_id,
                    token=token,
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
                await self.tsm.fail_task_and_notify(
                    room_id=room_id,
                    message=current_message,
                    error_text=f"Agent streaming failed: {exc}",
                    agent_id=current_message.agent_id,
                    agent_card=agent.agent_card,
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
            ) = await rp.handle_sync_response(
                current_message,
                agent.agent_card,
                prepared_message,
                room_id,
                current_message.user_id,
                user_message_id=user_message_id,
                token=token,
                step_number=step_number,
                total_steps=total_steps,
            )
            if not success:
                # Distinguish cancellation from failure
                task = get_task(current_message)
                was_canceled = (
                    (token and token.is_cancelled)
                    or (task and task.status and task.status.state == TaskState.canceled)
                )
                if was_canceled:
                    return ProcessingResult(ProcessingStatus.CANCELED)
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

        return ProcessingResult(ProcessingStatus.SUCCESS, full_response_text)

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

    async def _log_room_memory_stats(self, room_id: str) -> None:
        """Log room memory stats after processing (debug/monitoring only)."""
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        if room_memory and room_memory.memory_content:
            stats = get_context_stats(room_memory.memory_content)
            logger.info(
                "RoomMessageCenter: Room %s memory - %d turns, summary=%s, chars=%d",
                room_id,
                stats.get("history_turns", 0),
                "yes" if stats.get("has_summary") else "no",
                stats.get("total_chars", 0),
            )


# Module-level singleton
room_message_center = RoomMessageCenter()
