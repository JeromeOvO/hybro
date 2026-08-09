"""QueueExecutor — sequential agent-message queue processing.

Owns the queue loop, RAII cleanup (``_managed_queue``), continuation
save/resume for webhook-paused queues, per-item dispatch to the
``DirectTransport``, and queue chaining (``_queue_next_messages``).

Agent assignment is delegated to the injected ``AgentDispatcher``.

The Supervisor review hook has been removed; the Supervisor review hook has been removed.
Supervisor-enabled rooms use ``SupervisorExecutor`` exclusively.
"""

from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

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
    from common.eventing import InternalEventPublisher
    from execution.dispatch.response_handler import AgentResponseHandler
    from execution.ports import (
        CancellationControlPort,
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
_GENERIC_AGENT_INPUT_PROMPT = "The agent needs additional information."


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
    """Result plus the exact terminal projection requested by the queue."""

    result: QueueResult
    terminal_status: SSEProcessingStatus | None = None
    system_message_id: str | None = None
    clear_cancellation: bool = False
    error_message: str | None = None
    error_code: str | None = None


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
    token: CancellationToken | None = None


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
        cancellation_control: CancellationControlPort,
        room_runtime: RoomRuntimePort,
        internal_event_publisher: InternalEventPublisher,
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
        if internal_event_publisher is None:
            raise RuntimeError(
                "QueueExecutor internal_event_publisher dependency is required"
            )
        self.tsm = tsm
        self.delivery = delivery
        if cancellation_control is None:
            raise RuntimeError("QueueExecutor cancellation_control is required")
        self.cancellation_control = cancellation_control
        self.room_runtime = room_runtime
        self.internal_event_publisher = internal_event_publisher
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

    def _release_cancellation_token(
        self,
        message_id: str,
        token: CancellationToken,
    ) -> None:
        self.cancellation_control.release_token(message_id, token)

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
            self.internal_event_publisher,
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
        system_message_id: str | None = None,
        turn_event_enabled: bool = False,
    ) -> dict | None:
        if self._processing_status_emitter is None:
            raise RuntimeError("QueueExecutor execution event dependencies not bound")
        status_value = status.value if hasattr(status, "value") else str(status)
        return await self._processing_status_emitter(
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
            system_message_id=system_message_id,
            turn_event_enabled=turn_event_enabled,
        )

    # ------------------------------------------------------------------
    # RAII queue cleanup (A-2)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _managed_queue(self, message_queue: deque, last_popped: list[str]):
        """Release only in-memory queue ownership on an early exit.

        Child persistence is intentionally absent here. Failed/canceled roots
        carry durable descendant-cleanup intent, so an opposing terminal loser
        cannot mutate children before or after losing the root CAS.
        """
        try:
            yield message_queue
        finally:
            message_queue.clear()
            last_popped.clear()

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
        client_req_id = (
            await self.task_state_store.resolve_client_request_id_for_message_id(
                user_message_id
            )
        )

        system_message_ready = False
        system_message_error: BaseException | None = None
        try:
            # No agent dispatch starts until its durable system task exists.
            existing_sys_msg = (
                await self.message_reader.get_room_agent_message_by_message_id(
                    sys_message_id
                )
            )
            system_message_ready = existing_sys_msg is not None
            if not system_message_ready:
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
                for _attempt in range(3):
                    try:
                        system_message_ready = bool(
                            await self.message_writer.add_room_agent_message(sys_msg)
                        )
                    except Exception as exc:
                        system_message_error = exc
                    if not system_message_ready:
                        system_message_ready = (
                            await self.message_reader.get_room_agent_message_by_message_id(
                                sys_message_id
                            )
                            is not None
                        )
                    if system_message_ready:
                        break
                if system_message_ready:
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
        except Exception as exc:
            system_message_error = exc
            logger.warning("Failed to emit system:hybro task", exc_info=True)

        if not system_message_ready:
            logger.error(
                "QueueExecutor: system task persistence failed; refusing dispatch "
                "for root %s (error_class=%s)",
                user_message_id,
                type(system_message_error).__name__
                if system_message_error is not None
                else "unacknowledged_write",
            )
            message_queue.clear()
            return QueueProcessingResult(
                result=QueueResult.FAILED,
                terminal_status=None,
                system_message_id=None,
                clear_cancellation=False,
                error_message="Failed to create durable system task",
                error_code="system_task_persistence_failed",
            )

        queue_result = QueueResult.COMPLETED
        deferred_sse: tuple[SSEProcessingStatus, bool] | None = None
        failure_error: str | None = None
        failure_code: str | None = None

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
                    error_text = result.response_text or "Agent processing failed"
                    preflight_failure = (
                        current_message.extend_info.get("attachment_preflight_failure")
                        if isinstance(current_message.extend_info, dict)
                        else None
                    )
                    failure_error = error_text
                    if preflight_failure is not None:
                        failure_code = (
                            str(preflight_failure.get("code"))
                            if isinstance(preflight_failure, dict)
                            and preflight_failure.get("code")
                            else result.status_message
                        )
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
                            prompt=_GENERIC_AGENT_INPUT_PROMPT,
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

        # Cancellation may arrive after the final agent completed but before
        # summary/terminal projection. Convert that boundary to normal cancel
        # semantics before exposing any completed task state.
        if (
            queue_result == QueueResult.COMPLETED
            and token is not None
            and token.is_cancelled
        ):
            queue_result = QueueResult.CANCELED
            deferred_sse = (SSEProcessingStatus.CANCELED, True)

        # Root terminal persistence and its durable projection intent always
        # precede child task/turn/SSE projections.
        if deferred_sse:
            sse_status, _clear_cancel = deferred_sse
            await self._emit_processing_status(
                room_id=room_id,
                status=sse_status,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
                system_message_id=sys_message_id,
                turn_event_enabled=bool(getattr(self, "_turn_event_appender", None)),
            )
            # Cancellation tombstones are cleared only by CancellationFinalizer
            # after propagation and durable marker reconciliation succeed.

        if queue_result == QueueResult.COMPLETED:
            logger.info("QueueExecutor: Finished processing message queue")

        return QueueProcessingResult(
            result=queue_result,
            terminal_status=deferred_sse[0] if deferred_sse else None,
            system_message_id=sys_message_id,
            clear_cancellation=deferred_sse[1] if deferred_sse else False,
            error_message=failure_error,
            error_code=failure_code,
        )

    async def _terminalize_system_task(
        self,
        *,
        room_id: str,
        sys_message_id: str,
        task_status: str,
        token: CancellationToken | None,
    ) -> str:
        cancellation_sensitive = task_status == "completed" and token is not None
        effective_status = (
            "canceled" if cancellation_sensitive and token.is_cancelled else task_status
        )
        db_msg = await self.message_reader.get_room_agent_message_by_message_id(
            sys_message_id
        )
        if not (
            db_msg and db_msg.message_content and db_msg.message_content.message_task
        ):
            raise RuntimeError(
                f"system task message {sys_message_id!r} is missing task state"
            )

        async def persist(status: str) -> None:
            db_msg.message_content.message_task.status.state = (
                system_task_state_from_runtime_status(status)
            )
            last_error: BaseException | None = None
            for _attempt in range(3):
                try:
                    persisted = await self.message_writer.update_room_agent_message_with_new_message_content_by_message_id(
                        db_msg.message_id, db_msg.message_content
                    )
                except Exception as exc:
                    last_error = exc
                    continue
                if persisted:
                    return
                last_error = RuntimeError(
                    f"failed to persist system task {sys_message_id!r} as {status}"
                )
            assert last_error is not None
            raise last_error

        await persist(effective_status)
        if (
            cancellation_sensitive
            and token.is_cancelled
            and effective_status == "completed"
        ):
            effective_status = "canceled"
            await persist(effective_status)

        await self.delivery.send_task_update(
            room_id=room_id,
            message_id=sys_message_id,
            status=effective_status,
        )
        if (
            cancellation_sensitive
            and token.is_cancelled
            and effective_status == "completed"
        ):
            effective_status = "canceled"
            await persist(effective_status)
            await self.delivery.send_task_update(
                room_id=room_id,
                message_id=sys_message_id,
                status=effective_status,
            )
        return effective_status

    # ------------------------------------------------------------------
    # Agent resolution / rate-limit helpers (delegated from queue loop)
    # ------------------------------------------------------------------

    async def _resolve_agent_for_message(
        self,
        current_message: RoomAgentMessage,
        room_id: str,
    ):
        """Resolve an agent without terminalizing a child before the root CAS."""
        from models.agent import AgentStatus

        del room_id
        if current_message.agent_id is None:
            agent, _failure_reason = await self.agent_dispatcher.assign_agent_for_queue(
                current_message
            )
            return agent

        agent = await self.agent_lookup.get_agent_by_agent_id(current_message.agent_id)
        if agent is None:
            logger.error(
                "QueueExecutor: Assigned agent %s not found for message %s",
                current_message.agent_id,
                current_message.message_id,
            )
            return None
        if agent.agent_status == AgentStatus.active:
            return agent

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
            _failure_reason,
        ) = await self.agent_dispatcher.assign_agent_for_queue(current_message)
        if reassigned is None:
            current_message.agent_id = original_agent_id
        return reassigned

    async def _check_rate_limit(
        self,
        current_message: RoomAgentMessage,
        agent,
        room_id: str,
        user_message_id: str,
        request_user_id: str,
    ) -> bool:
        """Check rate limits. Returns ``True`` if rate-limited (caller should cancel)."""
        del current_message, room_id, user_message_id
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
            # Root terminal projection owns child DB/SSE convergence. A losing
            # rate-limit writer must not mutate or publish the child task.
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

    async def _restore_invalid_continuation(
        self,
        message_id: str,
        continuation: object,
        *,
        reason: str,
    ) -> None:
        """Restore an invalid destructive claim, or durably fail its root."""
        try:
            restored = await self.continuation_store.save_continuation_on_message(
                message_id, continuation
            )
        except Exception:
            restored = False
            logger.warning(
                "QueueExecutor: continuation restore raised for message %s",
                message_id,
                exc_info=True,
            )
        if restored:
            return

        continuation_fields = continuation if isinstance(continuation, dict) else {}
        root_id = continuation_fields.get("user_message_id")
        room_id = continuation_fields.get("room_id")
        agent_message = None
        if not root_id or not room_id:
            agent_message = (
                await self.message_reader.get_room_agent_message_by_message_id(
                    message_id
                )
            )
            root_id = root_id or getattr(agent_message, "related_message_id", None)
            room_id = room_id or getattr(agent_message, "room_id", None)
        if (
            isinstance(root_id, str)
            and root_id
            and isinstance(room_id, str)
            and room_id
        ):
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=root_id,
                lifecycle_message_id=root_id,
                system_message_id=f"sys-{root_id}",
                details={"message": reason, "code": "invalid_continuation"},
                turn_event_enabled=False,
            )
            return
        raise RuntimeError(
            f"unable to restore or terminalize continuation {message_id}: {reason}"
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
            await self.continuation_store.get_and_clear_continuation_on_message(
                message_id
            )
        )

        if not continuation:
            logger.debug(
                "QueueExecutor: No continuation found for message %s",
                message_id,
            )
            return ResumeResult(success=False)

        continuation_fields = continuation if isinstance(continuation, dict) else {}
        room_id = continuation_fields.get("room_id")
        user_message_id = continuation_fields.get("user_message_id")
        request_user_id = continuation_fields.get("request_user_id")

        if not user_message_id:
            # The destructive continuation claim owns cleanup. Recover the
            # root ID from the agent message when legacy/corrupt continuation
            # data omitted it, then identity-release the paused token.
            agent_message = (
                await self.message_reader.get_room_agent_message_by_message_id(
                    message_id
                )
            )
            recovered_id = getattr(agent_message, "related_message_id", None)
            if isinstance(recovered_id, str) and recovered_id:
                token = self.cancellation_control.get_token(recovered_id)
                self._release_cancellation_token(recovered_id, token)
            logger.error(
                "QueueExecutor: Invalid continuation data for message %s",
                message_id,
            )
            await self._restore_invalid_continuation(
                message_id,
                continuation,
                reason="continuation is missing user_message_id",
            )
            return ResumeResult(success=False)

        token = self.cancellation_control.create_token(user_message_id)
        if not room_id:
            logger.error(
                "QueueExecutor: Invalid continuation data for message %s",
                message_id,
            )
            try:
                await self._restore_invalid_continuation(
                    message_id,
                    continuation,
                    reason="continuation is missing room_id",
                )
            finally:
                self._release_cancellation_token(user_message_id, token)
            return ResumeResult(success=False)

        try:
            raw_queue = continuation_fields.get("remaining_queue", [])
            if not isinstance(raw_queue, list):
                raise TypeError("continuation remaining_queue must be a list")
            remaining_queue = deque(
                RoomAgentMessage.model_validate(msg_data) for msg_data in raw_queue
            )
        except Exception as exc:
            try:
                await self._restore_invalid_continuation(
                    message_id,
                    continuation,
                    reason=f"continuation queue is malformed: {exc.__class__.__name__}",
                )
            finally:
                self._release_cancellation_token(user_message_id, token)
                logger.warning(
                    "QueueExecutor: restored malformed continuation for message %s",
                    message_id,
                    exc_info=True,
                )
            return ResumeResult(success=False)

        logger.info(
            "QueueExecutor: Resuming queue for message %s with %d remaining messages",
            message_id,
            len(remaining_queue),
        )

        # Destructive continuation claim transfers ownership of the paused
        # token to this resume attempt. Hydrate Redis before sync checkpoints.
        try:
            await self.cancellation_control.check_cancelled(user_message_id)
            if token.is_cancelled:
                self._release_cancellation_token(user_message_id, token)
                return ResumeResult(success=True)

            quoted_text_resume: str | None = None
            um_resume = await self.message_reader.get_room_user_message_by_message_id(
                user_message_id
            )
            if um_resume is None:
                await self._restore_invalid_continuation(
                    message_id,
                    continuation,
                    reason=f"root user message is missing: {user_message_id}",
                )
                self._release_cancellation_token(user_message_id, token)
                return ResumeResult(success=False)

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
                await self._restore_invalid_continuation(
                    message_id,
                    continuation,
                    reason=f"quoted snippet is missing: {user_message_id}",
                )
                self._release_cancellation_token(user_message_id, token)
                return ResumeResult(success=False)
            if quoted_text_resume is None and isinstance(um_resume.extend_info, dict):
                quoted_text_resume = um_resume.extend_info.get("quoted_text")

            if task_result_text:
                await self._publish_agent_message_committed(
                    room_id=room_id,
                    agent_id=continuation_fields.get("current_agent_id"),
                    agent_name=continuation_fields.get("current_agent_name", "Agent"),
                    was_successful=True,
                    message_id=message_id,
                )

            if not remaining_queue:
                return ResumeResult(
                    success=True,
                    needs_completion=True,
                    room_id=room_id,
                    user_message_id=user_message_id,
                    token=token,
                )

            queue_processing_result = await self.process_queue(
                remaining_queue,
                room_id,
                user_message_id,
                token=token,
                request_user_id=request_user_id,
                quoted_text=quoted_text_resume,
            )
        except BaseException:
            self._release_cancellation_token(user_message_id, token)
            raise

        if queue_processing_result.result == QueueResult.PAUSED:
            self._release_cancellation_token(user_message_id, token)
            return ResumeResult(success=True)

        if queue_processing_result.result == QueueResult.FAILED:
            self._release_cancellation_token(user_message_id, token)
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
                system_message_id=getattr(
                    queue_processing_result, "system_message_id", None
                ),
                turn_event_enabled=False,
            )
            return ResumeResult(
                success=False,
                room_id=room_id,
                user_message_id=user_message_id,
            )
        if queue_processing_result.result == QueueResult.CANCELED:
            self._release_cancellation_token(user_message_id, token)
            return ResumeResult(success=True)

        return ResumeResult(
            success=True,
            needs_completion=True,
            room_id=room_id,
            user_message_id=user_message_id,
            token=token,
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
            await self.message_reader.get_room_agent_messages_by_related_message_id(
                current_message.message_id
            )
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
                new_agent_message = await self.debate_prompt_injector.inject_short_debate_for_agent_message(
                    next_message
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
