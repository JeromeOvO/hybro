"""QueueExecutor — sequential agent-message queue processing.

Owns the queue loop, RAII cleanup (``_managed_queue``), continuation
save/resume for webhook-paused queues, per-item dispatch to the
``ResponseProcessor``, and queue chaining (``_queue_next_messages``).

Agent assignment is delegated to the injected ``AgentDispatcher``.

Supervisor review hook: After each successful step, if a SupervisorPlan
is active, the Supervisor reviews the result and may revise, retry, or
skip remaining steps.
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
from models.supervisor import ReviewAction, StepResult, SupervisorPlan, SupervisorStep
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
    from services.room_supervisor_service import RoomSupervisorService
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
class QueueProcessingResult:
    """Extended result of queue processing with Supervisor state."""

    result: QueueResult
    step_results: list[StepResult] | None = None
    supervisor_plan: SupervisorPlan | None = None


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
    step_results: list[StepResult] | None = None
    supervisor_plan: SupervisorPlan | None = None


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
        supervisor_service: RoomSupervisorService | None = None,
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
        self.supervisor_service = supervisor_service

    # ------------------------------------------------------------------
    # RAII queue cleanup (A-2)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _managed_queue(self, message_queue: deque, last_popped: list[str]):
        """Context manager that cancels remaining items when the queue is
        abandoned (failure, cancellation, or unhandled exception).

        On normal completion (queue drained) the ``finally`` block is a no-op
        because nothing remains.  On early exit (``break`` from the loop) the
        ``finally`` block cancels every message still in the deque and clears
        it, guaranteeing all DB writes happen *before* control returns to the
        caller's post-``async with`` Phase 2 (deferred SSE notification).

        **Important:** Messages that have already been ``popleft``-ed from the
        deque are *not* visible to this cleanup.  The caller must transition
        the *current* message itself before ``break``-ing.

        **Descendant cleanup:** After cancelling in-memory siblings, this also
        bulk-cancels any DB-only descendants (not yet loaded into the deque)
        that are downstream in the ``related_message_id`` chain.  The
        ``last_popped`` list must contain the ``message_id`` of the most
        recently popped message so we know where the chain was interrupted.

        **PAUSED caveat:** The PAUSED path must call ``message_queue.clear()``
        *before* ``break`` (after ``_save_continuation`` captures the queue
        state) to prevent this handler from canceling saved messages.
        """
        try:
            yield message_queue
        finally:
            if len(message_queue) > 0:
                for msg in message_queue:
                    await self.tsm.transition_task(
                        msg, TaskState.canceled, persist=True, notify=False
                    )
                # Collect IDs of canceled siblings for descendant cleanup
                canceled_ids = [msg.message_id for msg in message_queue]
                message_queue.clear()
                # Cancel DB-only descendants of each canceled sibling
                for mid in canceled_ids:
                    await self.database_service.cancel_descendants(mid)
            # Cancel descendants of the last popped message (the one that
            # was being processed when the queue was interrupted).
            if last_popped:
                await self.database_service.cancel_descendants(last_popped[0])

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
        supervisor_plan: SupervisorPlan | None = None,
        completed_step_results: list[StepResult] | None = None,
        step_retry_counts: dict[str, int] | None = None,
    ) -> QueueProcessingResult:
        """Process all messages in the queue sequentially.

        Uses a two-phase approach to guarantee persist-before-notify ordering:

        **Phase 1** (inside ``async with _managed_queue``): The queue loop
        processes messages.  On any early exit the loop ``break``-s instead of
        ``return``-ing, which triggers the ``_managed_queue`` ``finally`` block
        to persist all remaining siblings as ``canceled`` in the DB.

        **Phase 2** (after the ``async with`` block): The deferred SSE
        notification is sent.  Because ``_managed_queue`` has already run, all
        DB writes are guaranteed complete before the frontend is notified.

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
        supervisor_plan:
            Optional SupervisorPlan for review hook integration.
        completed_step_results:
            Optional list of already-completed step results (for resume).

        Returns
        -------
        QueueProcessingResult with result status, step_results, and supervisor_plan.
        """
        logger.info(
            "QueueExecutor: Starting to process message queue with %d messages",
            len(message_queue),
        )

        queue_result = QueueResult.COMPLETED
        # Deferred SSE: (status, should_clear_cancellation)
        # None means caller handles SSE (FAILED, COMPLETED) or no SSE needed (PAUSED)
        deferred_sse: tuple[SSEProcessingStatus, bool] | None = None

        # Track completed step results for Supervisor review and synthesis
        step_results: list[StepResult] = list(completed_step_results or [])

        # Track retry counts per step_id to enforce max_retries limit
        # Use provided counts (from continuation) or start fresh
        retry_counts: dict[str, int] = dict(step_retry_counts or {})

        # Tracks the message_id of the last message popped from the queue.
        # Used by _managed_queue to cancel DB-only descendants on early exit.
        # Empty list = nothing popped yet; on normal completion this is harmless
        # because cancel_descendants on a completed message finds no actionable
        # children.
        last_popped: list[str] = []

        async with self._managed_queue(message_queue, last_popped):
            while len(message_queue) > 0:
                current_message = message_queue.popleft()
                last_popped[:] = [current_message.message_id]
                logger.info(
                    "QueueExecutor: Processing message %s (step %s/%s), %d remaining",
                    current_message.message_id,
                    current_message.step_number,
                    current_message.total_steps,
                    len(message_queue),
                )

                # --- Cancel check ---
                if token and token.is_cancelled:
                    logger.info(
                        "QueueExecutor: Message processing cancelled for %s",
                        user_message_id,
                    )
                    await self.tsm.transition_task(
                        current_message, TaskState.canceled, persist=True, notify=False
                    )
                    queue_result = QueueResult.CANCELED
                    deferred_sse = (SSEProcessingStatus.CANCELED, True)
                    break  # → _managed_queue finally persists remaining siblings

                # --- Agent resolution ---
                agent = await self._resolve_agent_for_message(
                    current_message, room_id
                )
                if agent is None:
                    # _resolve_agent_for_message already persisted a failure
                    # notification for current_message.
                    queue_result = QueueResult.FAILED
                    break  # → _managed_queue finally persists remaining siblings;
                    # caller sends FAILED SSE after process_queue returns

                # --- Rate limit check ---
                if request_user_id:
                    rate_limited = await self._check_rate_limit(
                        current_message, agent, room_id, user_message_id, request_user_id
                    )
                    if rate_limited:
                        # _check_rate_limit sent send_rate_limit_error (contextual
                        # data) but NOT send_processing_status — that's deferred
                        # to Phase 2.
                        queue_result = QueueResult.CANCELED
                        deferred_sse = (SSEProcessingStatus.RATE_LIMITED, False)
                        break

                # --- Dispatch to single-message processing ---
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
                    queue_result = QueueResult.FAILED
                    break  # → _managed_queue finally persists remaining siblings;
                    # caller sends FAILED SSE after process_queue returns

                elif result.status == ProcessingStatus.CANCELED:
                    queue_result = QueueResult.CANCELED
                    deferred_sse = (SSEProcessingStatus.CANCELED, True)
                    break

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
                            supervisor_plan=supervisor_plan,
                            step_results=step_results,
                            step_retry_counts=retry_counts,
                        )
                        logger.info(
                            "QueueExecutor: Queue paused for message %s with %d remaining",
                            result.message_id,
                            len(message_queue),
                        )
                    # PAUSED caveat: clear BEFORE break so _managed_queue doesn't
                    # cancel messages that were saved for later resumption.
                    message_queue.clear()
                    last_popped.clear()
                    queue_result = QueueResult.PAUSED
                    break

                # --- Success: continue loop ---

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

                # --- Supervisor review hook ---
                review_action = await self._supervisor_review_step(
                    supervisor_plan=supervisor_plan,
                    current_message=current_message,
                    agent=agent,
                    response_text=result.response_text,
                    message_queue=message_queue,
                    step_results=step_results,
                    step_retry_counts=retry_counts,
                    room_id=room_id,
                    user_message_id=user_message_id,
                    user_id=request_user_id,
                    is_direct_chat=is_direct_chat,
                )

                if review_action == ReviewAction.SKIP:
                    logger.info(
                        "QueueExecutor: Supervisor review returned SKIP, draining queue"
                    )
                    message_queue.clear()
                    last_popped.clear()
                    break

                # Queue up next messages in the chain (skip for direct chat)
                if not is_direct_chat:
                    await self._queue_next_messages(
                        current_message, message_queue, room_id
                    )

            else:
                # While loop drained normally (NOT via break) — all messages
                # processed successfully.  Clear last_popped so _managed_queue's
                # finally block skips the (harmless but unnecessary)
                # cancel_descendants call.
                last_popped.clear()

        # Phase 2: _managed_queue finally has run — all DB writes done.
        # Now safe to send workflow-level SSE notification.
        if deferred_sse:
            sse_status, clear_cancel = deferred_sse
            await self.sse_manager.send_processing_status(
                room_id, sse_status, user_message_id
            )
            if clear_cancel:
                self.sse_manager.clear_cancellation(user_message_id)

        if queue_result == QueueResult.COMPLETED:
            logger.info("QueueExecutor: Finished processing message queue")

        return QueueProcessingResult(
            result=queue_result,
            step_results=step_results if step_results else None,
            supervisor_plan=supervisor_plan,
        )

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
        """Check rate limits. Returns ``True`` if rate-limited (caller should cancel).

        Sends the contextual ``send_rate_limit_error`` event (which needs
        agent-specific data only available here) but does **not** send the
        workflow-level ``send_processing_status(RATE_LIMITED)`` — that is
        deferred to ``process_queue`` Phase 2 so it fires *after*
        ``_managed_queue`` has persisted all remaining siblings.
        """
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
            # NOTE: Do NOT send send_processing_status here — process_queue
            # handles the workflow-level SSE in Phase 2 after all siblings
            # are persisted by _managed_queue.
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
        supervisor_plan: SupervisorPlan | None = None,
        step_results: list[StepResult] | None = None,
        step_retry_counts: dict[str, int] | None = None,
    ) -> None:
        """Save queue continuation state for a push notification task.

        This allows the queue to be resumed when the task completes via webhook.
        Includes Supervisor state so review continues after resume.
        """
        serialized_queue = [msg.model_dump(mode="json") for msg in message_queue]

        continuation_data: dict = {
            "remaining_queue": serialized_queue,
            "room_id": room_id,
            "user_message_id": user_message_id,
            "request_user_id": request_user_id,
            "current_agent_id": current_agent.agent_id,
            "current_agent_name": current_agent.agent_card.name,
        }

        # Include Supervisor state for review continuity after resume
        if supervisor_plan:
            continuation_data["supervisor_plan"] = supervisor_plan.model_dump(mode="json")
        if step_results:
            continuation_data["completed_step_results"] = [
                r.model_dump(mode="json") for r in step_results
            ]
        if step_retry_counts:
            continuation_data["step_retry_counts"] = step_retry_counts

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
        Restores Supervisor state so review continues after resume.

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

        # Restore Supervisor state
        supervisor_plan: SupervisorPlan | None = None
        completed_step_results: list[StepResult] | None = None
        step_retry_counts: dict[str, int] | None = None

        plan_data = continuation.get("supervisor_plan")
        if plan_data:
            try:
                supervisor_plan = SupervisorPlan.model_validate(plan_data)
            except Exception as e:
                logger.warning(
                    "QueueExecutor: Failed to restore supervisor_plan: %s", e
                )

        results_data = continuation.get("completed_step_results")
        if results_data:
            try:
                completed_step_results = [
                    StepResult.model_validate(r) for r in results_data
                ]
            except Exception as e:
                logger.warning(
                    "QueueExecutor: Failed to restore completed_step_results: %s", e
                )

        step_retry_counts = continuation.get("step_retry_counts")

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

            queue_processing_result = await self.process_queue(
                remaining_queue,
                room_id,
                user_message_id,
                token=token,
                request_user_id=request_user_id,
                supervisor_plan=supervisor_plan,
                completed_step_results=completed_step_results,
                step_retry_counts=step_retry_counts,
            )

            if queue_processing_result.result == QueueResult.PAUSED:
                return ResumeResult(success=True)
            if queue_processing_result.result == QueueResult.FAILED:
                await self.sse_manager.send_processing_status(
                    room_id, SSEProcessingStatus.ERROR, user_message_id
                )
                return ResumeResult(success=False)
            if queue_processing_result.result == QueueResult.CANCELED:
                return ResumeResult(success=True)

            # Pass step_results for synthesis
            return ResumeResult(
                success=True,
                needs_completion=True,
                room_id=room_id,
                user_message_id=user_message_id,
                step_results=queue_processing_result.step_results,
                supervisor_plan=queue_processing_result.supervisor_plan,
            )

        # COMPLETED (or empty remaining queue) — caller should trigger
        # coordinator + COMPLETED SSE status.
        return ResumeResult(
            success=True,
            needs_completion=True,
            room_id=room_id,
            user_message_id=user_message_id,
            step_results=completed_step_results,
            supervisor_plan=supervisor_plan,
        )

    # ------------------------------------------------------------------
    # Supervisor review hook
    # ------------------------------------------------------------------

    async def _supervisor_review_step(
        self,
        supervisor_plan: SupervisorPlan | None,
        current_message: RoomAgentMessage,
        agent: Agent,
        response_text: str,
        message_queue: deque,
        step_results: list[StepResult],
        step_retry_counts: dict[str, int],
        room_id: str,
        user_message_id: str,
        user_id: str | None,
        is_direct_chat: bool,
    ) -> ReviewAction:
        """Run Supervisor review after a successful step.

        Returns the review action (ReviewAction enum).
        For direct chat or when no supervisor is configured, returns CONTINUE.

        Side effects:
        - Appends a StepResult to step_results
        - May modify message_queue (for REVISE action)
        - May re-queue current step (for RETRY action)
        - Updates step_retry_counts when RETRY is triggered
        """
        # Skip review for direct chat or when supervisor is not configured
        if is_direct_chat or not supervisor_plan or not self.supervisor_service:
            return ReviewAction.CONTINUE

        # Find the SupervisorStep corresponding to this message
        current_step = self._find_step_for_message(supervisor_plan, current_message)
        if not current_step:
            logger.warning(
                "QueueExecutor: Could not find SupervisorStep for message %s",
                current_message.message_id,
            )
            return ReviewAction.CONTINUE

        # Record the step result
        step_results.append(
            StepResult(
                step_id=current_step.step_id,
                agent_id=current_step.agent_id or current_message.agent_id or "",
                agent_name=current_step.agent_name,
                task_description=current_step.task_description,
                response_text=response_text or "",
                success=True,
            )
        )

        # Determine remaining steps
        completed_step_ids = {r.step_id for r in step_results}
        remaining_steps = [
            s for s in supervisor_plan.steps if s.step_id not in completed_step_ids
        ]

        # Skip review if this is the last step (nothing to revise/skip)
        if not remaining_steps:
            logger.debug(
                "QueueExecutor: Last step completed, skipping Supervisor review"
            )
            return ReviewAction.CONTINUE

        # Check if review should be skipped for this step
        if not self.supervisor_service._should_review_step(
            supervisor_plan, current_step
        ):
            return ReviewAction.CONTINUE

        # Calculate retries left for this step
        retries_used = step_retry_counts.get(current_step.step_id, 0)
        retries_left = max(0, current_step.max_retries - retries_used)

        # Call Supervisor review
        try:
            review = await self.supervisor_service.review_step(
                plan=supervisor_plan,
                completed_step=current_step,
                agent_result=response_text or "",
                remaining_steps=remaining_steps,
                retries_left=retries_left,
            )
        except Exception as e:
            logger.error(
                "QueueExecutor: Supervisor review failed for step %s: %s",
                current_step.step_id,
                e,
            )
            return ReviewAction.CONTINUE

        logger.info(
            "QueueExecutor: Supervisor review for step %s: action=%s, reasoning=%s",
            current_step.step_id,
            review.action,
            review.reasoning[:100] if review.reasoning else "",
        )

        if review.action == ReviewAction.CONTINUE:
            return ReviewAction.CONTINUE

        elif review.action == ReviewAction.SKIP:
            return ReviewAction.SKIP

        elif review.action == ReviewAction.REVISE and review.revised_steps:
            await self._handle_revise_action(
                review.revised_steps,
                supervisor_plan,
                completed_step_ids,
                message_queue,
                room_id,
                user_message_id,
                user_id,
                current_message,
            )
            return ReviewAction.CONTINUE

        elif review.action == ReviewAction.RETRY:
            # Check if retries are exhausted
            if retries_left <= 0:
                logger.warning(
                    "QueueExecutor: Supervisor requested RETRY for step %s but "
                    "retries exhausted (max_retries=%d, used=%d). Continuing instead.",
                    current_step.step_id,
                    current_step.max_retries,
                    retries_used,
                )
                return ReviewAction.CONTINUE

            # Increment retry counter
            step_retry_counts[current_step.step_id] = retries_used + 1

            await self._handle_retry_action(
                current_step,
                review.reasoning,
                message_queue,
                room_id,
                user_message_id,
                user_id,
                current_message,
            )
            return ReviewAction.CONTINUE

        return ReviewAction.CONTINUE

    def _find_step_for_message(
        self,
        plan: SupervisorPlan,
        message: RoomAgentMessage,
    ) -> SupervisorStep | None:
        """Find the SupervisorStep that corresponds to a RoomAgentMessage.

        Matches by step_number (1-indexed) to step index in plan.steps.
        """
        if message.step_number is None:
            return None

        step_index = message.step_number - 1
        if 0 <= step_index < len(plan.steps):
            return plan.steps[step_index]

        return None

    async def _handle_revise_action(
        self,
        revised_steps: list[SupervisorStep],
        supervisor_plan: SupervisorPlan,
        completed_step_ids: set[str],
        message_queue: deque,
        room_id: str,
        user_message_id: str,
        user_id: str | None,
        current_message: RoomAgentMessage,
    ) -> None:
        """Handle Supervisor "revise" action by replacing remaining queue.

        Also updates supervisor_plan.steps in-place so that subsequent
        _find_step_for_message calls return the correct revised step.
        """
        logger.info(
            "QueueExecutor: Supervisor revised plan with %d new steps",
            len(revised_steps),
        )

        # Clear existing queue
        for msg in message_queue:
            await self.tsm.transition_task(
                msg, TaskState.canceled, persist=True, notify=False
            )
        message_queue.clear()

        # Update supervisor_plan.steps in-place:
        # Keep completed steps, replace remaining with revised steps
        completed_steps = [s for s in supervisor_plan.steps if s.step_id in completed_step_ids]
        supervisor_plan.steps[:] = completed_steps + revised_steps

        # Calculate step numbering: completed steps + revised steps
        base_step_number = len(completed_steps)
        total_steps = len(supervisor_plan.steps)

        # Generate new agent messages from revised steps
        for i, step in enumerate(revised_steps):
            new_message = self.room_services._generate_new_agent_message(
                room_id=room_id,
                related_message_id=current_message.message_id,
                agent_id=step.agent_id,
                content=step.task_description,
                user_id=user_id,
                step_number=base_step_number + i + 1,
                total_steps=total_steps,
            )

            success = await self.database_service.add_room_agent_message(new_message)
            if success:
                message_queue.append(new_message)
            else:
                logger.error(
                    "QueueExecutor: Failed to save revised step message %s",
                    new_message.message_id,
                )

    async def _handle_retry_action(
        self,
        step: SupervisorStep,
        retry_reasoning: str,
        message_queue: deque,
        room_id: str,
        user_message_id: str,
        user_id: str | None,
        current_message: RoomAgentMessage,
    ) -> None:
        """Handle Supervisor "retry" action by re-queuing the step."""
        logger.info(
            "QueueExecutor: Supervisor requested retry for step %s: %s",
            step.step_id,
            retry_reasoning[:100] if retry_reasoning else "",
        )

        # Create a refined task description with retry context
        refined_task = (
            f"{step.task_description}\n\n"
            f"[Retry requested: {retry_reasoning}]"
        )

        retry_message = self.room_services._generate_new_agent_message(
            room_id=room_id,
            related_message_id=current_message.related_message_id or user_message_id,
            agent_id=step.agent_id,
            content=refined_task,
            user_id=user_id,
            step_number=current_message.step_number,
            total_steps=current_message.total_steps,
        )

        success = await self.database_service.add_room_agent_message(retry_message)
        if success:
            # Insert at front of queue so it's processed next
            message_queue.appendleft(retry_message)
        else:
            logger.error(
                "QueueExecutor: Failed to save retry message for step %s",
                step.step_id,
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
