"""QueueExecutor — sequential agent-message queue processing.

Owns the queue loop, RAII cleanup (``_managed_queue``), continuation
save/resume for webhook-paused queues, per-item dispatch to the
``DirectTransport``, and queue chaining (``_queue_next_messages``).

Agent assignment is delegated to the injected ``AgentDispatcher``.

Since Phase 5 (V1 deprecation), the Supervisor review hook has been removed.
Supervisor-enabled rooms use ``SupervisorExecutor`` exclusively.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from a2a_adapter.task_status import coerce_task_state
from common.a2a_constants import SSEProcessingStatus
from common.message_commit_events import publish_message_committed
from common.utils.cancellation import CancellationToken
from common.utils.logger import get_logger
from execution.dispatch.agent_dispatcher import AgentDispatcher
from execution.dispatch.agent_message_processor import AgentMessageProcessor
from execution.state.task_state_manager import TaskStateManager
from execution.state.task_status_mapping import system_task_state_from_runtime_status
from models.processing import ProcessingResult, ProcessingStatus
from models.room import CoordinatorAgentId, RoomAgentMessage

if TYPE_CHECKING:
    from common.protocols import EventPublisher
    from execution.dispatch.response_handler import AgentResponseHandler
    from execution.ports import (
        DebateServicePort,
        ExecutionDeliveryPort,
        HITLCoordinator,
        RateLimitPort,
        RoomContinuationStore,
        RoomMemoryReader,
        RoomMessageReader,
        RoomMessageWriter,
        RoomReader,
        RoomRuntimePort,
        RoomTaskStateStore,
    )

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
    """Result of queue processing."""

    result: QueueResult


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
        delivery: ExecutionDeliveryPort,
        room_runtime: RoomRuntimePort,
        event_publisher: EventPublisher,
        message_reader: RoomMessageReader,
        message_writer: RoomMessageWriter,
        task_state_store: RoomTaskStateStore,
        continuation_store: RoomContinuationStore,
        agent_lookup: RoomReader,
        room_reader: RoomReader,
        memory_reader: RoomMemoryReader,
        debate_prompt_injector: DebateServicePort,
        rate_limit_service: RateLimitPort | None = None,
        agent_dispatcher: AgentDispatcher,
        agent_message_processor: AgentMessageProcessor,
        response_handler: AgentResponseHandler,
        slot_lifecycle=None,
        turn_event_appender=None,
        hitl_coordinator: HITLCoordinator | None = None,
    ) -> None:
        if event_publisher is None:
            raise RuntimeError("QueueExecutor event_publisher dependency is required")
        self.tsm = tsm
        self.delivery = delivery
        self.room_runtime = room_runtime
        self.event_publisher = event_publisher
        self.message_reader = message_reader
        self.message_writer = message_writer
        self.task_state_store = task_state_store
        self.continuation_store = continuation_store
        self.agent_lookup = agent_lookup
        self.room_reader = room_reader
        self.memory_reader = memory_reader
        self.debate_prompt_injector = debate_prompt_injector
        self.rate_limit_service = rate_limit_service
        self.agent_dispatcher = agent_dispatcher
        self._agent_message_processor = agent_message_processor
        self.response_handler = response_handler
        self._slot_lifecycle = slot_lifecycle
        self._turn_event_appender = turn_event_appender
        self.hitl_coordinator = hitl_coordinator
        self._processing_status_emitter = None

    def bind_execution_event_deps(self, processing_status_emitter) -> None:
        self._processing_status_emitter = processing_status_emitter

    async def _publish_agent_message_committed(
        self,
        *,
        room_id: str,
        message_id: str | None,
        agent_id: str | None,
        agent_name: str,
        was_successful: bool,
    ) -> None:
        if not message_id:
            return
        await publish_message_committed(
            self.event_publisher,
            room_id=room_id,
            message_id=message_id,
            message_type="agent",
            agent_id=agent_id,
            agent_name=agent_name,
            was_successful=was_successful,
        )

    async def _emit_processing_status(
        self,
        *,
        room_id: str,
        status,
        message_id: str | None,
        lifecycle_message_id: str | None = None,
        record_lifecycle: bool = True,
        client_request_id: str | None = None,
        details=None,
    ) -> None:
        if self._processing_status_emitter is None:
            raise RuntimeError("QueueExecutor execution event dependencies not bound")
        status_value = status.value if hasattr(status, "value") else str(status)
        await self._processing_status_emitter(
            room_id=room_id,
            status=status,
            message_id=message_id,
            lifecycle_message_id=lifecycle_message_id or message_id,
            record_lifecycle=record_lifecycle,
            client_request_id=client_request_id,
            details=(
                details
                if isinstance(details, dict)
                else {"message": details}
                if isinstance(details, str)
                else None
            ),
            error_message=(
                details
                if isinstance(details, str)
                and status_value in {"failed", "canceled", "rejected", "error"}
                else None
            ),
        )

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
                        msg, coerce_task_state("canceled"), persist=True
                    )
                canceled_ids = [msg.message_id for msg in message_queue]
                message_queue.clear()
                for mid in canceled_ids:
                    await self.message_writer.cancel_descendants(mid)
            if last_popped:
                await self.message_writer.cancel_descendants(last_popped[0])

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
        """
        logger.info(
            "QueueExecutor: Starting to process message queue with %d messages",
            len(message_queue),
        )

        sys_message_id = f"sys-{user_message_id}"
        client_req_id = await self.task_state_store.resolve_client_request_id_for_message_id(
            user_message_id
        )

        try:
            # Phase 1/3: Emit system:hybro task on start if not already emitted
            existing_sys_msg = await self.message_reader.get_room_agent_message_by_message_id(
                sys_message_id
            )
            if not existing_sys_msg:
                from common.utils.time import utcnow

                sys_msg = self.room_runtime.create_agent_message(
                    room_id=room_id,
                    related_message_id=user_message_id,
                    agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                    content="",
                    user_id=request_user_id,
                    step_number=0,
                    task_content="Orchestrating workflow...",
                    client_request_id=client_req_id,
                )
                sys_msg.message_id = sys_message_id
                await self.message_writer.add_room_agent_message(sys_msg)

                await self.delivery.send_task_submitted(
                    room_id=room_id,
                    message_id=sys_message_id,
                    task_id=sys_message_id,
                    agent_name="HYBRO AI",
                    agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                    status="working",
                    related_message_id=user_message_id,
                    created_at=utcnow().isoformat(),
                    task_content="Orchestrating workflow...",
                    client_request_id=client_req_id,
                )
        except Exception:
            logger.warning("Failed to emit system:hybro task", exc_info=True)

        queue_result = QueueResult.COMPLETED
        deferred_sse: tuple[SSEProcessingStatus, bool] | None = None

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
                        current_message, coerce_task_state("canceled"), persist=True
                    )
                    queue_result = QueueResult.CANCELED
                    deferred_sse = (SSEProcessingStatus.CANCELED, True)
                    break

                # --- Agent resolution ---
                agent = await self._resolve_agent_for_message(current_message, room_id)
                if agent is None:
                    queue_result = QueueResult.FAILED
                    break

                # --- Emit slot_opened turn event (Phase 1b) ---
                if getattr(self, "_slot_lifecycle", None) and current_message.turn_id:
                    try:
                        await self._slot_lifecycle.open_slot(
                            room_id=room_id,
                            turn_id=current_message.turn_id,
                            slot_id=current_message.message_id,
                            slot_type="agent",
                            agent_id=agent.agent_id,
                            agent_name=getattr(agent.agent_card, "name", None)
                            if hasattr(agent, "agent_card") and agent.agent_card
                            else None,
                        )
                    except Exception:
                        logger.warning(
                            "QueueExecutor: Failed to emit slot_opened for %s",
                            current_message.message_id,
                            exc_info=True,
                        )

                # --- Rate limit check ---
                if request_user_id:
                    rate_limited = await self._check_rate_limit(
                        current_message,
                        agent,
                        room_id,
                        user_message_id,
                        request_user_id,
                    )
                    if rate_limited:
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
                    break

                elif result.status == ProcessingStatus.CANCELED:
                    queue_result = QueueResult.CANCELED
                    deferred_sse = (SSEProcessingStatus.CANCELED, True)
                    break

                elif result.status == ProcessingStatus.AWAITING_INPUT:
                    # Agent returned input_required — create HITL request
                    # so the frontend shows an input form, then pause the
                    # queue exactly like PAUSED.
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
                        if self.hitl_coordinator is None:
                            raise RuntimeError("HITL coordinator has not been bound")
                        hitl_req = await self.hitl_coordinator.request_input(
                            room_id=room_id,
                            user_message_id=user_message_id,
                            source="agent",
                            prompt=(
                                result.status_message
                                or "The agent needs additional information."
                            ),
                            agent_id=current_message.agent_id,
                            agent_name=(agent.agent_card.name if agent else None),
                            a2a_task_id=result.a2a_task_id,
                            a2a_context_id=result.a2a_context_id,
                            continuation_message_id=result.message_id,
                            display_message_id=current_message.message_id,
                        )
                        if hitl_req is None:
                            logger.warning(
                                "QueueExecutor: Max HITL rounds exceeded "
                                "for message %s — failing queue",
                                result.message_id,
                            )
                            queue_result = QueueResult.FAILED
                            break
                        await self._emit_processing_status(
                            room_id=room_id,
                            status=SSEProcessingStatus.AWAITING_INPUT,
                            message_id=user_message_id,
                            lifecycle_message_id=user_message_id,
                        )
                        logger.info(
                            "QueueExecutor: Queue paused for HITL on message %s",
                            result.message_id,
                        )
                    message_queue.clear()
                    last_popped.clear()
                    queue_result = QueueResult.PAUSED
                    break

                elif result.status in (
                    ProcessingStatus.PAUSED,
                    ProcessingStatus.RELAY_DISPATCHED,
                ):
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
                    last_popped.clear()
                    queue_result = QueueResult.PAUSED
                    break

                # --- Success: continue loop ---

                if request_user_id:
                    await self.rate_limit_service.record_request(
                        agent_id=agent.agent_id,
                        user_id=request_user_id,
                    )

                if result.response_text:
                    await self._publish_agent_message_committed(
                        room_id=room_id,
                        agent_id=current_message.agent_id,
                        agent_name=agent.agent_card.name if agent else "Agent",
                        was_successful=result.status == ProcessingStatus.SUCCESS,
                        message_id=getattr(current_message, "message_id", None),
                    )

                # Queue up next messages in the chain (skip for direct chat)
                if not is_direct_chat:
                    await self._queue_next_messages(
                        current_message, message_queue, room_id
                    )

            else:
                last_popped.clear()

        # Phase 2: deferred SSE notification
        # Phase 3: Emit terminal state for system:hybro
        if queue_result != QueueResult.PAUSED:
            try:
                task_status = (
                    "completed"
                    if queue_result == QueueResult.COMPLETED
                    else queue_result.value
                )
                await self.delivery.send_task_update(
                    room_id=room_id,
                    message_id=sys_message_id,
                    status=task_status,
                )

                db_msg = await self.message_reader.get_room_agent_message_by_message_id(
                    sys_message_id
                )
                if (
                    db_msg
                    and db_msg.message_content
                    and db_msg.message_content.message_task
                ):
                    db_msg.message_content.message_task.status.state = (
                        system_task_state_from_runtime_status(task_status)
                    )
                    await self.message_writer.update_room_agent_message_with_new_message_content_by_message_id(
                        db_msg.message_id, db_msg.message_content
                    )
            except Exception:
                logger.warning(
                    "Failed to update terminal state for system:hybro", exc_info=True
                )

        if deferred_sse:
            sse_status, clear_cancel = deferred_sse
            if sse_status == SSEProcessingStatus.CANCELED and getattr(
                self, "_turn_event_appender", None
            ):
                try:
                    await self._turn_event_appender.append(
                        room_id,
                        user_message_id,
                        "turn_canceled",
                        {},
                    )
                except Exception:
                    pass
            await self._emit_processing_status(
                room_id=room_id,
                status=sse_status,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
            )
            if clear_cancel:
                self.delivery.clear_cancellation(user_message_id)

        if queue_result == QueueResult.COMPLETED:
            logger.info("QueueExecutor: Finished processing message queue")

        return QueueProcessingResult(result=queue_result)

    # ------------------------------------------------------------------
    # Agent resolution / rate-limit helpers (delegated from queue loop)
    # ------------------------------------------------------------------

    async def _resolve_agent_for_message(
        self,
        current_message: RoomAgentMessage,
        room_id: str,
    ):
        """Resolve or verify the agent for *current_message*.

        If no ``agent_id`` is set, delegates to the ``AgentDispatcher``.
        If one is set, fetches from DB and verifies it is still active,
        re-assigning via the dispatcher if not.

        Returns the ``Agent`` on success or ``None`` after persisting a
        failure notification on the message.
        """
        from models.agent import AgentStatus

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
                await self.tsm.transition_task(
                    current_message,
                    coerce_task_state("failed"),
                    error=error_text,
                    persist=True,
                )
                await self.response_handler.notify_task_update(
                    message_id=current_message.message_id,
                    state=coerce_task_state("failed"),
                    room_id=room_id,
                    user_id=current_message.user_id or "",
                    error=error_text,
                )
                # --- Emit failed slot (Phase 1b) ---
                if getattr(self, "_slot_lifecycle", None) and current_message.turn_id:
                    try:
                        await self._slot_lifecycle.open_slot(
                            room_id=room_id,
                            turn_id=current_message.turn_id,
                            slot_id=current_message.message_id,
                            slot_type="agent",
                            agent_id=current_message.agent_id,
                        )
                        await self._slot_lifecycle.terminate_slot(
                            room_id=room_id,
                            turn_id=current_message.turn_id,
                            slot_id=current_message.message_id,
                            status="failed",
                            error="agent_unavailable",
                        )
                    except Exception:
                        logger.warning(
                            "QueueExecutor: Failed to emit agent_unavailable slot for %s",
                            current_message.message_id,
                            exc_info=True,
                        )
                return None
            return agent

        agent = await self.agent_lookup.get_agent_by_agent_id(current_message.agent_id)
        if agent is None:
            logger.error(
                "QueueExecutor: Assigned agent %s not found for message %s",
                current_message.agent_id,
                current_message.message_id,
            )
            await self.tsm.transition_task(
                current_message,
                coerce_task_state("failed"),
                error="The assigned agent could not be found.",
                persist=True,
            )
            await self.response_handler.notify_task_update(
                message_id=current_message.message_id,
                state=coerce_task_state("failed"),
                room_id=room_id,
                user_id=current_message.user_id or "",
                error="The assigned agent could not be found.",
            )
            # --- Emit failed slot (Phase 1b) ---
            if getattr(self, "_slot_lifecycle", None) and current_message.turn_id:
                try:
                    await self._slot_lifecycle.open_slot(
                        room_id=room_id,
                        turn_id=current_message.turn_id,
                        slot_id=current_message.message_id,
                        slot_type="agent",
                        agent_id=current_message.agent_id,
                    )
                    await self._slot_lifecycle.terminate_slot(
                        room_id=room_id,
                        turn_id=current_message.turn_id,
                        slot_id=current_message.message_id,
                        status="failed",
                        error="agent_unavailable",
                    )
                except Exception:
                    logger.warning(
                        "QueueExecutor: Failed to emit agent_unavailable slot for %s",
                        current_message.message_id,
                        exc_info=True,
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
            (
                reassigned,
                failure_reason,
            ) = await self.agent_dispatcher.assign_agent_for_queue(current_message)
            if reassigned is None:
                error_text = (
                    failure_reason
                    or "The assigned agent is no longer available and no alternative could be found."
                )
                current_message.agent_id = original_agent_id
                await self.tsm.transition_task(
                    current_message,
                    coerce_task_state("failed"),
                    error=error_text,
                    persist=True,
                )
                await self.response_handler.notify_task_update(
                    message_id=current_message.message_id,
                    state=coerce_task_state("failed"),
                    room_id=room_id,
                    user_id=current_message.user_id or "",
                    error=error_text,
                )
                # --- Emit failed slot (Phase 1b) ---
                if getattr(self, "_slot_lifecycle", None) and current_message.turn_id:
                    try:
                        await self._slot_lifecycle.open_slot(
                            room_id=room_id,
                            turn_id=current_message.turn_id,
                            slot_id=current_message.message_id,
                            slot_type="agent",
                            agent_id=current_message.agent_id,
                        )
                        await self._slot_lifecycle.terminate_slot(
                            room_id=room_id,
                            turn_id=current_message.turn_id,
                            slot_id=current_message.message_id,
                            status="failed",
                            error="agent_unavailable",
                        )
                    except Exception:
                        logger.warning(
                            "QueueExecutor: Failed to emit agent_unavailable slot for %s",
                            current_message.message_id,
                            exc_info=True,
                        )
                return None
            return reassigned

        return agent

    async def _check_rate_limit(
        self,
        current_message: RoomAgentMessage,
        agent,
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
            await self.delivery.send_rate_limit_error(
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
            await self.tsm.transition_task(
                current_message, coerce_task_state("canceled"), persist=True
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
        agent,
        user_message_id: str,
        *,
        token: CancellationToken | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        quoted_text: str | None = None,
    ) -> ProcessingResult:
        """Process a single agent message.

        Delegates to ``AgentMessageProcessor.process_single_message``.
        """
        return await self._agent_message_processor.process_single_message(
            current_message,
            room_id,
            agent,
            user_message_id,
            token=token,
            step_number=step_number,
            total_steps=total_steps,
            quoted_text=quoted_text,
        )

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
        current_agent,
    ) -> None:
        """Save queue continuation state for a push notification task."""
        serialized_queue = [msg.model_dump(mode="json") for msg in message_queue]

        continuation_data: dict = {
            "remaining_queue": serialized_queue,
            "room_id": room_id,
            "user_message_id": user_message_id,
            "request_user_id": request_user_id,
            "current_agent_id": current_agent.agent_id,
            "current_agent_name": current_agent.agent_card.name,
        }

        success = await self.continuation_store.save_continuation_on_message(
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
        *,
        before_terminal_failure: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> ResumeResult:
        """Resume queue processing after a push notification task completes.

        Called from the webhook handler when a task reaches a terminal state.

        Returns a ``ResumeResult`` indicating whether the caller should trigger
        post-completion logic (coordinator + COMPLETED SSE status).
        """
        continuation = await self.continuation_store.get_and_clear_continuation_on_message(
            message_id
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

        quoted_text_resume: str | None = None
        um_resume = await self.message_reader.get_room_user_message_by_message_id(
            user_message_id
        )
        if um_resume:
            from execution.orchestration.turn_context import (
                TurnQuoteMissingError,
                load_turn_context,
            )

            try:
                tc = await load_turn_context(self.message_reader, um_resume)
                quoted_text_resume = tc.quoted_text
            except TurnQuoteMissingError:
                logger.error(
                    "QueueExecutor: missing quoted snippet for turn %s on resume",
                    user_message_id,
                )
                return ResumeResult(success=False)
            if quoted_text_resume is None and isinstance(um_resume.extend_info, dict):
                quoted_text_resume = um_resume.extend_info.get("quoted_text")

        if task_result_text:
            current_agent_id = continuation.get("current_agent_id")
            current_agent_name = continuation.get("current_agent_name", "Agent")
            await self._publish_agent_message_committed(
                room_id=room_id,
                agent_id=current_agent_id,
                agent_name=current_agent_name,
                was_successful=True,
                message_id=message_id,
            )

        if len(remaining_queue) > 0:
            token = self.delivery.get_token(user_message_id)
            if token is None:
                token = self.delivery.create_token(user_message_id)

            queue_processing_result = await self.process_queue(
                remaining_queue,
                room_id,
                user_message_id,
                token=token,
                request_user_id=request_user_id,
                quoted_text=quoted_text_resume,
            )

            if queue_processing_result.result == QueueResult.PAUSED:
                return ResumeResult(success=True)
            if queue_processing_result.result == QueueResult.FAILED:
                if before_terminal_failure is not None:
                    await before_terminal_failure(room_id, user_message_id)
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.FAILED,
                    message_id=user_message_id,
                    lifecycle_message_id=user_message_id,
                )
                return ResumeResult(
                    success=False,
                    room_id=room_id,
                    user_message_id=user_message_id,
                )
            if queue_processing_result.result == QueueResult.CANCELED:
                return ResumeResult(success=True)

            return ResumeResult(
                success=True,
                needs_completion=True,
                room_id=room_id,
                user_message_id=user_message_id,
            )

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
        next_messages = await self.message_reader.get_room_agent_messages_by_related_message_id(
            current_message.message_id
        )
        logger.info(
            "QueueExecutor: Found %d next messages for message %s",
            len(next_messages),
            current_message.message_id,
        )

        is_debate_mode = False
        room = await self.room_reader.get_room_by_room_id(room_id)
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
                    await self.debate_prompt_injector.inject_short_debate_for_agent_message(
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
