"""QueueExecutor — sequential agent-message queue processing.

Owns the queue loop, RAII cleanup (``_managed_queue``), continuation
save/resume for webhook-paused queues, per-item dispatch to the
``ResponseProcessor``, and queue chaining (``_queue_next_messages``).

Agent assignment is delegated to the injected ``AgentDispatcher``.
"""

from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from a2a.types import TaskState

from common.utils.cancellation import CancellationToken
from common.utils.logger import get_logger
from models.agent import Agent, AgentStatus
from models.memory import MemoryContent
from models.room import RoomAgentMessage
from modules.AgentDispatcher import AgentDispatcher
from modules.ResponseProcessor import ProcessingStatus, ResponseProcessor
from modules.TaskStateManager import TaskStateManager, get_task
from services.a2a_constants import SSEProcessingStatus

if TYPE_CHECKING:
    from services.a2a_service import A2AService
    from services.database_service import DatabaseService
    from services.debate_service import DebateService
    from services.memory_service import RoomMemoryService
    from services.rate_limit_service import RateLimitService
    from services.room_services import RoomServices
    from services.sse_services import SSEManager

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Public data types
# ------------------------------------------------------------------


class QueueResult(str, Enum):
    """Result of processing the agent message queue."""

    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELED = "canceled"


@dataclass
class ProcessingResult:
    """Result of message processing with optional metadata."""

    status: ProcessingStatus
    response_text: str = ""
    message_id: str | None = None


@dataclass
class ResumeResult:
    """Returned by ``QueueExecutor.resume_from_continuation``.

    The caller (``RoomMessageCenter``) uses ``room_id`` / ``user_message_id``
    to send the terminal SSE event and trigger the coordinator when
    ``needs_completion`` is ``True``.
    """

    success: bool
    needs_completion: bool = False
    room_id: str | None = None
    user_message_id: str | None = None


# ------------------------------------------------------------------
# QueueExecutor
# ------------------------------------------------------------------


class QueueExecutor:
    """Sequential agent-message queue processing, continuation, and RAII cleanup."""

    def __init__(
        self,
        *,
        tsm: TaskStateManager,
        sse_manager: SSEManager,
        response_processor: ResponseProcessor,
        a2a_service: A2AService,
        room_services: RoomServices,
        room_memory_service: RoomMemoryService,
        database_service: DatabaseService,
        debate_service: DebateService,
        rate_limit_service: RateLimitService,
        agent_dispatcher: AgentDispatcher,
    ) -> None:
        self.tsm = tsm
        self.sse_manager = sse_manager
        self.response_processor = response_processor
        self.a2a_service = a2a_service
        self.room_services = room_services
        self.room_memory_service = room_memory_service
        self.database_service = database_service
        self.debate_service = debate_service
        self.rate_limit_service = rate_limit_service
        self.agent_dispatcher = agent_dispatcher

    # ------------------------------------------------------------------
    # RAII queue cleanup (A-2)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _managed_queue(self, message_queue: deque):
        """Context manager that cancels remaining items when the queue is
        abandoned (failure, cancellation, or unhandled exception).

        On normal completion (queue drained) the ``finally`` block is a no-op
        because nothing remains.  On early exit the ``finally`` block cancels
        every message still in the deque via ``cancel_remaining_queue``.

        **Important:** Messages that have already been ``popleft``-ed from the
        deque are *not* visible to this cleanup.  The caller must transition
        the *current* message itself before returning.
        """
        try:
            yield message_queue
        finally:
            if len(message_queue) > 0:
                await self.tsm.cancel_remaining_queue(message_queue)

    # ------------------------------------------------------------------
    # Main queue loop
    # ------------------------------------------------------------------

    async def process_queue(
        self,
        message_queue: deque,
        room_id: str,
        user_message_id: str,
        *,
        token: CancellationToken | None = None,
        request_user_id: str | None = None,
        quoted_text: str | None = None,
    ) -> QueueResult:
        """Process all messages in the queue sequentially.

        Parameters
        ----------
        message_queue:
            Queue of ``RoomAgentMessage`` objects to process.
        room_id:
            The room this queue belongs to.
        user_message_id:
            The originating user message ID (for cancellation checks).
        token:
            ``CancellationToken`` for cooperative cancellation (A-3).
        request_user_id:
            The user ID making the request (for rate limiting).
        quoted_text:
            Text the user highlighted and quoted from a previous message.
        """
        logger.info(
            "QueueExecutor: Starting to process message queue with %d messages",
            len(message_queue),
        )
        async with self._managed_queue(message_queue):
            while len(message_queue) > 0:
                current_message = message_queue.popleft()
                logger.info(
                    "QueueExecutor: Processing message %s (step %s/%s), %d remaining",
                    current_message.message_id,
                    current_message.step_number,
                    current_message.total_steps,
                    len(message_queue),
                )

                # Check for cancellation before processing each agent message
                if token and token.is_cancelled:
                    logger.info(
                        "QueueExecutor: Message processing cancelled for %s",
                        user_message_id,
                    )
                    await self.tsm.transition_task(
                        current_message, TaskState.canceled, persist=True, notify=False
                    )
                    await self.sse_manager.send_processing_status(
                        room_id, SSEProcessingStatus.CANCELED, user_message_id
                    )
                    self.sse_manager.clear_cancellation(user_message_id)
                    return QueueResult.CANCELED

                # Assign agent if not already assigned
                agent = await self._resolve_agent_for_message(
                    current_message, room_id
                )
                if agent is None:
                    return QueueResult.FAILED

                # Check rate limits before processing (only if user_id available)
                if request_user_id:
                    rate_limited = await self._check_rate_limit(
                        current_message, agent, room_id, user_message_id, request_user_id
                    )
                    if rate_limited:
                        return QueueResult.CANCELED

                # Dispatch to single-message processing
                is_direct_chat = bool(
                    current_message.extend_info
                    and current_message.extend_info.get("is_direct_chat")
                )
                result = await self._process_single_message(
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
                    if not is_direct_chat:
                        await self._queue_next_messages(
                            current_message, message_queue, room_id
                        )
                    if result.message_id:
                        await self._save_continuation(
                            message_id=result.message_id,
                            message_queue=message_queue,
                            room_id=room_id,
                            user_message_id=user_message_id,
                            request_user_id=request_user_id,
                            current_agent=agent,
                        )
                        logger.info(
                            "QueueExecutor: Queue paused for message %s with %d remaining",
                            result.message_id,
                            len(message_queue),
                        )
                    message_queue.clear()
                    return QueueResult.PAUSED

                # Record the request for rate limiting
                if request_user_id:
                    await self.rate_limit_service.record_request(
                        agent_id=agent.agent_id,
                        user_id=request_user_id,
                    )

                # Store agent response in conversation history
                if result.response_text:
                    await self.room_memory_service.add_agent_response_to_memory(
                        room_id=room_id,
                        agent_id=current_message.agent_id,
                        agent_name=agent.agent_card.name if agent else "Agent",
                        response_text=result.response_text,
                    )

                # Queue up next messages in the chain (skip for direct chat)
                if not is_direct_chat:
                    await self._queue_next_messages(
                        current_message, message_queue, room_id
                    )

        logger.info("QueueExecutor: Finished processing message queue")
        return QueueResult.COMPLETED

    # ------------------------------------------------------------------
    # Agent resolution / rate-limit helpers (delegated from queue loop)
    # ------------------------------------------------------------------

    async def _resolve_agent_for_message(
        self,
        current_message: RoomAgentMessage,
        room_id: str,
    ) -> Agent | None:
        """Resolve or verify the agent for *current_message*.

        If no ``agent_id`` is set, delegates to the ``AgentDispatcher``.
        If one is set, fetches from DB and verifies it is still active,
        re-assigning via the dispatcher if not.

        Returns the ``Agent`` on success or ``None`` after persisting a
        failure notification on the message.
        """
        if current_message.agent_id is None:
            agent, failure_reason = await self.agent_dispatcher.assign_agent_for_queue(
                current_message
            )
            if agent is None:
                error_text = (
                    failure_reason
                    or "No available agent could be found for your request."
                )
                intended_agent_id = None
                if isinstance(current_message.extend_info, dict):
                    allowed = current_message.extend_info.get("allowed_agent_ids") or []
                    if len(allowed) == 1:
                        intended_agent_id = allowed[0]
                if intended_agent_id:
                    current_message.agent_id = intended_agent_id
                await self.tsm.fail_task_and_notify(
                    room_id=room_id,
                    message=current_message,
                    error_text=error_text,
                    agent_id=intended_agent_id,
                )
                return None
            return agent

        # Agent already assigned — fetch and verify active
        agent = await self.database_service.get_agent_by_agent_id(
            current_message.agent_id
        )
        if agent is None:
            logger.error(
                "QueueExecutor: Assigned agent %s not found for message %s",
                current_message.agent_id,
                current_message.message_id,
            )
            await self.tsm.fail_task_and_notify(
                room_id=room_id,
                message=current_message,
                error_text="The assigned agent could not be found.",
                agent_id=current_message.agent_id,
            )
            return None

        if agent.agent_status != AgentStatus.active:
            logger.warning(
                "QueueExecutor: Agent %s inactive (status=%s), re-assigning for %s",
                current_message.agent_id,
                agent.agent_status,
                current_message.message_id,
            )
            original_agent_id = current_message.agent_id
            current_message.agent_id = None
            reassigned, failure_reason = await self.agent_dispatcher.assign_agent_for_queue(
                current_message
            )
            if reassigned is None:
                error_text = (
                    failure_reason
                    or "The assigned agent is no longer available and no alternative could be found."
                )
                current_message.agent_id = original_agent_id
                await self.tsm.fail_task_and_notify(
                    room_id=room_id,
                    message=current_message,
                    error_text=error_text,
                    agent_id=original_agent_id,
                )
                return None
            return reassigned

        return agent

    async def _check_rate_limit(
        self,
        current_message: RoomAgentMessage,
        agent: Agent,
        room_id: str,
        user_message_id: str,
        request_user_id: str,
    ) -> bool:
        """Check rate limits. Returns ``True`` if rate-limited (caller should cancel)."""
        rate_limit_result = await self.rate_limit_service.check_rate_limit(
            agent_id=agent.agent_id,
            user_id=request_user_id,
            rate_limit_per_user=agent.rate_limit_per_user_per_hour,
            rate_limit_system=agent.rate_limit_system_per_hour,
        )

        if not rate_limit_result.allowed:
            logger.warning(
                "QueueExecutor: Rate limit exceeded for agent %s, user %s: %s",
                agent.agent_id,
                request_user_id,
                rate_limit_result.reason,
            )
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
            await self.tsm.transition_task(
                current_message, TaskState.canceled, persist=True, notify=False
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Single-message dispatch
    # ------------------------------------------------------------------

    async def _process_single_message(
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
        """Process a single agent message with streaming support.

        Delegates the actual agent communication (streaming/sync) to the
        ``ResponseProcessor``, keeping orchestration logic here.
        """
        from models.request import RoomCenterAgentMessageRequest

        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        room_memory_content = (
            room_memory.memory_content if room_memory else MemoryContent()
        )

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
                    "QueueExecutor: Unhandled exception in streaming for message %s: %s",
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
                task = get_task(current_message)
                was_canceled = (
                    (token and token.is_cancelled)
                    or (task and task.status and task.status.state == TaskState.canceled)
                )
                if was_canceled:
                    return ProcessingResult(ProcessingStatus.CANCELED)
                return ProcessingResult(ProcessingStatus.FAILED)

        if full_response_text is None and paused_message_id:
            logger.info(
                "QueueExecutor: Push notification task submitted for message %s; "
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
                "QueueExecutor: Async task submitted for message %s; "
                "skipping immediate agent response",
                current_message.message_id,
            )
            return ProcessingResult(ProcessingStatus.SUCCESS)

        current_message = (
            await self.database_service.get_room_agent_message_by_message_id(
                current_message.message_id
            )
        )

        if current_message is None:
            return ProcessingResult(ProcessingStatus.FAILED, full_response_text)

        return ProcessingResult(ProcessingStatus.SUCCESS, full_response_text)

    # ------------------------------------------------------------------
    # Continuation save/resume (webhook-paused queues)
    # ------------------------------------------------------------------

    async def _save_continuation(
        self,
        message_id: str,
        message_queue: deque,
        room_id: str,
        user_message_id: str,
        request_user_id: str | None,
        current_agent: Agent,
    ) -> None:
        """Save queue continuation state for a push notification task.

        This allows the queue to be resumed when the task completes via webhook.
        """
        serialized_queue = [msg.model_dump(mode="json") for msg in message_queue]

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
                "QueueExecutor: Failed to save continuation for message %s",
                message_id,
            )

    async def resume_from_continuation(
        self,
        message_id: str,
        task_result_text: str | None = None,
    ) -> ResumeResult:
        """Resume queue processing after a push notification task completes.

        Called from the webhook handler when a task reaches a terminal state.

        Returns a ``ResumeResult`` indicating whether the caller should trigger
        post-completion logic (coordinator + COMPLETED SSE status).
        """
        continuation = (
            await self.database_service.get_and_clear_continuation_on_message(
                message_id
            )
        )

        if not continuation:
            logger.debug(
                "QueueExecutor: No continuation found for message %s",
                message_id,
            )
            return ResumeResult(success=False)

        logger.info(
            "QueueExecutor: Resuming queue for message %s with %d remaining messages",
            message_id,
            len(continuation.get("remaining_queue", [])),
        )

        remaining_queue = deque()
        for msg_data in continuation.get("remaining_queue", []):
            remaining_queue.append(RoomAgentMessage.model_validate(msg_data))

        room_id = continuation.get("room_id")
        user_message_id = continuation.get("user_message_id")
        request_user_id = continuation.get("request_user_id")

        if not room_id or not user_message_id:
            logger.error(
                "QueueExecutor: Invalid continuation data for message %s",
                message_id,
            )
            return ResumeResult(success=False)

        if task_result_text:
            current_agent_id = continuation.get("current_agent_id")
            current_agent_name = continuation.get("current_agent_name", "Agent")
            await self.room_memory_service.add_agent_response_to_memory(
                room_id=room_id,
                agent_id=current_agent_id,
                agent_name=current_agent_name,
                response_text=task_result_text,
            )

        if len(remaining_queue) > 0:
            token = self.sse_manager.get_token(user_message_id)
            if token is None:
                token = self.sse_manager.create_token(user_message_id)

            queue_result = await self.process_queue(
                remaining_queue,
                room_id,
                user_message_id,
                token=token,
                request_user_id=request_user_id,
            )

            if queue_result == QueueResult.PAUSED:
                return ResumeResult(success=True)
            if queue_result == QueueResult.FAILED:
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.ERROR, user_message_id
                )
                return ResumeResult(success=False)
            if queue_result == QueueResult.CANCELED:
                return ResumeResult(success=True)

        # COMPLETED (or empty remaining queue) — caller should trigger
        # coordinator + COMPLETED SSE status.
        return ResumeResult(
            success=True,
            needs_completion=True,
            room_id=room_id,
            user_message_id=user_message_id,
        )

    # ------------------------------------------------------------------
    # Queue chaining
    # ------------------------------------------------------------------

    async def _queue_next_messages(
        self, current_message: RoomAgentMessage, message_queue: deque, room_id: str
    ) -> None:
        """Queue up next messages in the chain after processing current message."""
        logger.info(
            "QueueExecutor: Looking for next messages related to %s (step %s/%s)",
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
            "QueueExecutor: Found %d next messages for message %s",
            len(next_messages),
            current_message.message_id,
        )

        is_debate_mode = False
        room = await self.database_service.get_room_by_room_id(room_id)
        if room and room.extend_info and isinstance(room.extend_info, dict):
            is_debate_mode = bool(room.extend_info.get("debateMode", False))

        for next_message in next_messages:
            logger.info(
                "QueueExecutor: Queueing next message %s (step %s/%s, task_content: %s)",
                next_message.message_id,
                next_message.step_number,
                next_message.total_steps,
                next_message.message_content.message_text[:50]
                if next_message.message_content
                and next_message.message_content.message_text
                else "None",
            )

            if is_debate_mode:
                new_agent_message = (
                    await self.debate_service.inject_short_debate_for_agent_message(
                        next_message
                    )
                )
                if new_agent_message is None:
                    logger.warning(
                        "QueueExecutor: inject_short_debate returned None for message %s",
                        next_message.message_id,
                    )
                    continue
                message_queue.append(new_agent_message)
            else:
                message_queue.append(next_message)
