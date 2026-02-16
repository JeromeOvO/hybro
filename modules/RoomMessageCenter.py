import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

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
from common.utils.cancellation import CancellationError, CancellationToken
from common.utils.context_utils import get_context_stats
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.agent import Agent, AgentStatus
from models.memory import MemoryContent
from models.request import OrchestrationRequest, RoomCenterAgentMessageRequest
from models.response import OrchestrationResponse
from models.room import RoomAgentMessage
from services.a2a_constants import (
    TERMINAL_STATES,
    SSEProcessingStatus,
    SyntheticTaskId,
    is_failure_state,
    is_terminal_state,
)
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


class ProcessingStatus(Enum):
    """Status of message processing operations."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"  # Queue paused waiting for push notification task


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
class MessageStreamingState:
    """Tracks mutable streaming state across sub-handlers during a single streaming session."""

    full_response_text: str = ""
    accumulated_parts: list[Part] = field(default_factory=list)
    agent_message_id: str | None = None
    message_added_to_history: bool = False


@dataclass
class ProcessingContext:
    """Bundles the common parameters threaded through streaming/sync sub-handlers."""

    room_id: str
    current_message: RoomAgentMessage
    agent_card: AgentCard
    user_message_id: str
    token: CancellationToken | None = None
    task_info: dict[str, Any] | None = None
    created_at: str | None = None
    step_number: int | None = None
    total_steps: int | None = None
    send_sse: bool = False

    @property
    def tracked_message_id(self) -> str | None:
        """Return message_id only if task tracking was set up."""
        return self.current_message.message_id if self.task_info else None


def _state_str(state) -> str:
    """Convert a TaskState enum (or string) to its string value."""
    return state.value if hasattr(state, "value") else str(state)


@dataclass
class AssignResult:
    """Result of ``_assign_agent``.

    Replaces the old ``self._last_resolve_failure`` pattern which stored the
    failure reason on the singleton instance — a concurrency hazard when
    multiple asyncio tasks process different rooms simultaneously (Issue 16).
    """

    agent: Agent | None
    failure_reason: str | None = None


def _get_task(msg: RoomAgentMessage) -> Task | None:
    """Safely access ``msg.message_content.message_task``, returning None on any miss."""
    if msg.message_content and msg.message_content.message_task:
        return msg.message_content.message_task
    return None


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

    # ------------------------------------------------------------------
    # Shared helpers (reduce repeated boilerplate)
    # ------------------------------------------------------------------

    async def _persist_message(self, message: RoomAgentMessage) -> bool:
        """Persist a RoomAgentMessage to the database. Returns True on success."""
        resp = await self.room_services.update_agent_message_by_message_id(
            RoomCenterAgentMessageRequest(
                message_id=message.message_id, message=message
            )
        )
        if not resp.success:
            logger.error(
                "RoomMessageCenter: Failed to persist message %s: %s",
                message.message_id,
                resp.error,
            )
        return resp.success

    async def _notify_task(self, ctx: ProcessingContext, status: str, **kwargs) -> None:
        """Send a task update notification using common fields from *ctx*."""
        await self.notification_service.send_task_update(
            room_id=ctx.room_id,
            message_id=ctx.tracked_message_id,
            status=status,
            agent_card=ctx.agent_card,
            agent_id=ctx.current_message.agent_id,
            created_at=ctx.created_at,
            step_number=ctx.step_number,
            total_steps=ctx.total_steps,
            **kwargs,
        )

    async def _fail_task_and_notify(
        self,
        *,
        room_id: str,
        message: RoomAgentMessage,
        error_text: str,
        agent_id: str | None,
        agent_card: AgentCard | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> None:
        """Persist a failed TaskStatus on *message* and send the failure notification.

        Delegates state persistence to ``_transition_task`` (which includes the
        terminal-state guard) and then sends the notification via
        ``notification_service`` with the full set of display parameters.

        *step_number* / *total_steps* default to the values stored on *message*
        when not supplied explicitly.  *agent_card* is forwarded to the
        notification service so it can resolve the agent display-name.
        """
        await self._transition_task(
            message, TaskState.failed, error=error_text, persist=True, notify=False
        )
        await self.notification_service.send_task_update(
            room_id=room_id,
            message_id=message.message_id,
            status=TaskState.failed,
            error=error_text,
            agent_id=agent_id,
            agent_card=agent_card,
            step_number=step_number if step_number is not None else message.step_number,
            total_steps=total_steps if total_steps is not None else message.total_steps,
            task_content=message.task_content,
        )

    async def _transition_task(
        self,
        message: RoomAgentMessage,
        new_state: TaskState,
        *,
        ctx: ProcessingContext | None = None,
        error: str | None = None,
        content: str | None = None,
        notify: bool = True,
        persist: bool = True,
    ) -> None:
        """Single entry point for all task state transitions.

        Always persists by default. Always notifies by default (when *ctx* is
        provided).  Callers opt out explicitly (e.g., ``notify=False`` for batch
        queue cleanup).

        The terminal-state guard prevents overwriting a ``completed``,
        ``failed``, ``canceled``, or ``rejected`` status — making double-
        transition bugs harmless instead of data-corrupting.
        """
        task = _get_task(message)
        if not task:
            return

        # Guard: never overwrite a terminal state
        if task.status and is_terminal_state(task.status.state):
            logger.warning(
                "Attempted to transition already-terminal task %s from %s to %s",
                message.message_id,
                _state_str(task.status.state),
                _state_str(new_state),
            )
            return

        # Update state
        task.status = TaskStatus(state=new_state)
        if error:
            task.status.message = Message(
                message_id=uuid4().hex,
                role=Role.agent,
                parts=[TextPart(text=error)],
            )
        message.task_updated_at = utcnow()

        if persist:
            await self._persist_message(message)

        if notify and ctx:
            await self._notify_task(
                ctx, new_state, content=content, error=error
            )

    async def _cancel_remaining_queue(
        self,
        message_queue: deque,
        current_message: RoomAgentMessage | None = None,
    ) -> None:
        """Persist ``TaskState.canceled`` for *current_message* (if given)
        and every remaining message in *message_queue*.

        Already-terminal messages are skipped by ``_transition_task``'s guard.
        """
        messages_to_cancel: list[RoomAgentMessage] = []
        if current_message is not None:
            messages_to_cancel.append(current_message)
        messages_to_cancel.extend(message_queue)

        for msg in messages_to_cancel:
            await self._transition_task(
                msg, TaskState.canceled, persist=True, notify=False
            )

    async def _try_cancel_remote_task(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
    ) -> None:
        """Best-effort, fire-and-forget cancellation of the remote A2A agent task.

        Looks for a remote task ID on the message's stored task object.
        If found, sends a ``tasks/cancel`` JSON-RPC request to the agent.
        Failures are logged at DEBUG level and silently ignored — the local
        cancellation must not depend on the remote agent supporting cancel.
        """
        task = _get_task(current_message)
        if not task:
            return
        remote_task_id = task.id if task.id else None
        # The placeholder ID format "pending-<uuid>" means we haven't received
        # a real task ID from the agent yet — no point trying to cancel it.
        if not remote_task_id or remote_task_id.startswith("pending-"):
            logger.debug(
                "RoomMessageCenter: No remote task ID to cancel for message %s",
                current_message.message_id,
            )
            return

        logger.info(
            "RoomMessageCenter: Attempting to cancel remote task %s on agent %s",
            remote_task_id,
            agent_card.name,
        )
        await self.a2a_service.cancel_remote_task(agent_card, remote_task_id)

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
                await self._cancel_remaining_queue(message_queue)

    # ------------------------------------------------------------------

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

            # Use the explicit task_content field for frontend display.
            # For direct chat this is None (avoids echoing the user's message);
            # for workflow steps it carries a meaningful task description.
            task_content = current_message.task_content

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
                task_id=SyntheticTaskId.PENDING,
                agent_name=agent_card.name,
                agent_id=current_message.agent_id,
                status=TaskState.working,
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

    async def _setup_tracking_context(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        prepared_message: Message,
        room_id: str,
        user_message_id: str,
        *,
        token: CancellationToken | None = None,
        send_sse: bool = False,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> tuple[dict[str, Any] | None, ProcessingContext]:
        """Set up task tracking and build a ProcessingContext in one step.

        Combines ``_setup_task_tracking`` with ``ProcessingContext`` construction
        to avoid duplicating this boilerplate across streaming and sync paths.

        Returns:
            A (task_info, ctx) tuple.  *task_info* may be ``None`` when tracking
            setup fails (degraded mode).
        """
        task_info = await self._setup_task_tracking(
            current_message,
            agent_card,
            prepared_message,
            room_id,
            step_number=step_number,
            total_steps=total_steps,
        )
        ctx = ProcessingContext(
            room_id=room_id,
            current_message=current_message,
            agent_card=agent_card,
            user_message_id=user_message_id,
            token=token,
            task_info=task_info,
            created_at=task_info.get("created_at") if task_info else None,
            step_number=step_number,
            total_steps=total_steps,
            send_sse=send_sse,
        )
        return task_info, ctx

    async def _poll_task_until_complete(
        self,
        agent_card: AgentCard,
        task_id: str,
        message_id: str,
        timeout_seconds: int = 120,
        initial_delay: float = 0.5,
        max_delay: float = 5.0,
        token: CancellationToken | None = None,
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
            token: CancellationToken for instant cancellation of the sleep (A-3).

        Returns:
            The completed Task if found, None if timeout, error, or cancelled
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

            # Check for cancellation between polls (Issue 11)
            if token and token.is_cancelled:
                logger.info(
                    "RoomMessageCenter: Polling cancelled for task %s after %d polls",
                    message_id,
                    poll_count,
                )
                return None

            # Wait before polling (except first iteration).
            # Use token.race() so cancellation instantly wakes the sleep (A-3).
            if poll_count > 0:
                try:
                    if token:
                        await token.race(asyncio.sleep(delay))
                    else:
                        await asyncio.sleep(delay)
                except CancellationError:
                    logger.info(
                        "RoomMessageCenter: Polling sleep interrupted by cancellation for task %s",
                        message_id,
                    )
                    return None
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
                state_value = _state_str(state)

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
        *,
        token: CancellationToken | None = None,
        send_sse: bool = False,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> tuple[ProcessingStatus, str]:
        """Handle streaming responses from an agent for a room message."""
        _task_info, ctx = await self._setup_tracking_context(
            current_message,
            agent_card,
            prepared_message,
            room_id,
            user_message_id,
            token=token,
            send_sse=send_sse,
            step_number=step_number,
            total_steps=total_steps,
        )
        streaming_state = MessageStreamingState()

        async for a2a_response in self.a2a_service.send_message_streaming(
            agent_card, prepared_message
        ):
            if token and token.is_cancelled:
                return await self._handle_streaming_cancellation(ctx, streaming_state)

            if isinstance(a2a_response.root, JSONRPCErrorResponse):
                return await self._handle_streaming_error(
                    a2a_response, ctx, streaming_state
                )

            result = a2a_response.root.result
            match result.kind:
                case "message":
                    await self._handle_stream_message_chunk(
                        result, ctx, streaming_state
                    )
                case "task":
                    self._handle_stream_task_event(result)
                case "status-update":
                    await self._handle_stream_status_update(
                        result, ctx, streaming_state
                    )
                case "artifact-update":
                    await self._handle_stream_artifact_update(result, ctx)

        return await self._finalize_streaming(ctx, streaming_state)

    # --- Streaming sub-handlers (Phase 2 decomposition) ---

    async def _handle_streaming_cancellation(
        self,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> tuple[ProcessingStatus, str]:
        """Handle cancellation during streaming."""
        logger.info(
            "RoomMessageCenter: Streaming cancelled for message %s", ctx.user_message_id
        )
        # Persist canceled status to DB and notify frontend in one step
        await self._transition_task(
            ctx.current_message, TaskState.canceled, ctx=ctx
        )
        await self.sse_manager.send_processing_status(
            ctx.room_id, SSEProcessingStatus.CANCELED, ctx.user_message_id
        )
        self.sse_manager.clear_cancellation(ctx.user_message_id)
        # Best-effort: tell the remote agent to stop (Issue 14).
        # Sent *after* the CANCELED SSE event so the frontend gets immediate
        # feedback — the remote cancel may take up to the timeout (5 s).
        await self._try_cancel_remote_task(ctx.current_message, ctx.agent_card)
        return ProcessingStatus.CANCELED, streaming_state.full_response_text

    async def _handle_streaming_error(
        self,
        a2a_response,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> tuple[ProcessingStatus, str]:
        """Handle JSON-RPC error during streaming."""
        error_message = a2a_response.root.error.model_dump_json()
        logger.error("RoomMessageCenter: Agent error: %s", error_message)
        # Persist + notify failure (fixes Issue 10: previously only notified)
        await self._transition_task(
            ctx.current_message, TaskState.failed, error=error_message, ctx=ctx
        )
        if ctx.send_sse:
            await self.sse_manager.send_error(ctx.room_id, error_message)
        return ProcessingStatus.FAILED, streaming_state.full_response_text

    async def _handle_stream_message_chunk(
        self,
        result,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> None:
        """Handle a 'message' event during streaming: accumulate parts, save to DB, send SSE tokens."""
        message_list = result.parts
        streaming_state.accumulated_parts.extend(message_list)

        if streaming_state.agent_message_id is None:
            streaming_state.agent_message_id = result.message_id

        content = "".join(
            part.root.text if part.root and hasattr(part.root, "text") else ""
            for part in message_list
        )
        streaming_state.full_response_text += content

        task = _get_task(ctx.current_message)
        if task:
            if task.history is None:
                task.history = []

            updated_message = Message(
                kind="message",
                role=result.role,
                message_id=streaming_state.agent_message_id,
                parts=streaming_state.accumulated_parts.copy(),
            )

            if not streaming_state.message_added_to_history:
                task.history.append(updated_message)
                streaming_state.message_added_to_history = True
            else:
                for i, msg in enumerate(task.history):
                    if (
                        hasattr(msg, "message_id")
                        and msg.message_id == streaming_state.agent_message_id
                    ):
                        task.history[i] = updated_message
                        break

            await self._persist_message(ctx.current_message)

        if ctx.send_sse:
            await self.sse_manager.send_agent_token(
                ctx.room_id,
                ctx.current_message.message_id,
                ctx.current_message.agent_id,
                content,
            )

    @staticmethod
    def _handle_stream_task_event(result) -> None:
        """Handle a 'task' event during streaming (log only)."""
        status = result.status
        logger.debug(
            "RoomMessageCenter: Task event: %s", status.state if status else "no status"
        )

    async def _handle_stream_status_update(
        self,
        result,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> None:
        """Handle a 'status-update' event during streaming."""
        state = result.status.state
        logger.info(
            "RoomMessageCenter: Status update for message %s: %s",
            ctx.current_message.message_id,
            state,
        )

        a2a_status_message_text: str | None = None
        if result.status.message:
            a2a_status_message_text = get_text_from_message(result.status.message)

        task = _get_task(ctx.current_message)
        if task:
            if task.status is None:
                task.status = TaskStatus(state=TaskState.submitted)
            task.status.state = state
            await self._persist_message(ctx.current_message)

        if state in TERMINAL_STATES:
            logger.info(
                "RoomMessageCenter: Final status for message %s: %s",
                ctx.current_message.message_id,
                state,
            )
            fetched_task = await self.task_service.get_task_from_agent(
                ctx.agent_card, result.task_id
            )
            if fetched_task is not None:
                message = get_message_from_task(fetched_task)
                await self._handle_a2a_response_for_room(ctx.current_message, message)
                fetched_text = get_text_from_a2a_response(message)
                if fetched_text:
                    if (
                        streaming_state.full_response_text
                        and fetched_text != streaming_state.full_response_text
                    ):
                        logger.warning(
                            "RoomMessageCenter: Fetched final text differs from streaming text for %s",
                            ctx.current_message.message_id,
                        )
                    streaming_state.full_response_text = fetched_text
                else:
                    logger.warning(
                        "RoomMessageCenter: Fetched task returned empty text for %s, keeping streaming text",
                        ctx.current_message.message_id,
                    )
            else:
                logger.error(
                    "RoomMessageCenter: Failed to retrieve final task for task id %s",
                    result.task_id,
                )

        if ctx.send_sse:
            if a2a_status_message_text and state not in TERMINAL_STATES:
                await self._notify_task(
                    ctx,
                    state,
                    status_message=a2a_status_message_text,
                )

    async def _handle_stream_artifact_update(
        self,
        result,
        ctx: ProcessingContext,
    ) -> None:
        """Handle an 'artifact-update' event during streaming."""
        artifact_result = getattr(result, "artifact", None)
        append = getattr(result, "append", False)
        last_chunk = getattr(result, "last_chunk", False)

        task = _get_task(ctx.current_message)
        if not artifact_result or not task:
            return

        if task.artifacts is None:
            task.artifacts = []

        artifact_id = getattr(artifact_result, "artifact_id", None)
        if append and artifact_id:
            existing = next(
                (a for a in task.artifacts if a.artifact_id == artifact_id), None
            )
            if existing:
                artifact_parts = getattr(artifact_result, "parts", None)
                if artifact_parts:
                    existing.parts.extend(artifact_parts)
            else:
                task.artifacts.append(artifact_result)
        else:
            task.artifacts.append(artifact_result)

        await self._persist_message(ctx.current_message)

        if ctx.send_sse:
            await self.sse_manager.send_artifact_update(
                ctx.room_id,
                ctx.current_message.message_id,
                ctx.current_message.agent_id,
                artifact_result,
                append=append,
                last_chunk=last_chunk,
            )

    async def _finalize_streaming(
        self,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> tuple[ProcessingStatus, str]:
        """Finalize streaming: persist final state, send task_update SSE."""
        logger.info(
            "RoomMessageCenter: Streaming complete for message %s, text length: %d",
            ctx.current_message.message_id,
            len(streaming_state.full_response_text),
        )

        task = _get_task(ctx.current_message)
        already_terminal = task and task.status and is_terminal_state(task.status.state)

        if task and not already_terminal:
            # Set message_text before _transition_task persists
            if streaming_state.full_response_text:
                ctx.current_message.message_content.message_text = (
                    streaming_state.full_response_text
                )
            await self._transition_task(
                ctx.current_message,
                TaskState.completed,
                ctx=ctx,
                content=streaming_state.full_response_text,
            )

        if already_terminal:
            final_state = task.status.state
            final_state_value = _state_str(final_state)
            final_error = None
            if is_failure_state(final_state):
                if task.status.message and task.status.message.parts:
                    for part in task.status.message.parts:
                        part_root = getattr(part, "root", part)
                        if hasattr(part_root, "text"):
                            final_error = part_root.text
                            break
                if not final_error:
                    final_error = f"Task {final_state_value}"

            await self._notify_task(
                ctx,
                final_state_value,
                content=streaming_state.full_response_text
                if final_state == TaskState.completed
                else None,
                error=final_error,
            )

            if is_failure_state(final_state):
                return ProcessingStatus.FAILED, streaming_state.full_response_text
        else:
            await self._notify_task(
                ctx,
                TaskState.completed,
                content=streaming_state.full_response_text,
            )

        return ProcessingStatus.SUCCESS, streaming_state.full_response_text

    async def _handle_a2a_response_for_room(
        self, room_agent_message: RoomAgentMessage, message_data: None | Task | Message
    ) -> bool:
        if message_data is None:
            logger.error("RoomMessageCenter: process_a2a_response returned None")
            return False

        if message_data.kind == "task":
            room_agent_message.message_content.message_task = message_data
            return await self._persist_message(room_agent_message)

        if message_data.kind == "message":
            task = _get_task(room_agent_message)
            if task:
                if task.history is None:
                    task.history = []
                task.history.append(message_data)
            return await self._persist_message(room_agent_message)

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
            await self._cancel_remaining_queue(message_queue)
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
                    await self._transition_task(
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
                        await self._fail_task_and_notify(
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
                        await self._fail_task_and_notify(
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
                            await self._fail_task_and_notify(
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
                        await self._transition_task(
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
            task = _get_task(current_message)
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

        Args:
            current_message: The agent message to process
            room_id: The room ID
            agent: The agent to process the message
            user_message_id: User message ID for cancellation checks
            token: CancellationToken for cooperative cancellation (A-3)
            step_number: Current step number in the workflow (1-indexed)
            total_steps: Total number of steps in the workflow
            quoted_text: Text the user highlighted and quoted from a previous message

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
                await self._fail_task_and_notify(
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
            ) = await self._handle_sync_response_for_room(
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
                # Distinguish cancellation from failure: if the cancellation
                # flag is (still) set or the task was already transitioned to
                # canceled by the sync handler, report CANCELED so the queue
                # handler skips sending a second FAILED status.
                task = _get_task(current_message)
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
            state_value = _state_str(state)
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
        *,
        user_message_id: str | None = None,
        token: CancellationToken | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """Handle synchronous (non-streaming) response from an agent.

        Returns:
            Tuple of (success, response_text, paused_message_id).
        """
        task_info, ctx = await self._setup_tracking_context(
            current_message,
            agent_card,
            prepared_message,
            room_id,
            current_message.message_id,
            token=token,
            step_number=step_number,
            total_steps=total_steps,
        )
        if not task_info:
            logger.warning(
                "RoomMessageCenter: task tracking setup failed for message %s — degraded mode",
                current_message.message_id,
            )
            # Degraded mode: still send task_submitted so the frontend can render
            # the agent message placeholder immediately.
            await self.sse_manager.send_task_submitted(
                room_id=room_id,
                message_id=current_message.message_id,
                task_id=SyntheticTaskId.DEGRADED,
                agent_name=agent_card.name,
                agent_id=current_message.agent_id,
                status=TaskState.working,
                step_number=step_number,
                total_steps=total_steps,
            )

        message_id = current_message.message_id

        # Check for cancellation before the (potentially long) sync agent call
        if token and token.is_cancelled:
            logger.info(
                "RoomMessageCenter: Sync call cancelled before agent call for %s",
                message_id,
            )
            await self._transition_task(
                current_message, TaskState.canceled, ctx=ctx if task_info else None
            )
            return False, "", None

        # Call the agent — wrap with token.race() so a cancellation during the
        # blocking HTTP call interrupts immediately (A-3, eliminates Issue 18/19).
        try:
            if task_info:
                agent_coro = self.a2a_service.send_message_to_tracked_agent(
                    agent_card=agent_card,
                    message=prepared_message,
                    message_id=message_id,
                    webhook_token=task_info["webhook_token"],
                    context_id=task_info["context_id"],
                )
            else:
                async def _sync_fallback():
                    raw = await self.a2a_service.send_message_sync(
                        agent_card=agent_card,
                        message=prepared_message,
                    )
                    return self._parse_sync_fallback_response(raw, message_id)

                agent_coro = _sync_fallback()

            if token:
                response = await token.race(agent_coro)
            else:
                response = await agent_coro
        except CancellationError:
            logger.info(
                "RoomMessageCenter: Sync call cancelled during agent call for %s",
                message_id,
            )
            await self._transition_task(
                current_message, TaskState.canceled, ctx=ctx if task_info else None
            )
            # Best-effort: tell the remote agent to stop
            await self._try_cancel_remote_task(current_message, agent_card)
            return False, "", None
        except Exception as exc:
            logger.error("Agent error: %s", exc, exc_info=True)
            # Persist failure + notify (previously only notified — same class
            # as Issue 10)
            await self._transition_task(
                current_message, TaskState.failed, error=str(exc),
                ctx=ctx if task_info else None,
            )
            await self.sse_manager.send_error(room_id, str(exc))
            return False, "", None

        # Post-call cancellation check: if the token was signalled just
        # after the HTTP call completed (race window narrower than before,
        # but still possible), honour the cancellation.
        if token and token.is_cancelled:
            logger.info(
                "RoomMessageCenter: Sync call cancelled after agent response for %s",
                message_id,
            )
            await self._transition_task(
                current_message, TaskState.canceled, ctx=ctx if task_info else None
            )
            # Best-effort: tell the remote agent to stop
            await self._try_cancel_remote_task(current_message, agent_card)
            return False, "", None

        # Handle "message" response (fast path)
        if response.get("type") == "message":
            full_response_text = response.get("content") or ""
            # Set message_text before _transition_task persists
            if full_response_text:
                current_message.message_content.message_text = full_response_text
            await self._transition_task(
                current_message,
                TaskState.completed,
                ctx=ctx if task_info else None,
                content=full_response_text,
            )

            if not task_info:
                # Degraded mode: still notify the frontend about the agent response
                # so the UI updates in real-time without requiring a page refresh.
                logger.info(
                    "RoomMessageCenter: Degraded mode — sending task_update directly for %s",
                    message_id,
                )
                await self.sse_manager.send_task_update(
                    room_id=room_id,
                    message_id=message_id,
                    status=TaskState.completed,
                    content=full_response_text,
                    agent_name=agent_card.name if agent_card else None,
                    agent_id=current_message.agent_id,
                    step_number=step_number,
                    total_steps=total_steps,
                )
            return True, full_response_text, None

        # Handle "task" response (async path)
        if response.get("type") == "task":
            status = response.get("status") or TaskState.working
            if task_info:
                await self._notify_task(
                    ctx,
                    status,
                    requires_input=response.get("requires_input", False),
                    requires_auth=response.get("requires_auth", False),
                    status_message=response.get("message"),
                )

            if self.a2a_service.has_push_notification_capability(agent_card):
                return True, None, message_id

            # Non-push agent: poll for completion
            agent_task_id = response.get("task_id")
            if not agent_task_id:
                logger.warning(
                    "RoomMessageCenter: Non-push agent task response without task_id"
                )
                return True, None, None

            logger.info(
                "RoomMessageCenter: Polling non-push agent task %s", agent_task_id
            )
            completed_task = await self._poll_task_until_complete(
                agent_card=agent_card,
                task_id=agent_task_id,
                message_id=message_id,
                timeout_seconds=120,
                token=token,
            )

            # If poll returned None and cancellation is pending, handle it
            # (the poll loop exits early on cancellation but doesn't send SSE —
            # the queue-level handler takes care of that)
            if completed_task is None and (token and token.is_cancelled):
                logger.info(
                    "RoomMessageCenter: Poll cancelled for task %s, transitioning to canceled",
                    message_id,
                )
                await self._transition_task(
                    current_message, TaskState.canceled, ctx=ctx if task_info else None
                )
                # Best-effort: tell the remote agent to stop
                await self._try_cancel_remote_task(current_message, agent_card)
                return False, "", None

            if completed_task:
                state = completed_task.status.state
                state_value = _state_str(state)

                if task_info:
                    await self.database_service.update_task_on_message(
                        message_id, completed_task.model_dump(mode="json")
                    )

                final_content = None
                final_error = None
                if state == TaskState.completed and completed_task.artifacts:
                    final_content = extract_text_from_artifacts(
                        completed_task.artifacts
                    )
                elif is_failure_state(state):
                    final_error = (
                        extract_error_message(completed_task) or f"Task {state_value}"
                    )

                if task_info:
                    await self._notify_task(
                        ctx,
                        state,
                        content=final_content,
                        error=final_error,
                    )
                else:
                    # Degraded mode: still push the result via SSE
                    logger.info(
                        "RoomMessageCenter: Degraded mode — sending polled task_update for %s",
                        message_id,
                    )
                    await self.sse_manager.send_task_update(
                        room_id=room_id,
                        message_id=message_id,
                        status=state,
                        content=final_content,
                        error=final_error,
                        agent_name=agent_card.name if agent_card else None,
                        agent_id=current_message.agent_id,
                        step_number=step_number,
                        total_steps=total_steps,
                    )
                return True, final_content, None
            else:
                logger.warning(
                    "RoomMessageCenter: Polling timed out for task %s",
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
