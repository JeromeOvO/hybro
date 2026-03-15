"""DirectTransport — cloud SSE/sync transport to cloud-reachable A2A agents.

Absorbs all response-processing logic (formerly in ``ResponseProcessor``)
and replaces terminal ``notify_task_update`` calls with ``AgentEvent``
emissions through ``AgentResponseHandler``.

Mid-stream SSE (``send_agent_token`` during streaming) stays inside
``DirectTransport`` via its own ``sse_manager`` reference — this is an
accepted asymmetry (see design doc §2.8).
"""

import asyncio
from dataclasses import dataclass, field
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
from models.processing import ProcessingContext, ProcessingResult, ProcessingStatus
from models.room import RoomAgentMessage
from modules.agent_event import AgentEvent
from modules.dispatch_middleware import DispatchContext
from modules.TaskStateManager import (
    TaskStateManager,
    get_task,
    state_str,
)
from modules.transports.base import AgentTransport
from services.a2a_constants import (
    INTERACTIVE_STATES,
    TERMINAL_STATES,
    SyntheticTaskId,
    is_failure_state,
    is_terminal_state,
)

logger = get_logger(__name__)


@dataclass
class MessageStreamingState:
    """Tracks mutable streaming state across sub-handlers during a single streaming session."""

    full_response_text: str = ""
    accumulated_parts: list[Part] = field(default_factory=list)
    non_text_parts: list[dict] = field(default_factory=list)
    inline_conversion_count: int = 0
    agent_message_id: str | None = None
    message_added_to_history: bool = False
    stream_finalized: bool = False
    final_state: TaskState | None = None


class DirectTransport(AgentTransport):
    """Direct HTTP/SSE transport to cloud-reachable A2A agents.

    Contains all streaming and sync response processing logic (formerly
    in ``ResponseProcessor``).  Terminal notifications go through
    ``AgentResponseHandler`` via ``_emit_terminal``.
    """

    def __init__(
        self,
        response_handler,
        tsm: TaskStateManager,
        a2a_service,
        task_service,
        sse_manager,
        database_service,
    ) -> None:
        super().__init__(response_handler)
        self.tsm = tsm
        self.sse_manager = sse_manager
        self.a2a_service = a2a_service
        self.task_service = task_service
        self.database_service = database_service
        self._s3_service = None

    @property
    def s3_service(self):
        if self._s3_service is None:
            from services.s3_service import s3_service

            self._s3_service = s3_service
        return self._s3_service

    # ------------------------------------------------------------------
    # Terminal event emission
    # ------------------------------------------------------------------

    async def _emit_terminal(
        self,
        ctx: ProcessingContext,
        state: TaskState,
        *,
        error: str | None = None,
        parts: list[dict] | None = None,
    ) -> None:
        """Emit a terminal AgentEvent through the shared response handler.

        skip_persist=True because tsm.transition_task / tsm.persist_message
        already wrote the full document to DB.
        """
        msg = ctx.current_message
        kind: str
        if state == TaskState.canceled:
            kind = "canceled"
        elif is_failure_state(state):
            kind = "error"
        elif state in INTERACTIVE_STATES:
            kind = "interactive"
        else:
            kind = "response"

        await self.response_handler.handle(AgentEvent(
            kind=kind,
            message_id=msg.message_id,
            room_id=ctx.room_id,
            agent_id=msg.agent_id or "",
            text=(error or (msg.message_content.message_text if hasattr(msg, 'message_content') and msg.message_content else "")),
            error_text=error if kind == "error" else None,
            state=state.value if hasattr(state, 'value') else str(state),
            related_message_id=msg.related_message_id,
            user_id=msg.user_id or "",
            parts=parts,
            skip_persist=True,
        ))

    # ------------------------------------------------------------------
    # dispatch — unified entry point for AgentMessageProcessor router
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        ctx: DispatchContext,
        message: RoomAgentMessage,
    ) -> ProcessingResult:
        """Execute direct (cloud) dispatch: streaming or sync based on agent capabilities.

        Extracts all needed params from ``ctx`` (DispatchContext) and the
        ``message`` (RoomAgentMessage).  Returns a ``ProcessingResult``.
        """
        agent = ctx.agent
        room_id = ctx.room_id
        user_message_id = ctx.user_message_id
        prepared_message = ctx.prepared_message
        token = ctx.token
        step_number = ctx.step_number
        total_steps = ctx.total_steps

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
                ) = await self.handle_streaming_response(
                    message,
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
                    "DirectTransport.dispatch: Unhandled exception in streaming for message %s: %s",
                    message.message_id,
                    exc,
                    exc_info=True,
                )
                await self.tsm.transition_task(
                    message, TaskState.failed,
                    error=f"Agent streaming failed: {exc}",
                    persist=True,
                )
                fallback_ctx = ProcessingContext(
                    room_id=room_id,
                    current_message=message,
                    agent_card=agent.agent_card,
                    user_message_id=user_message_id,
                )
                await self._emit_terminal(
                    fallback_ctx, TaskState.failed,
                    error=f"Agent streaming failed: {exc}",
                )

                # Record capability issue for the agent
                try:
                    from services.agent_capability_issue_service import (
                        capability_issue_service,
                    )

                    await capability_issue_service.record_issue(
                        agent_id=message.agent_id,
                        error_message=f"Agent streaming failed: {exc}",
                        query_text=(
                            message.task_content
                            or (message.message_content.message_text or "")
                        ),
                        room_id=room_id,
                        message_id=message.message_id,
                    )
                except Exception as rec_exc:  # noqa: BLE001
                    logger.warning(
                        "DirectTransport: Failed to record capability issue: %s",
                        rec_exc,
                    )

                return ProcessingResult(ProcessingStatus.FAILED, "")
            if status != ProcessingStatus.SUCCESS:
                return ProcessingResult(status, full_response_text)
        else:
            (
                success,
                full_response_text,
                paused_message_id,
            ) = await self.handle_sync_response(
                message,
                agent.agent_card,
                prepared_message,
                room_id,
                message.user_id,
                user_message_id=user_message_id,
                token=token,
                step_number=step_number,
                total_steps=total_steps,
            )
            if not success:
                task = get_task(message)
                was_canceled = (
                    (token and token.is_cancelled)
                    or (task and task.status and task.status.state == TaskState.canceled)
                )
                if was_canceled:
                    return ProcessingResult(ProcessingStatus.CANCELED)
                return ProcessingResult(ProcessingStatus.FAILED)

        if full_response_text is None and paused_message_id:
            task = get_task(message)
            if task and task.status and task.status.state == TaskState.input_required:
                logger.info(
                    "DirectTransport.dispatch: Agent returned input_required for message %s",
                    paused_message_id,
                )
                task_data = task.model_dump(mode="json") if hasattr(task, "model_dump") else {}
                status_msg = None
                if task.status and task.status.message:
                    parts = task.status.message.parts or []
                    for p in parts:
                        if hasattr(p, "root") and hasattr(p.root, "text"):
                            status_msg = p.root.text
                            break
                        if hasattr(p, "text"):
                            status_msg = p.text
                            break
                return ProcessingResult(
                    ProcessingStatus.AWAITING_INPUT,
                    response_text="",
                    message_id=paused_message_id,
                    a2a_task_id=task_data.get("id") or (task.id if hasattr(task, "id") else None),
                    a2a_context_id=task_data.get("context_id") or (task.context_id if hasattr(task, "context_id") else None),
                    status_message=status_msg,
                )

            logger.info(
                "DirectTransport.dispatch: Push notification task submitted for message %s; "
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
                "DirectTransport.dispatch: Async task submitted for message %s; "
                "skipping immediate agent response",
                message.message_id,
            )
            return ProcessingResult(ProcessingStatus.SUCCESS)

        message = (
            await self.database_service.get_room_agent_message_by_message_id(
                message.message_id
            )
        )

        if message is None:
            return ProcessingResult(ProcessingStatus.FAILED, full_response_text)

        return ProcessingResult(ProcessingStatus.SUCCESS, full_response_text)

    async def _convert_inline_bytes_to_s3(
        self, artifact, room_id: str, message_id: str,
        conversion_counter: list[int] | None = None,
    ) -> None:
        """Convert inline base64 bytes and external URIs in artifact parts to S3 URIs.

        Delegates to the shared helper in a2a_helpers. ``conversion_counter``
        is a single-element list ``[count]`` shared across calls for the same
        message so the per-message cap is enforced.
        """
        from common.utils.a2a_helpers import convert_pydantic_artifacts_to_s3

        if not artifact.parts:
            return

        if conversion_counter is None:
            conversion_counter = [0]

        new_total = await convert_pydantic_artifacts_to_s3(
            [artifact], room_id, message_id,
            converted_so_far=conversion_counter[0],
        )
        conversion_counter[0] = new_total

    async def _convert_streaming_parts_to_s3(
        self,
        non_text_parts: list[dict],
        room_id: str,
        message_id: str,
        *,
        converted_so_far: int = 0,
    ) -> int:
        """Convert inline base64 bytes in accumulated streaming file parts to S3 URIs.

        Delegates to the shared helper in a2a_helpers.  Returns the updated
        running total so callers can keep the per-message cap accurate.
        """
        from common.utils.a2a_helpers import convert_inline_bytes_to_s3

        return await convert_inline_bytes_to_s3(
            non_text_parts, room_id, message_id,
            converted_so_far=converted_so_far,
        )

    @staticmethod
    def _materialize_non_text_parts_as_artifact(
        task, non_text_parts: list[dict]
    ) -> None:
        """Wrap accumulated non-text streaming parts into an A2A artifact.

        This ensures the multimodal data is persisted in the DB alongside
        any artifacts produced by ``artifact_update`` events, so that
        ``notify_task_update`` / hydration can recover them after reconnect.

        Uses ``a2a.types.Artifact`` / ``a2a.types.Part`` (RootModel wrapper)
        to stay compatible with the task serializer.
        """
        from uuid import uuid4

        from a2a.types import Artifact as A2AArtifact
        from a2a.types import DataPart, FilePart
        from a2a.types import Part as A2APart

        if not non_text_parts:
            return

        wrapped_parts: list[A2APart] = []
        for p in non_text_parts:
            kind = p.get("kind")
            try:
                if kind == "file":
                    wrapped_parts.append(A2APart(root=FilePart(**p)))
                elif kind == "data":
                    wrapped_parts.append(A2APart(root=DataPart(**p)))
            except Exception:
                logger.warning("Skipping invalid non-text part during materialization: %s", kind)

        if not wrapped_parts:
            return

        if task.artifacts is None:
            task.artifacts = []

        artifact = A2AArtifact(
            artifact_id=uuid4().hex,
            parts=wrapped_parts,
            name="streaming-multimodal",
            metadata={"source": "streaming_non_text"},
        )
        task.artifacts.append(artifact)

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
                "DirectTransport: No remote task ID to cancel for message %s",
                current_message.message_id,
            )
            return

        logger.info(
            "DirectTransport: Attempting to cancel remote task %s on agent %s",
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
                "DirectTransport: Setting up task tracking for message %s (step %s/%s, agent: %s)",
                current_message.message_id,
                step_number,
                total_steps,
                agent_card.name,
            )
            task_info = await self.a2a_service.create_task_for_tracking(
                current_message,
                agent_card,
                prepared_message,
                step_number=step_number,
                total_steps=total_steps,
            )

            created_at = task_info.get("created_at")

            task_content = current_message.task_content

            logger.info(
                "DirectTransport: Sending task_submitted SSE for step %s/%s, task_content: %s",
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
            "DirectTransport: Starting poll for task %s (agent task: %s), timeout: %ds",
            message_id,
            task_id,
            timeout_seconds,
        )

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_seconds:
                logger.warning(
                    "DirectTransport: Poll timeout for task %s after %.1fs (%d polls)",
                    message_id,
                    elapsed,
                    poll_count,
                )
                return None

            if token and token.is_cancelled:
                logger.info(
                    "DirectTransport: Polling cancelled for task %s after %d polls",
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
                        "DirectTransport: Polling sleep interrupted by cancellation for task %s",
                        message_id,
                    )
                    return None
                delay = min(delay * 1.5, max_delay)

            poll_count += 1

            try:
                async with self.a2a_service.create_a2a_client(agent_card) as a2a_client:
                    response = await a2a_client.get_task(
                        GetTaskRequest(id=task_id, params=TaskQueryParams(id=task_id))
                    )

                if not response or isinstance(response.root, JSONRPCErrorResponse):
                    logger.warning(
                        "DirectTransport: Poll %d for task %s returned error/empty",
                        poll_count,
                        message_id,
                    )
                    continue

                task = response.root.result
                if task is None:
                    logger.warning(
                        "DirectTransport: Poll %d for task %s returned no result",
                        poll_count,
                        message_id,
                    )
                    continue

                state = task.status.state
                state_value = state_str(state)

                if is_terminal_state(state):
                    logger.info(
                        "DirectTransport: Task %s completed with state %s after %.1fs (%d polls)",
                        message_id,
                        state_value,
                        asyncio.get_event_loop().time() - start_time,
                        poll_count,
                    )
                    return task

                logger.debug(
                    "DirectTransport: Poll %d for task %s: state=%s",
                    poll_count,
                    message_id,
                    state_value,
                )

            except Exception as e:
                logger.warning(
                    "DirectTransport: Poll %d for task %s failed: %s",
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
                    await self._handle_stream_artifact_update(
                        result, ctx, streaming_state
                    )
                case _:
                    logger.warning(
                        "DirectTransport: Unknown streaming event kind '%s' for message %s",
                        result.kind, ctx.user_message_id,
                    )

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
            "DirectTransport: Streaming cancelled for message %s", ctx.user_message_id
        )
        await self.tsm.transition_task(
            ctx.current_message, TaskState.canceled, persist=True
        )
        if ctx.task_info:
            await self._emit_terminal(ctx, TaskState.canceled)
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
        logger.error("DirectTransport: Agent error: %s", error_message)
        await self.tsm.transition_task(
            ctx.current_message, TaskState.failed, error=error_message,
            persist=True,
        )
        if ctx.task_info:
            await self._emit_terminal(ctx, TaskState.failed, error=error_message)
        if ctx.send_sse:
            await self.sse_manager.send_error(ctx.room_id, error_message)

        # Record capability issue for the agent
        try:
            from services.agent_capability_issue_service import (
                capability_issue_service,
            )

            await capability_issue_service.record_issue(
                agent_id=ctx.current_message.agent_id,
                error_message=error_message,
                query_text=(
                    ctx.current_message.task_content
                    or (ctx.current_message.message_content.message_text or "")
                ),
                room_id=ctx.room_id,
                message_id=ctx.current_message.message_id,
            )
        except Exception as rec_exc:  # noqa: BLE001
            logger.warning(
                "DirectTransport: Failed to record capability issue: %s",
                rec_exc,
            )

        return ProcessingStatus.FAILED, streaming_state.full_response_text

    async def _handle_stream_message_chunk(
        self,
        result,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> None:
        """Handle a 'message' event during streaming."""
        from common.utils.a2a_helpers import extract_parts

        message_list = result.parts
        streaming_state.accumulated_parts.extend(message_list)

        if streaming_state.agent_message_id is None:
            streaming_state.agent_message_id = result.message_id

        extracted = extract_parts(message_list)
        content = extracted.text
        streaming_state.full_response_text += content

        if extracted.has_non_text:
            streaming_state.non_text_parts.extend(extracted.file_parts)
            streaming_state.non_text_parts.extend(extracted.data_parts)

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
            "DirectTransport: Task event: %s", status.state if status else "no status"
        )

    async def _handle_stream_status_update(
        self,
        result,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> None:
        """Handle a 'status-update' event during streaming."""
        state = result.status.state
        is_final = getattr(result, "final", False)
        logger.info(
            "DirectTransport: Status update for message %s: %s (final=%s)",
            ctx.current_message.message_id,
            state,
            is_final,
        )

        if is_final:
            streaming_state.stream_finalized = True
            streaming_state.final_state = state

        a2a_status_message_text: str | None = None
        if result.status.message:
            a2a_status_message_text = get_text_from_message(result.status.message)

        task = get_task(ctx.current_message)
        if task:
            if is_terminal_state(state):
                await self.tsm.transition_task(
                    ctx.current_message, state, persist=True
                )
            else:
                task.status = TaskStatus(state=state)
                await self.tsm.persist_message(ctx.current_message)

        if state in TERMINAL_STATES:
            logger.info(
                "DirectTransport: Final status for message %s: %s",
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
                            "DirectTransport: Fetched final text differs from streaming text for %s",
                            ctx.current_message.message_id,
                        )
                    streaming_state.full_response_text = fetched_text
                else:
                    logger.warning(
                        "DirectTransport: Fetched task returned empty text for %s, keeping streaming text",
                        ctx.current_message.message_id,
                    )
            else:
                logger.error(
                    "DirectTransport: Failed to retrieve final task for task id %s",
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
        streaming_state: MessageStreamingState,
    ) -> None:
        """Handle an 'artifact-update' event during streaming."""
        artifact_result = getattr(result, "artifact", None)
        append = getattr(result, "append", False) or False
        last_chunk = getattr(result, "last_chunk", False) or False

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

        # Convert inline base64 to S3 URIs before persistence and SSE.
        # Share the counter across chunks via streaming_state so the
        # per-message cap is enforced across the whole streaming session.
        shared_counter = [streaming_state.inline_conversion_count]
        await self._convert_inline_bytes_to_s3(
            artifact_result, ctx.room_id, ctx.current_message.message_id,
            conversion_counter=shared_counter,
        )
        streaming_state.inline_conversion_count = shared_counter[0]

        await self.tsm.persist_message(ctx.current_message)

        if ctx.send_sse:
            # Explicitly serialize to dict so json.dumps doesn't choke on Pydantic models
            artifact_dict = (
                artifact_result.model_dump()
                if hasattr(artifact_result, "model_dump")
                else artifact_result
            )
            await self.sse_manager.send_artifact_update(
                ctx.room_id,
                ctx.current_message.message_id,
                ctx.current_message.agent_id,
                artifact_dict,
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
            "DirectTransport: Streaming complete for message %s, text length: %d",
            ctx.current_message.message_id,
            len(streaming_state.full_response_text),
        )

        task = get_task(ctx.current_message)
        if not task:
            logger.warning(
                "DirectTransport: _finalize_streaming: no in-memory task for message %s; "
                "task_update SSE will not be sent via this path",
                ctx.current_message.message_id,
            )
        already_terminal = task and task.status and is_terminal_state(task.status.state)

        # Convert inline base64 file parts to S3 URIs and materialize them
        # as a task artifact *before* persisting / notifying, so that the DB
        # always contains the multimodal data and clients that reconnect
        # after missing the real-time SSE can still hydrate them.
        if streaming_state.non_text_parts:
            new_total = await self._convert_streaming_parts_to_s3(
                streaming_state.non_text_parts,
                ctx.room_id,
                ctx.current_message.message_id,
                converted_so_far=streaming_state.inline_conversion_count,
            )
            streaming_state.inline_conversion_count = new_total
            if task:
                self._materialize_non_text_parts_as_artifact(
                    task, streaming_state.non_text_parts,
                )

        if task and not already_terminal:
            if streaming_state.stream_finalized:
                final_st = streaming_state.final_state or TaskState.completed
                if is_failure_state(final_st):
                    if streaming_state.full_response_text:
                        ctx.current_message.message_content.message_text = (
                            streaming_state.full_response_text
                        )
                    await self.tsm.transition_task(
                        ctx.current_message, final_st, persist=True
                    )
                    await self._emit_terminal(ctx, final_st)
                    return ProcessingStatus.FAILED, streaming_state.full_response_text
                elif final_st in INTERACTIVE_STATES:
                    await self.tsm.transition_task(
                        ctx.current_message, final_st, persist=True
                    )
                    await self._emit_terminal(ctx, final_st)
                    return ProcessingStatus.SUCCESS, streaming_state.full_response_text
                else:
                    if streaming_state.full_response_text:
                        ctx.current_message.message_content.message_text = (
                            streaming_state.full_response_text
                        )
                    await self.tsm.transition_task(
                        ctx.current_message, TaskState.completed, persist=True
                    )
                    await self._emit_terminal(ctx, TaskState.completed)
            elif streaming_state.full_response_text:
                ctx.current_message.message_content.message_text = (
                    streaming_state.full_response_text
                )
                await self.tsm.transition_task(
                    ctx.current_message,
                    TaskState.completed,
                    persist=True,
                )
                await self._emit_terminal(ctx, TaskState.completed)
            else:
                logger.warning(
                    "DirectTransport: Stream ended without terminal status or content for %s",
                    ctx.current_message.message_id,
                )
                await self.tsm.transition_task(
                    ctx.current_message,
                    TaskState.failed,
                    persist=True,
                )
                await self._emit_terminal(ctx, TaskState.failed)
                return ProcessingStatus.FAILED, streaming_state.full_response_text

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

            if streaming_state.full_response_text:
                ctx.current_message.message_content.message_text = (
                    streaming_state.full_response_text
                )
            await self.tsm.persist_message(ctx.current_message)
            await self._emit_terminal(ctx, final_state)

            if is_failure_state(final_state):
                return ProcessingStatus.FAILED, streaming_state.full_response_text

        # Send non-text parts via agent_response for real-time clients
        if streaming_state.non_text_parts and ctx.send_sse:
            await self.sse_manager.send_agent_response(
                ctx.room_id,
                ctx.current_message.message_id,
                ctx.current_message.agent_id,
                streaming_state.full_response_text,
                parts=streaming_state.non_text_parts,
            )

        return ProcessingStatus.SUCCESS, streaming_state.full_response_text

    async def _handle_a2a_response_for_room(
        self, room_agent_message: RoomAgentMessage, message_data: None | Task | Message
    ) -> bool:
        if message_data is None:
            logger.error("DirectTransport: process_a2a_response returned None")
            return False

        if message_data.kind == "task":
            existing_task = get_task(room_agent_message)
            if (
                existing_task
                and existing_task.status
                and is_terminal_state(existing_task.status.state)
            ):
                # The in-memory task already has a terminal status (e.g. completed).
                # The re-fetched task may carry stale non-terminal data — merge
                # only artifacts and history without downgrading the status.
                incoming_state = (
                    message_data.status.state if message_data.status else None
                )
                if incoming_state and not is_terminal_state(incoming_state):
                    logger.info(
                        "_handle_a2a_response_for_room: preserving terminal status %s, "
                        "incoming had %s for %s",
                        state_str(existing_task.status.state),
                        state_str(incoming_state),
                        room_agent_message.message_id,
                    )
                    if message_data.artifacts:
                        existing_task.artifacts = message_data.artifacts
                    if message_data.history:
                        existing_task.history = message_data.history
                else:
                    room_agent_message.message_content.message_task = message_data
            else:
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
            "DirectTransport: Unexpected data kind in A2A response: %s",
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
            from common.utils.a2a_helpers import extract_parts

            extracted = extract_parts(result.parts or [])
            parsed: dict[str, Any] = {
                "type": "message",
                "message_id": message_id,
                "content": extracted.text,
            }
            if extracted.has_non_text:
                parsed["parts"] = extracted.file_parts + extracted.data_parts
            return parsed

        if result.kind == "task":
            state = result.status.state
            state_value = state_str(state)
            parsed: dict[str, Any] = {
                "type": "task",
                "message_id": message_id,
                "task_id": result.id,
                "status": state_value,
            }
            if is_terminal_state(state) and result.artifacts:
                from common.utils.a2a_helpers import extract_parts_from_artifacts as _epfa

                extracted_task = _epfa(result.artifacts)
                if extracted_task.text:
                    parsed["type"] = "message"
                    parsed["content"] = extracted_task.text
                if extracted_task.has_non_text:
                    parsed["parts"] = extracted_task.file_parts + extracted_task.data_parts
            return parsed

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
                "DirectTransport: task tracking setup failed for message %s — degraded mode",
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
                "DirectTransport: Sync call cancelled before agent call for %s",
                message_id,
            )
            await self.tsm.transition_task(
                current_message, TaskState.canceled,
                persist=True,
            )
            if task_info:
                await self._emit_terminal(ctx, TaskState.canceled)
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
                "DirectTransport: Sync call cancelled during agent call for %s",
                message_id,
            )
            await self.tsm.transition_task(
                current_message, TaskState.canceled,
                persist=True,
            )
            if task_info:
                await self._emit_terminal(ctx, TaskState.canceled)
            await self._try_cancel_remote_task(current_message, agent_card)
            return False, "", None
        except Exception as exc:
            logger.error("Agent error: %s", exc, exc_info=True)
            await self.tsm.transition_task(
                current_message,
                TaskState.failed,
                error=str(exc),
                persist=True,
            )
            if task_info:
                await self._emit_terminal(ctx, TaskState.failed, error=str(exc),
                )
            await self.sse_manager.send_error(room_id, str(exc))

            # Record capability issue for the agent
            try:
                from services.agent_capability_issue_service import (
                    capability_issue_service,
                )

                await capability_issue_service.record_issue(
                    agent_id=current_message.agent_id,
                    error_message=str(exc),
                    query_text=(
                        current_message.task_content
                        or (current_message.message_content.message_text or "")
                    ),
                    room_id=room_id,
                    message_id=current_message.message_id,
                )
            except Exception as rec_exc:  # noqa: BLE001
                logger.warning(
                    "DirectTransport: Failed to record capability issue: %s",
                    rec_exc,
                )

            return False, "", None

        # Post-call cancellation check
        if token and token.is_cancelled:
            logger.info(
                "DirectTransport: Sync call cancelled after agent response for %s",
                message_id,
            )
            await self.tsm.transition_task(
                current_message, TaskState.canceled,
                persist=True,
            )
            if task_info:
                await self._emit_terminal(ctx, TaskState.canceled)
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
            non_text_parts = response.get("parts")
            if full_response_text:
                current_message.message_content.message_text = full_response_text

            # Convert inline base64 file parts to S3 URIs
            if non_text_parts:
                await self._convert_streaming_parts_to_s3(
                    non_text_parts, room_id, message_id,
                )
                task = get_task(current_message)
                if task:
                    self._materialize_non_text_parts_as_artifact(
                        task, non_text_parts,
                    )

            await self.tsm.transition_task(
                current_message,
                TaskState.completed,
                persist=True,
            )
            await self._emit_terminal(ctx, TaskState.completed, parts=non_text_parts if non_text_parts else None)

            if not task_info:
                logger.info(
                    "DirectTransport: Degraded mode — sending task_update directly for %s",
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
                    parts=non_text_parts if non_text_parts else None,
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
                if response.get("requires_input"):
                    task_obj = get_task(current_message)
                    if task_obj and task_obj.status:
                        task_obj.status.state = TaskState.input_required
                return True, None, message_id

            # Non-push agent: poll for completion
            agent_task_id = response.get("task_id")
            if not agent_task_id:
                logger.warning(
                    "DirectTransport: Non-push agent task response without task_id"
                )
                return True, None, None

            logger.info(
                "DirectTransport: Polling non-push agent task %s", agent_task_id
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
                    "DirectTransport: Poll cancelled for task %s, transitioning to canceled",
                    message_id,
                )
                await self.tsm.transition_task(
                    current_message, TaskState.canceled,
                    persist=True,
                )
                if task_info:
                    await self._emit_terminal(ctx, TaskState.canceled)
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
                    "DirectTransport: Polling timed out for task %s",
                    message_id,
                )
                await self.tsm.transition_task(
                    current_message, TaskState.failed,
                    error="Task polling timed out",
                    persist=True,
                )
                await self._emit_terminal(ctx, TaskState.failed, error="Task polling timed out")
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

        if completed_task.artifacts:
            conversion_counter: list[int] = [0]
            for artifact in completed_task.artifacts:
                await self._convert_inline_bytes_to_s3(
                    artifact, room_id, message_id,
                    conversion_counter=conversion_counter,
                )

        final_content = None
        final_error = None
        if state == TaskState.completed and completed_task.artifacts:
            final_content = extract_text_from_artifacts(completed_task.artifacts)
        elif is_failure_state(state):
            final_error = extract_error_message(completed_task) or f"Task {state_value}"

        if task_info:
            # TODO: Phase 5 -- migrate to incremental update_task_state_on_message.
            # This is the last caller of the full-document update_task_on_message;
            # the polled task has a complete Task model that doesn't yet fit the
            # incremental pattern.
            await self.database_service.update_task_on_message(
                message_id,
                completed_task.model_dump(mode="json"),
                message_text=final_content or None,
            )

        if task_info:
            await self._emit_terminal(ctx, state)
        else:
            logger.info(
                "DirectTransport: Degraded mode — sending polled task_update for %s",
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
