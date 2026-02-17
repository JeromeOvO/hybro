"""Streaming and synchronous response processing from A2A agents.

Handles chunk accumulation, artifact collection, stream finalization,
sync response handling with polling, and cancellation/error handling.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from a2a.types import (
    AgentCard,
    GetTaskRequest,
    JSONRPCErrorResponse,
    Message,
    Part,
    Task,
    TaskQueryParams,
    TaskState,
    TaskStatus,
)

from common.utils.a2a_helpers import (
    extract_error_message,
    extract_text_from_artifacts,
    get_message_from_task,
    get_text_from_a2a_response,
    get_text_from_message,
)
from common.utils.cancellation import CancellationError, CancellationToken
from common.utils.logger import get_logger
from models.processing import ProcessingContext
from models.room import RoomAgentMessage
from modules.TaskStateManager import (
    TaskStateManager,
    get_task,
    state_str,
)
from services.a2a_constants import (
    TERMINAL_STATES,
    SyntheticTaskId,
    is_failure_state,
    is_terminal_state,
)

logger = get_logger(__name__)


class ProcessingStatus(Enum):
    """Status of message processing operations."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"  # Queue paused waiting for push notification task


@dataclass
class MessageStreamingState:
    """Tracks mutable streaming state across sub-handlers during a single streaming session."""

    full_response_text: str = ""
    accumulated_parts: list[Part] = field(default_factory=list)
    agent_message_id: str | None = None
    message_added_to_history: bool = False


class ResponseProcessor:
    """Handles streaming and sync response processing from A2A agents.

    Responsible for:
    - Streaming chunk handling and accumulation
    - Artifact accumulation
    - Stream finalization (persist + notify)
    - Sync response handling and polling
    - Cancellation/error handling during responses
    """

    def __init__(
        self,
        tsm: TaskStateManager,
        sse_manager,
        a2a_service,
        task_service,
        database_service,
    ):
        self.tsm = tsm
        self.sse_manager = sse_manager
        self.a2a_service = a2a_service
        self.task_service = task_service
        self.database_service = database_service

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _try_cancel_remote_task(
        self,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
    ) -> None:
        """Best-effort, fire-and-forget cancellation of the remote A2A agent task."""
        task = get_task(current_message)
        if not task:
            return
        remote_task_id = task.id if task.id else None
        if not remote_task_id or remote_task_id.startswith("pending-"):
            logger.debug(
                "ResponseProcessor: No remote task ID to cancel for message %s",
                current_message.message_id,
            )
            return

        logger.info(
            "ResponseProcessor: Attempting to cancel remote task %s on agent %s",
            remote_task_id,
            agent_card.name,
        )
        await self.a2a_service.cancel_remote_task(agent_card, remote_task_id)

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
                "ResponseProcessor: Setting up task tracking for message %s (step %s/%s, agent: %s)",
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

            task_content = current_message.task_content

            logger.info(
                "ResponseProcessor: Sending task_submitted SSE for step %s/%s, task_content: %s",
                step_number,
                total_steps,
                task_content[:50] if task_content else "None",
            )

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
        """Set up task tracking and build a ProcessingContext in one step."""
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

    # ------------------------------------------------------------------
    # Polling (sync path)
    # ------------------------------------------------------------------

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
        """Poll an agent for task completion with exponential backoff.

        Returns:
            The completed Task if found, None if timeout, error, or cancelled
        """
        start_time = asyncio.get_event_loop().time()
        delay = initial_delay
        poll_count = 0

        logger.info(
            "ResponseProcessor: Starting poll for task %s (agent task: %s), timeout: %ds",
            message_id,
            task_id,
            timeout_seconds,
        )

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_seconds:
                logger.warning(
                    "ResponseProcessor: Poll timeout for task %s after %.1fs (%d polls)",
                    message_id,
                    elapsed,
                    poll_count,
                )
                return None

            if token and token.is_cancelled:
                logger.info(
                    "ResponseProcessor: Polling cancelled for task %s after %d polls",
                    message_id,
                    poll_count,
                )
                return None

            if poll_count > 0:
                try:
                    if token:
                        await token.race(asyncio.sleep(delay))
                    else:
                        await asyncio.sleep(delay)
                except CancellationError:
                    logger.info(
                        "ResponseProcessor: Polling sleep interrupted by cancellation for task %s",
                        message_id,
                    )
                    return None
                delay = min(delay * 1.5, max_delay)

            poll_count += 1

            try:
                a2a_client = await self.a2a_service.create_a2a_client(agent_card)
                response = await a2a_client.get_task(
                    GetTaskRequest(id=task_id, params=TaskQueryParams(id=task_id))
                )

                if not response or isinstance(response.root, JSONRPCErrorResponse):
                    logger.warning(
                        "ResponseProcessor: Poll %d for task %s returned error/empty",
                        poll_count,
                        message_id,
                    )
                    continue

                task = response.root.result
                if task is None:
                    logger.warning(
                        "ResponseProcessor: Poll %d for task %s returned no result",
                        poll_count,
                        message_id,
                    )
                    continue

                state = task.status.state
                state_value = state_str(state)

                if is_terminal_state(state):
                    logger.info(
                        "ResponseProcessor: Task %s completed with state %s after %.1fs (%d polls)",
                        message_id,
                        state_value,
                        asyncio.get_event_loop().time() - start_time,
                        poll_count,
                    )
                    return task

                logger.debug(
                    "ResponseProcessor: Poll %d for task %s: state=%s",
                    poll_count,
                    message_id,
                    state_value,
                )

            except Exception as e:
                logger.warning(
                    "ResponseProcessor: Poll %d for task %s failed: %s",
                    poll_count,
                    message_id,
                    e,
                )
                continue

    # ------------------------------------------------------------------
    # Streaming path
    # ------------------------------------------------------------------

    async def handle_streaming_response(
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

    async def _handle_streaming_cancellation(
        self,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> tuple[ProcessingStatus, str]:
        """Handle cancellation during streaming — per-message cleanup only.

        Transitions the *current* message to ``canceled`` and attempts to
        cancel the remote A2A task.  Does **not** send the workflow-level
        ``send_processing_status(CANCELED)`` — that is the responsibility of
        ``QueueExecutor.process_queue`` (Phase 2), which fires *after*
        ``_managed_queue`` has persisted all remaining siblings.
        """
        logger.info(
            "ResponseProcessor: Streaming cancelled for message %s", ctx.user_message_id
        )
        await self.tsm.transition_task(ctx.current_message, TaskState.canceled, ctx=ctx)
        # NOTE: Do NOT send processing_status here — QueueExecutor handles
        # workflow-level SSE after all siblings are persisted.
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
        logger.error("ResponseProcessor: Agent error: %s", error_message)
        await self.tsm.transition_task(
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
        """Handle a 'message' event during streaming."""
        message_list = result.parts
        streaming_state.accumulated_parts.extend(message_list)

        if streaming_state.agent_message_id is None:
            streaming_state.agent_message_id = result.message_id

        content = "".join(
            part.root.text if part.root and hasattr(part.root, "text") else ""
            for part in message_list
        )
        streaming_state.full_response_text += content

        task = get_task(ctx.current_message)
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

            await self.tsm.persist_message(ctx.current_message)

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
            "ResponseProcessor: Task event: %s", status.state if status else "no status"
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
            "ResponseProcessor: Status update for message %s: %s",
            ctx.current_message.message_id,
            state,
        )

        a2a_status_message_text: str | None = None
        if result.status.message:
            a2a_status_message_text = get_text_from_message(result.status.message)

        task = get_task(ctx.current_message)
        if task:
            if is_terminal_state(state):
                await self.tsm.transition_task(
                    ctx.current_message, state, persist=True, notify=False
                )
            else:
                task.status = TaskStatus(state=state)
                await self.tsm.persist_message(ctx.current_message)

        if state in TERMINAL_STATES:
            logger.info(
                "ResponseProcessor: Final status for message %s: %s",
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
                            "ResponseProcessor: Fetched final text differs from streaming text for %s",
                            ctx.current_message.message_id,
                        )
                    streaming_state.full_response_text = fetched_text
                else:
                    logger.warning(
                        "ResponseProcessor: Fetched task returned empty text for %s, keeping streaming text",
                        ctx.current_message.message_id,
                    )
            else:
                logger.error(
                    "ResponseProcessor: Failed to retrieve final task for task id %s",
                    result.task_id,
                )

        if ctx.send_sse:
            if a2a_status_message_text and state not in TERMINAL_STATES:
                await self.tsm.notify_task(
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

        task = get_task(ctx.current_message)
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

        await self.tsm.persist_message(ctx.current_message)

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
            "ResponseProcessor: Streaming complete for message %s, text length: %d",
            ctx.current_message.message_id,
            len(streaming_state.full_response_text),
        )

        task = get_task(ctx.current_message)
        already_terminal = task and task.status and is_terminal_state(task.status.state)

        if task and not already_terminal:
            if streaming_state.full_response_text:
                ctx.current_message.message_content.message_text = (
                    streaming_state.full_response_text
                )
            await self.tsm.transition_task(
                ctx.current_message,
                TaskState.completed,
                ctx=ctx,
                content=streaming_state.full_response_text,
            )

        if already_terminal:
            final_state = task.status.state
            final_state_value = state_str(final_state)
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

            await self.tsm.notify_task(
                ctx,
                final_state_value,
                content=streaming_state.full_response_text
                if final_state == TaskState.completed
                else None,
                error=final_error,
            )

            if is_failure_state(final_state):
                return ProcessingStatus.FAILED, streaming_state.full_response_text

        return ProcessingStatus.SUCCESS, streaming_state.full_response_text

    async def _handle_a2a_response_for_room(
        self, room_agent_message: RoomAgentMessage, message_data: None | Task | Message
    ) -> bool:
        if message_data is None:
            logger.error("ResponseProcessor: process_a2a_response returned None")
            return False

        if message_data.kind == "task":
            room_agent_message.message_content.message_task = message_data
            return await self.tsm.persist_message(room_agent_message)

        if message_data.kind == "message":
            task = get_task(room_agent_message)
            if task:
                if task.history is None:
                    task.history = []
                task.history.append(message_data)
            return await self.tsm.persist_message(room_agent_message)

        logger.error(
            "ResponseProcessor: Unexpected data kind in A2A response: %s",
            message_data.kind,
        )
        return False

    # ------------------------------------------------------------------
    # Sync path
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sync_fallback_response(
        raw_response,
        message_id: str,
    ) -> dict[str, Any]:
        """Parse a raw ``send_message_sync`` response into the dict format
        expected by ``handle_sync_response``."""
        if raw_response is None:
            return {"type": "message", "message_id": message_id, "content": ""}

        if isinstance(raw_response.root, JSONRPCErrorResponse):
            from services.a2a_service import A2AServiceError

            raise A2AServiceError(str(raw_response.root.error.message))

        result = raw_response.root.result

        if result.kind == "message":
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
            state_value = state_str(state)
            return {
                "type": "task",
                "message_id": message_id,
                "task_id": result.id,
                "status": state_value,
            }

        return {"type": "message", "message_id": message_id, "content": ""}

    async def handle_sync_response(
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
                "ResponseProcessor: task tracking setup failed for message %s — degraded mode",
                current_message.message_id,
            )
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
                "ResponseProcessor: Sync call cancelled before agent call for %s",
                message_id,
            )
            await self.tsm.transition_task(
                current_message, TaskState.canceled, ctx=ctx if task_info else None
            )
            return False, "", None

        # Call the agent
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
                "ResponseProcessor: Sync call cancelled during agent call for %s",
                message_id,
            )
            await self.tsm.transition_task(
                current_message, TaskState.canceled, ctx=ctx if task_info else None
            )
            await self._try_cancel_remote_task(current_message, agent_card)
            return False, "", None
        except Exception as exc:
            logger.error("Agent error: %s", exc, exc_info=True)
            await self.tsm.transition_task(
                current_message,
                TaskState.failed,
                error=str(exc),
                ctx=ctx if task_info else None,
            )
            await self.sse_manager.send_error(room_id, str(exc))
            return False, "", None

        # Post-call cancellation check
        if token and token.is_cancelled:
            logger.info(
                "ResponseProcessor: Sync call cancelled after agent response for %s",
                message_id,
            )
            await self.tsm.transition_task(
                current_message, TaskState.canceled, ctx=ctx if task_info else None
            )
            await self._try_cancel_remote_task(current_message, agent_card)
            return False, "", None

        return await self._process_sync_response(
            response,
            current_message,
            agent_card,
            room_id,
            message_id,
            task_info,
            ctx,
            token,
            step_number=step_number,
            total_steps=total_steps,
        )

    async def _process_sync_response(
        self,
        response: dict[str, Any],
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        room_id: str,
        message_id: str,
        task_info: dict[str, Any] | None,
        ctx: ProcessingContext,
        token: CancellationToken | None,
        *,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """Process the parsed sync response (message or task type)."""
        # Handle "message" response (fast path)
        if response.get("type") == "message":
            full_response_text = response.get("content") or ""
            if full_response_text:
                current_message.message_content.message_text = full_response_text
            await self.tsm.transition_task(
                current_message,
                TaskState.completed,
                ctx=ctx if task_info else None,
                content=full_response_text,
            )

            if not task_info:
                logger.info(
                    "ResponseProcessor: Degraded mode — sending task_update directly for %s",
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
                await self.tsm.notify_task(
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
                    "ResponseProcessor: Non-push agent task response without task_id"
                )
                return True, None, None

            logger.info(
                "ResponseProcessor: Polling non-push agent task %s", agent_task_id
            )
            completed_task = await self._poll_task_until_complete(
                agent_card=agent_card,
                task_id=agent_task_id,
                message_id=message_id,
                timeout_seconds=120,
                token=token,
            )

            if completed_task is None and (token and token.is_cancelled):
                logger.info(
                    "ResponseProcessor: Poll cancelled for task %s, transitioning to canceled",
                    message_id,
                )
                await self.tsm.transition_task(
                    current_message, TaskState.canceled, ctx=ctx if task_info else None
                )
                await self._try_cancel_remote_task(current_message, agent_card)
                return False, "", None

            if completed_task:
                return await self._finalize_polled_task(
                    completed_task,
                    current_message,
                    agent_card,
                    room_id,
                    message_id,
                    task_info,
                    ctx,
                    step_number=step_number,
                    total_steps=total_steps,
                )
            else:
                logger.warning(
                    "ResponseProcessor: Polling timed out for task %s",
                    message_id,
                )
                return True, None, None

        logger.error("Unexpected response type from task tracking: %s", response)
        return False, "", None

    async def _finalize_polled_task(
        self,
        completed_task: Task,
        current_message: RoomAgentMessage,
        agent_card: AgentCard,
        room_id: str,
        message_id: str,
        task_info: dict[str, Any] | None,
        ctx: ProcessingContext,
        *,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> tuple[bool, str | None, str | None]:
        """Finalize a polled task that reached a terminal state."""
        state = completed_task.status.state
        state_value = state_str(state)

        if task_info:
            await self.database_service.update_task_on_message(
                message_id, completed_task.model_dump(mode="json")
            )

        final_content = None
        final_error = None
        if state == TaskState.completed and completed_task.artifacts:
            final_content = extract_text_from_artifacts(completed_task.artifacts)
        elif is_failure_state(state):
            final_error = extract_error_message(completed_task) or f"Task {state_value}"

        if task_info:
            await self.tsm.notify_task(
                ctx,
                state,
                content=final_content,
                error=final_error,
            )
        else:
            logger.info(
                "ResponseProcessor: Degraded mode — sending polled task_update for %s",
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
