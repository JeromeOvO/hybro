"""DirectTransport — cloud SSE/sync transport to cloud-reachable A2A agents.

Absorbs all response-processing logic (formerly in ``ResponseProcessor``)
and replaces terminal ``notify_task_update`` calls with ``AgentEvent``
emissions through ``AgentResponseHandler``.

Mid-stream content stays in memory until terminal finalization.
"""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from a2a_adapter.remote_task import fetch_remote_task
from a2a_adapter.task_artifacts import materialize_non_text_parts_as_artifact
from a2a_adapter.task_status import build_task_status, coerce_task_state
from common.a2a_constants import (
    INTERACTIVE_STATES,
    TERMINAL_STATES,
    CommonTaskState,
    SyntheticTaskId,
    is_failure_state,
    is_interactive_state,
    is_terminal_state,
)
from common.a2a_task_projection import (
    public_artifact_data,
    public_message_data,
    public_part_data,
    public_persisted_task_data,
)
from common.types import (
    Artifact,
    Message,
    MessageRole,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    TextPart,
)
from common.utils.a2a_helpers import (
    extract_error_message,
    extract_text_from_artifacts,
    get_message_from_task,
    get_text_from_message,
)
from common.utils.artifact_delivery import (
    OUTPUT_DELIVERY_FAILURE_CODE,
    OUTPUT_DELIVERY_FAILURE_MESSAGE,
    mark_task_output_delivery_failed,
    mark_unresolved_file_parts_unavailable,
    new_materialization_report,
    output_delivery_failed,
)
from common.utils.cancellation import CancellationError, CancellationToken
from common.utils.logger import get_logger
from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.dispatch_middleware import DispatchContext
from execution.dispatch.transports.base import AgentTransport
from execution.hitl.public_prompt import safe_agent_input_prompt
from execution.state.task_state_manager import (
    TaskStateManager,
    get_task,
    state_str,
)
from execution.task_tracking import (
    extract_public_completed_status_text,
    resolve_public_task_label,
)
from models.error import A2AServiceError
from models.processing import ProcessingContext, ProcessingResult, ProcessingStatus
from models.room import RoomAgentMessage

logger = get_logger(__name__)

_PUBLIC_AGENT_FAILURE_MESSAGE = "Agent processing failed"
_PUBLIC_AGENT_FAILURE_CODE = "agent_execution_failed"
_PUBLIC_TASK_TERMINAL_ERRORS = {
    CommonTaskState.FAILED.value: "Task failed",
    CommonTaskState.REJECTED.value: "Task was rejected by the agent",
    CommonTaskState.CANCELED.value: "Task was canceled",
    "expired": "Task expired",
}


if TYPE_CHECKING:
    from execution.ports import (
        A2ATransportPort,
        ExecutionDeliveryPort,
        RemoteTaskReaderPort,
    )

    class DirectMessageReader(Protocol):
        async def get_room_agent_message_by_message_id(self, message_id: str): ...

    class DirectArtifactStore(Protocol):
        async def accumulate_artifact_on_message(
            self, message_id: str, artifact: dict, *, append: bool = False
        ) -> bool: ...

    class DirectTaskUpdatePort(Protocol):
        async def update_task_on_message(
            self,
            message_id: str,
            task_data: dict,
            *,
            message_text: str | None = None,
        ) -> bool: ...


@dataclass
class MessageStreamingState:
    """Tracks mutable streaming state across sub-handlers during a single streaming session."""

    full_response_text: str = ""
    public_message_text: str | None = None
    accumulated_parts: list[Any] = field(default_factory=list)
    non_text_parts: list[dict] = field(default_factory=list)
    inline_conversion_count: int = 0
    materialization_report: dict[str, Any] = field(
        default_factory=new_materialization_report
    )
    stream_finalized: bool = False
    final_state: Any | None = None


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
        a2a_transport: "A2ATransportPort",
        remote_task_reader: "RemoteTaskReaderPort",
        delivery: "ExecutionDeliveryPort",
        message_reader: "DirectMessageReader",
        artifact_store: "DirectArtifactStore",
        task_updater: "DirectTaskUpdatePort",
        capability_issue_service=None,
    ) -> None:
        super().__init__(response_handler)
        self.tsm = tsm
        self.delivery = delivery
        self.a2a_transport = a2a_transport
        self.remote_task_reader = remote_task_reader
        self._message_reader = message_reader
        self._artifact_store = artifact_store
        self._task_updater = task_updater
        self.capability_issue_service = capability_issue_service

    # ------------------------------------------------------------------
    # Terminal event emission
    # ------------------------------------------------------------------

    @staticmethod
    def _platform_failure(message: RoomAgentMessage) -> tuple[str, str]:
        task = get_task(message)
        metadata = getattr(task, "metadata", None) if task is not None else None
        if (
            isinstance(metadata, dict)
            and metadata.get("output_failure_code") == OUTPUT_DELIVERY_FAILURE_CODE
        ):
            return OUTPUT_DELIVERY_FAILURE_MESSAGE, OUTPUT_DELIVERY_FAILURE_CODE
        return _PUBLIC_AGENT_FAILURE_MESSAGE, _PUBLIC_AGENT_FAILURE_CODE

    async def _emit_terminal(
        self,
        ctx: ProcessingContext,
        state: Any,
        *,
        error: str | None = None,
        parts: list[dict] | None = None,
        public_text: str | None = None,
    ) -> None:
        """Emit a terminal AgentEvent through the shared response handler.

        skip_persist=True because the task document was already written to DB
        — either by tsm.transition_task / tsm.persist_message (streaming and
        degraded sync paths) or by A2A transport partial $set via
        update_task_on_message (tracked sync path).
        """
        msg = ctx.current_message
        kind: str
        if state == CommonTaskState.CANCELED:
            kind = "canceled"
        elif is_failure_state(state):
            kind = "error"
        elif state in INTERACTIVE_STATES:
            kind = "interactive"
        else:
            kind = "response"

        public_parts = None
        if parts is not None:
            public_parts = [
                public_part
                for part in parts
                if (public_part := public_part_data(part)) is not None
            ]
        explicit_public_text = public_text
        event_text = error or explicit_public_text or ""
        if not event_text:
            task = get_task(msg)
            if task is not None:
                public_task = Task.model_validate(public_persisted_task_data(task))
                event_text = get_text_from_message(get_message_from_task(public_task))
        if not event_text:
            agent_name = getattr(ctx.agent_card, "name", None)
            event_text = resolve_public_task_label(
                msg.extend_info,
                agent_name if isinstance(agent_name, str) else msg.agent_id or "agent",
            )

        task = get_task(msg)
        task_metadata = getattr(task, "metadata", None) if task is not None else None
        output_failure = bool(
            isinstance(task_metadata, dict)
            and task_metadata.get("output_failure_code") == OUTPUT_DELIVERY_FAILURE_CODE
        )

        await self.response_handler.handle(
            AgentEvent(
                kind=kind,
                message_id=msg.message_id,
                room_id=ctx.room_id,
                agent_id=msg.agent_id or "",
                text=event_text,
                public_text=(explicit_public_text if kind == "response" else None),
                error_text=error if kind == "error" else None,
                details=(
                    {"output_failure_code": OUTPUT_DELIVERY_FAILURE_CODE}
                    if output_failure
                    else None
                ),
                state=state.value if hasattr(state, "value") else str(state),
                related_message_id=msg.related_message_id,
                user_id=msg.user_id or "",
                client_request_id=msg.client_request_id,
                parts=public_parts,
                skip_persist=True,
            )
        )

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

        support_streaming = self.a2a_transport.has_streaming_capability(
            agent_card=agent.agent_card
        )

        full_response_text = ""
        paused_message_id = None
        agent_task_id = None
        interactive_status_context: dict[str, str | None] = {}
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
                    interactive_status_context=interactive_status_context,
                )
            except Exception as exc:
                internal_error = f"Agent streaming failed: {exc}"
                logger.error(
                    "DirectTransport.dispatch: Unhandled exception in streaming for message %s: %s",
                    message.message_id,
                    exc,
                    exc_info=True,
                )
                await self.tsm.transition_task(
                    message,
                    CommonTaskState.FAILED,
                    error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                    persist=True,
                )
                fallback_ctx = ProcessingContext(
                    room_id=room_id,
                    current_message=message,
                    agent_card=agent.agent_card,
                    user_message_id=user_message_id,
                )
                await self._emit_terminal(
                    fallback_ctx,
                    CommonTaskState.FAILED,
                    error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                )
                await self.delivery.send_error(
                    room_id,
                    _PUBLIC_AGENT_FAILURE_MESSAGE,
                    message_id=message.message_id,
                )

                # Record capability issue for the agent
                if self.capability_issue_service is not None:
                    try:
                        await self.capability_issue_service.record_issue(
                            agent_id=message.agent_id,
                            error_message=internal_error,
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

                return ProcessingResult(
                    ProcessingStatus.FAILED,
                    _PUBLIC_AGENT_FAILURE_MESSAGE,
                    status_message=_PUBLIC_AGENT_FAILURE_CODE,
                )
            if status != ProcessingStatus.SUCCESS:
                if status == ProcessingStatus.FAILED:
                    failure_message, failure_code = self._platform_failure(message)
                    return ProcessingResult(
                        status,
                        failure_message,
                        status_message=failure_code,
                    )
                return ProcessingResult(status, full_response_text)
        else:
            (
                success,
                full_response_text,
                paused_message_id,
                agent_task_id,
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
                interactive_status_context=interactive_status_context,
            )
            if not success:
                task = get_task(message)
                was_canceled = (token and token.is_cancelled) or (
                    task
                    and task.status
                    and task.status.state == CommonTaskState.CANCELED
                )
                if was_canceled:
                    return ProcessingResult(ProcessingStatus.CANCELED)
                failure_message, failure_code = self._platform_failure(message)
                return ProcessingResult(
                    ProcessingStatus.FAILED,
                    failure_message,
                    status_message=failure_code,
                )

        if full_response_text is None and paused_message_id:
            task = get_task(message)
            if task and task.status and is_interactive_state(task.status.state):
                logger.info(
                    "DirectTransport.dispatch: Agent returned interactive state %s for message %s",
                    task.status.state,
                    paused_message_id,
                )
                task_data = (
                    public_persisted_task_data(Task.model_validate(task))
                    if hasattr(task, "model_dump")
                    else {}
                )
                status_value = state_str(task.status.state)
                if status_value == CommonTaskState.AUTH_REQUIRED.value:
                    status_msg = "Authentication required"
                else:
                    status_msg = interactive_status_context.get("status_message") or (
                        self._public_interactive_status_message(
                            ProcessingContext(
                                room_id=room_id,
                                current_message=message,
                                agent_card=agent.agent_card,
                                user_message_id=user_message_id,
                            )
                        )
                    )
                return ProcessingResult(
                    ProcessingStatus.AWAITING_INPUT,
                    response_text="",
                    message_id=paused_message_id,
                    a2a_task_id=agent_task_id
                    or task_data.get("id")
                    or (task.id if hasattr(task, "id") else None),
                    a2a_context_id=task.context_id
                    if hasattr(task, "context_id")
                    else task_data.get("contextId"),
                    status_message=status_msg,
                    interactive_state=status_value,
                    requires_auth=(status_value == CommonTaskState.AUTH_REQUIRED.value),
                    requires_policy=bool(
                        status_value == CommonTaskState.POLICY_REQUIRED.value
                        or (task_data.get("metadata") or {}).get("requires_policy")
                        or (task_data.get("metadata") or {}).get("policy_required")
                    ),
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

        message = await self._message_reader.get_room_agent_message_by_message_id(
            message.message_id
        )

        if message is None:
            return ProcessingResult(ProcessingStatus.FAILED, full_response_text)

        return ProcessingResult(ProcessingStatus.SUCCESS, full_response_text)

    async def _materialize_streaming_file_parts(
        self,
        non_text_parts: list[dict],
        room_id: str,
        message_id: str,
        *,
        converted_so_far: int = 0,
        report: dict[str, Any] | None = None,
    ) -> int:
        """Materialize accumulated streaming file parts as durable room files.

        Delegates to the shared helper in a2a_helpers.  Returns the updated
        running total so callers can keep the per-message cap accurate.
        """
        from common.utils.a2a_helpers import materialize_inline_file_parts

        return await materialize_inline_file_parts(
            non_text_parts,
            room_id,
            message_id,
            converted_so_far=converted_so_far,
            report=report,
        )

    @staticmethod
    def _materialize_non_text_parts_as_artifact(
        task, non_text_parts: list[dict]
    ) -> None:
        """Wrap accumulated non-text streaming parts into a task artifact."""
        materialize_non_text_parts_as_artifact(task, non_text_parts)

    @staticmethod
    def _materialize_text_as_response_artifact(
        task: Task,
        text: str,
        *,
        artifact_id: str,
    ) -> None:
        """Store completed text output as a sanitized artifact, never history."""
        if not text:
            return
        artifact = Artifact(
            artifact_id=artifact_id,
            name="response",
            parts=[Part(root=TextPart(text=text))],
        )
        public_artifact = Artifact.model_validate(public_artifact_data(artifact))
        if task.artifacts is None:
            task.artifacts = []
        for index, existing in enumerate(task.artifacts):
            if getattr(existing, "artifact_id", None) == artifact_id:
                task.artifacts[index] = public_artifact
                break
        else:
            task.artifacts.append(public_artifact)

    @staticmethod
    def _force_terminal_state_for_projection(task: Task, state: Any) -> None:
        """Mirror transition_task's mutation when tests inject a bare mock."""
        if task.status and is_terminal_state(task.status.state):
            return
        task.status = build_task_status(state)

    @staticmethod
    def _public_task_model(task: Any) -> Task:
        """Return a Task safe to persist or retain for later persistence."""
        return Task.model_validate(
            public_persisted_task_data(Task.model_validate(task))
        )

    @staticmethod
    def _public_terminal_output_task_model(task: Any) -> Task:
        task_data = public_persisted_task_data(Task.model_validate(task))
        try:
            return Task.model_validate(task_data)
        except ValueError:
            return Task.model_validate(
                DirectTransport._drop_unaddressable_public_file_parts(task_data)
            )

    @staticmethod
    def _drop_unaddressable_public_file_parts(
        task_data: dict[str, Any],
    ) -> dict[str, Any]:
        artifacts = task_data.get("artifacts")
        if not isinstance(artifacts, list):
            return task_data

        public_artifacts: list[dict[str, Any]] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            parts = artifact.get("parts")
            if not isinstance(parts, list):
                continue

            public_parts = []
            for part in parts:
                if not isinstance(part, dict):
                    public_parts.append(part)
                    continue
                payload = (
                    part.get("root") if isinstance(part.get("root"), dict) else part
                )
                file_payload = (
                    payload.get("file") if isinstance(payload, dict) else None
                )
                if isinstance(file_payload, dict) and not file_payload.get("uri"):
                    continue
                public_parts.append(part)

            if public_parts:
                public_artifact = dict(artifact)
                public_artifact["parts"] = public_parts
                public_artifacts.append(public_artifact)

        sanitized = dict(task_data)
        sanitized["artifacts"] = public_artifacts or None
        return sanitized

    @staticmethod
    def _is_agent_message(message: Any) -> bool:
        role = getattr(message, "role", None)
        return role == MessageRole.AGENT or role == MessageRole.AGENT.value

    @staticmethod
    def _public_task_label(
        current_message: RoomAgentMessage,
        agent_name: str,
    ) -> str:
        return resolve_public_task_label(current_message.extend_info, agent_name)

    def _public_interactive_status_message(
        self,
        ctx: ProcessingContext,
        raw_status_message: str | None = None,
    ) -> str:
        if raw_status_message:
            safe_prompt = safe_agent_input_prompt(raw_status_message)
            if safe_prompt:
                return safe_prompt
        agent_name = getattr(ctx.agent_card, "name", None)
        return self._public_task_label(
            ctx.current_message,
            agent_name
            if isinstance(agent_name, str)
            else ctx.current_message.agent_id or "agent",
        )

    def _materialize_message_as_response_artifact(
        self,
        task: Task,
        message: Any,
        *,
        artifact_id: str,
    ) -> bool:
        message_data = public_message_data(Message.model_validate(message))
        if message_data is None:
            return False
        parts = [Part.model_validate(part) for part in message_data.get("parts") or []]
        if not parts:
            return False
        artifact = Artifact(
            artifact_id=artifact_id,
            name="response",
            parts=parts,
        )
        public_artifact = Artifact.model_validate(public_artifact_data(artifact))
        if task.artifacts is None:
            task.artifacts = []
        task.artifacts.append(public_artifact)
        return True

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _try_cancel_remote_task(
        self,
        current_message: RoomAgentMessage,
        agent_card: Any,
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
        await self.a2a_transport.cancel_remote_task(agent_card, remote_task_id)

    async def _setup_task_tracking(
        self,
        current_message: RoomAgentMessage,
        agent_card: Any,
        prepared_message: Any,
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
            task_info = await self.a2a_transport.create_task_for_tracking(
                current_message,
                agent_card,
                prepared_message,
                step_number=step_number,
                total_steps=total_steps,
            )

            created_at = (
                current_message.message_created_at.isoformat()
                if current_message.message_created_at
                else task_info.get("created_at")
            )

            task_content = self._public_task_label(current_message, agent_card.name)

            logger.info(
                "DirectTransport: Sending task_submitted SSE for step %s/%s, task_content: %s",
                step_number,
                total_steps,
                task_content[:50] if task_content else "None",
            )

            await self.delivery.send_task_submitted(
                room_id=room_id,
                message_id=current_message.message_id,
                task_id=SyntheticTaskId.PENDING,
                agent_name=agent_card.name,
                agent_id=current_message.agent_id,
                status=CommonTaskState.WORKING,
                related_message_id=current_message.related_message_id,
                created_at=created_at,
                step_number=step_number,
                total_steps=total_steps,
                task_content=task_content,
                client_request_id=current_message.client_request_id,
            )
            # Emit an initial working status so the agent card shows a live
            # status description immediately after task_submitted.
            # Agents that emit their own A2A status-update messages will
            # overwrite this with more specific text.
            initial_status_msg = task_content or "Working on your request…"
            await self.delivery.send_task_update(
                room_id=room_id,
                message_id=current_message.message_id,
                status="working",
                status_message=initial_status_msg,
                agent_id=current_message.agent_id,
                client_request_id=current_message.client_request_id,
            )
            return task_info
        except Exception as exc:
            logger.warning("Failed to setup task tracking: %s", exc)
            return None

    async def _setup_tracking_context(
        self,
        current_message: RoomAgentMessage,
        agent_card: Any,
        prepared_message: Any,
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
            created_at=(
                current_message.message_created_at.isoformat()
                if current_message.message_created_at
                else (task_info.get("created_at") if task_info else None)
            ),
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
        agent_card: Any,
        task_id: str,
        message_id: str,
        timeout_seconds: int = 120,
        initial_delay: float = 0.5,
        max_delay: float = 5.0,
        token: CancellationToken | None = None,
    ) -> Any | None:
        """Poll an agent for task completion with exponential backoff.

        Returns:
            The completed task if found, None if timeout, error, or cancelled
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
                task = await fetch_remote_task(agent_card, task_id)
                if task is None:
                    logger.warning(
                        "DirectTransport: Poll %d for task %s returned no result",
                        poll_count,
                        message_id,
                    )
                    continue

                state = task.status.state
                state_value = state_str(state)

                if is_terminal_state(state) or state in INTERACTIVE_STATES:
                    logger.info(
                        "DirectTransport: Task %s reached actionable state %s after %.1fs (%d polls)",
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
        agent_card: Any,
        prepared_message: Any,
        room_id: str,
        user_message_id: str,
        *,
        token: CancellationToken | None = None,
        send_sse: bool = False,
        step_number: int | None = None,
        total_steps: int | None = None,
        interactive_status_context: dict[str, str | None] | None = None,
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

        async for a2a_response in self.a2a_transport.send_message_streaming(
            agent_card,
            prepared_message,
            agent_id=current_message.agent_id,
        ):
            if token and token.is_cancelled:
                return await self._handle_streaming_cancellation(ctx, streaming_state)

            error_message = self._stream_error_message(a2a_response)
            if error_message is not None:
                return await self._handle_streaming_error(
                    error_message,
                    ctx,
                    streaming_state,
                )

            result = self._coerce_stream_result(a2a_response)
            match result.kind:
                case "message":
                    await self._handle_stream_message_chunk(
                        result, ctx, streaming_state
                    )
                case "task":
                    self._handle_stream_task_event(result)
                case "status-update":
                    await self._handle_stream_status_update(
                        result,
                        ctx,
                        streaming_state,
                        interactive_status_context=interactive_status_context,
                    )
                case "artifact-update":
                    await self._handle_stream_artifact_update(
                        result, ctx, streaming_state
                    )
                case _:
                    logger.warning(
                        "DirectTransport: Unknown streaming event kind '%s' for message %s",
                        result.kind,
                        ctx.user_message_id,
                    )

        return await self._finalize_streaming(ctx, streaming_state)

    @staticmethod
    def _coerce_stream_result(a2a_response: dict[str, Any]) -> Any:
        if not isinstance(a2a_response, dict):
            raise A2AServiceError("A2A stream event must be a normalized dict")
        kind = a2a_response.get("kind")
        result = a2a_response.get("result") or {}
        if kind == "message":
            return Message.model_validate(result)
        if kind == "task":
            return Task.model_validate(result)
        if kind == "status-update":
            return TaskStatusUpdateEvent.model_validate(result)
        if kind == "artifact-update":
            return TaskArtifactUpdateEvent.model_validate(result)
        raise A2AServiceError(f"Unknown A2A stream event kind: {kind}")

    @staticmethod
    def _stream_error_message(a2a_response: Any) -> str | None:
        if not isinstance(a2a_response, dict):
            return None
        if a2a_response.get("kind") != "error":
            return None
        return str(a2a_response.get("error") or "Unknown A2A stream error")

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
            ctx.current_message, CommonTaskState.CANCELED, persist=True
        )
        if ctx.task_info:
            await self._emit_terminal(ctx, CommonTaskState.CANCELED)
        # NOTE: Do NOT send processing_status here — QueueExecutor handles
        # workflow-level SSE after all siblings are persisted.
        await self._try_cancel_remote_task(ctx.current_message, ctx.agent_card)
        return ProcessingStatus.CANCELED, streaming_state.full_response_text

    async def _handle_streaming_error(
        self,
        error_message: str,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> tuple[ProcessingStatus, str]:
        """Handle JSON-RPC error during streaming."""
        logger.error("DirectTransport: Agent error: %s", error_message)
        await self.tsm.transition_task(
            ctx.current_message,
            CommonTaskState.FAILED,
            error=_PUBLIC_AGENT_FAILURE_MESSAGE,
            persist=True,
        )
        if ctx.task_info:
            await self._emit_terminal(
                ctx,
                CommonTaskState.FAILED,
                error=_PUBLIC_AGENT_FAILURE_MESSAGE,
            )
        if ctx.send_sse:
            await self.delivery.send_error(
                ctx.room_id,
                _PUBLIC_AGENT_FAILURE_MESSAGE,
                message_id=ctx.current_message.message_id,
            )

        # Record capability issue for the agent
        if self.capability_issue_service is not None:
            try:
                await self.capability_issue_service.record_issue(
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

        return ProcessingStatus.FAILED, _PUBLIC_AGENT_FAILURE_MESSAGE

    async def _handle_stream_message_chunk(
        self,
        result,
        ctx: ProcessingContext,
        streaming_state: MessageStreamingState,
    ) -> None:
        """Handle a 'message' event during streaming."""
        if not self._is_agent_message(result):
            logger.warning(
                "DirectTransport: Ignoring non-agent stream message for %s",
                ctx.current_message.message_id,
            )
            return

        from common.utils.a2a_helpers import extract_parts

        message_list = self._coerce_parts(result.parts)
        streaming_state.accumulated_parts.extend(message_list)

        extracted = extract_parts(message_list)
        content = extracted.text
        streaming_state.full_response_text += content

        if extracted.has_non_text:
            streaming_state.non_text_parts.extend(extracted.file_parts)
            streaming_state.non_text_parts.extend(extracted.data_parts)

        task = get_task(ctx.current_message)
        if task:
            ctx.current_message.message_content.message_task = self._public_task_model(
                task
            )
            await self.tsm.persist_message(ctx.current_message)

    @staticmethod
    def _coerce_parts(parts: list[Any]) -> list[Part]:
        coerced: list[Part] = []
        for part in parts or []:
            if isinstance(part, Part):
                coerced.append(part)
            elif isinstance(part, dict):
                data = dict(part)
                if "kind" not in data and "text" in data:
                    data["kind"] = "text"
                coerced.append(Part.model_validate(data))
            elif hasattr(part, "root"):
                coerced.append(Part(root=part.root))
            elif hasattr(part, "text"):
                coerced.append(Part(root=TextPart(text=part.text)))
        return coerced

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
        *,
        interactive_status_context: dict[str, str | None] | None = None,
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
        if (
            is_final
            and state_str(state) == CommonTaskState.COMPLETED.value
            and a2a_status_message_text
        ):
            streaming_state.public_message_text = (
                a2a_status_message_text.strip() or None
            )
            if (
                streaming_state.public_message_text
                and ctx.current_message.message_content
            ):
                ctx.current_message.message_content.message_text = (
                    streaming_state.public_message_text
                )

        task = get_task(ctx.current_message)
        if task:
            result_task_id = getattr(result, "task_id", None)
            result_context_id = getattr(result, "context_id", None)
            if isinstance(result_task_id, str) and result_task_id:
                task.id = result_task_id
            if isinstance(result_context_id, str) and result_context_id:
                task.context_id = result_context_id
            if is_terminal_state(state):
                ctx.current_message.message_content.message_task = (
                    self._public_task_model(task)
                )
                await self.tsm.transition_task(ctx.current_message, state, persist=True)
            else:
                task.status = build_task_status(state)
                ctx.current_message.message_content.message_task = (
                    self._public_task_model(task)
                )
                await self.tsm.persist_message(ctx.current_message)

        if state in TERMINAL_STATES:
            logger.info(
                "DirectTransport: Final status for message %s: %s",
                ctx.current_message.message_id,
                state,
            )
            fetched_task = await self.remote_task_reader.get_task_from_agent(
                ctx.agent_card, result.task_id
            )
            if fetched_task is not None:
                fetched_public_text = extract_public_completed_status_text(fetched_task)
                if fetched_public_text:
                    streaming_state.public_message_text = fetched_public_text
                await self._handle_a2a_response_for_room(
                    ctx.current_message, fetched_task
                )
                public_fetched_task = self._public_terminal_output_task_model(
                    fetched_task
                )
                fetched_text = (
                    extract_text_from_artifacts(public_fetched_task.artifacts)
                    if public_fetched_task.artifacts
                    else None
                )
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

        if ctx.send_sse and a2a_status_message_text:
            if is_interactive_state(state):
                if interactive_status_context is not None:
                    interactive_status_context["status_message"] = (
                        self._public_interactive_status_message(
                            ctx, a2a_status_message_text
                        )
                    )
            await self.tsm.notify_task(
                ctx,
                state,
                status_message=self._public_interactive_status_message(
                    ctx, a2a_status_message_text
                ),
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

        if not artifact_result:
            return

        from common.utils.a2a_helpers import extract_parts

        extracted = extract_parts(getattr(artifact_result, "parts", None) or [])
        if extracted.text:
            if append:
                streaming_state.full_response_text += extracted.text
            else:
                streaming_state.full_response_text = extracted.text

        if extracted.has_non_text:
            streaming_state.non_text_parts.extend(extracted.file_parts)
            streaming_state.non_text_parts.extend(extracted.data_parts)

        if ctx.send_sse and (append or last_chunk):
            logger.debug(
                "DirectTransport: Deferring streamed artifact update for %s "
                "(append=%s, last_chunk=%s) until terminal finalization",
                ctx.current_message.message_id,
                append,
                last_chunk,
            )

    @staticmethod
    def _public_stream_artifact_parts(parts: list[dict]) -> list[dict]:
        public_parts: list[dict] = []
        for part in parts:
            public_part = public_part_data(part)
            payload = (
                public_part.get("root")
                if isinstance(public_part.get("root"), dict)
                else public_part
            )
            file_payload = payload.get("file") if isinstance(payload, dict) else None
            if isinstance(file_payload, dict) and not file_payload.get("uri"):
                continue
            public_parts.append(public_part)
        return public_parts

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

        # Materialize inline file parts as a task artifact before public
        # projection and persistence / notification, so that the DB
        # always contains the multimodal data and clients that reconnect
        # after missing the real-time SSE can still hydrate them.
        if streaming_state.non_text_parts:
            new_total = await self._materialize_streaming_file_parts(
                streaming_state.non_text_parts,
                ctx.room_id,
                ctx.current_message.message_id,
                converted_so_far=streaming_state.inline_conversion_count,
                report=streaming_state.materialization_report,
            )
            streaming_state.inline_conversion_count = new_total
            if task:
                self._materialize_non_text_parts_as_artifact(
                    task,
                    streaming_state.non_text_parts,
                )

        delivery_failed = bool(
            task
            and output_delivery_failed(
                task.artifacts,
                streaming_state.materialization_report,
                text=(
                    streaming_state.full_response_text
                    or streaming_state.public_message_text
                ),
            )
        )
        if task and delivery_failed:
            mark_task_output_delivery_failed(task)
            ctx.current_message.message_content.message_text = (
                OUTPUT_DELIVERY_FAILURE_MESSAGE
            )
            ctx.current_message.message_content.message_task = (
                self._public_terminal_output_task_model(task)
            )
            await self.tsm.persist_message(ctx.current_message)
            await self._emit_terminal(
                ctx,
                CommonTaskState.COMPLETED,
                parts=streaming_state.non_text_parts,
            )
            return ProcessingStatus.FAILED, OUTPUT_DELIVERY_FAILURE_MESSAGE

        if task:
            ctx.current_message.message_content.message_task = self._public_task_model(
                task
            )
            task = get_task(ctx.current_message)

        if task and not already_terminal:
            if streaming_state.stream_finalized:
                final_st = streaming_state.final_state or CommonTaskState.COMPLETED
                if is_failure_state(final_st):
                    ctx.current_message.message_content.message_text = (
                        _PUBLIC_AGENT_FAILURE_MESSAGE
                    )
                    await self.tsm.transition_task(
                        ctx.current_message,
                        final_st,
                        error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                        persist=True,
                    )
                    await self._emit_terminal(
                        ctx,
                        final_st,
                        error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                    )
                    return ProcessingStatus.FAILED, _PUBLIC_AGENT_FAILURE_MESSAGE
                elif final_st in INTERACTIVE_STATES:
                    await self.tsm.transition_task(
                        ctx.current_message, final_st, persist=True
                    )
                    await self._emit_terminal(ctx, final_st)
                    return ProcessingStatus.SUCCESS, streaming_state.full_response_text
                else:
                    if streaming_state.full_response_text:
                        self._materialize_text_as_response_artifact(
                            task,
                            streaming_state.full_response_text,
                            artifact_id=f"{ctx.current_message.message_id}-final",
                        )
                    await self.tsm.transition_task(
                        ctx.current_message, CommonTaskState.COMPLETED, persist=False
                    )
                    task = get_task(ctx.current_message)
                    if task:
                        self._force_terminal_state_for_projection(
                            task, CommonTaskState.COMPLETED
                        )
                        ctx.current_message.message_content.message_task = (
                            self._public_terminal_output_task_model(task)
                        )
                    await self.tsm.persist_message(ctx.current_message)
                    await self._emit_terminal(
                        ctx,
                        CommonTaskState.COMPLETED,
                        public_text=streaming_state.public_message_text,
                    )
            elif streaming_state.full_response_text:
                self._materialize_text_as_response_artifact(
                    task,
                    streaming_state.full_response_text,
                    artifact_id=f"{ctx.current_message.message_id}-final",
                )
                await self.tsm.transition_task(
                    ctx.current_message,
                    CommonTaskState.COMPLETED,
                    persist=False,
                )
                task = get_task(ctx.current_message)
                if task:
                    self._force_terminal_state_for_projection(
                        task, CommonTaskState.COMPLETED
                    )
                    ctx.current_message.message_content.message_task = (
                        self._public_terminal_output_task_model(task)
                    )
                await self.tsm.persist_message(ctx.current_message)
                await self._emit_terminal(
                    ctx,
                    CommonTaskState.COMPLETED,
                    public_text=streaming_state.public_message_text,
                )
            else:
                logger.warning(
                    "DirectTransport: Stream ended without terminal status or content for %s",
                    ctx.current_message.message_id,
                )
                await self.tsm.transition_task(
                    ctx.current_message,
                    CommonTaskState.FAILED,
                    error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                    persist=True,
                )
                await self._emit_terminal(
                    ctx,
                    CommonTaskState.FAILED,
                    error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                )
                return ProcessingStatus.FAILED, _PUBLIC_AGENT_FAILURE_MESSAGE

        if already_terminal:
            final_state = task.status.state
            final_error = None
            if is_failure_state(final_state):
                final_error = _safe_terminal_error(final_state)

            if is_failure_state(final_state):
                ctx.current_message.message_content.message_text = (
                    final_error or _PUBLIC_AGENT_FAILURE_MESSAGE
                )
            elif (
                state_str(final_state) == CommonTaskState.COMPLETED.value
                and streaming_state.full_response_text
            ):
                self._materialize_text_as_response_artifact(
                    task,
                    streaming_state.full_response_text,
                    artifact_id=f"{ctx.current_message.message_id}-final",
                )
            if task:
                ctx.current_message.message_content.message_task = (
                    self._public_terminal_output_task_model(task)
                )
            await self.tsm.persist_message(ctx.current_message)
            await self._emit_terminal(
                ctx,
                final_state,
                error=final_error,
                public_text=streaming_state.public_message_text,
            )

            if is_failure_state(final_state):
                return (
                    ProcessingStatus.FAILED,
                    final_error or _PUBLIC_AGENT_FAILURE_MESSAGE,
                )

        # Non-text parts are already delivered via task_update SSE (which
        # carries the parts field).  A separate send_agent_response here
        # would create a duplicate entity on the frontend with a different
        # message_id, causing the agent response to render twice.

        return ProcessingStatus.SUCCESS, (
            streaming_state.full_response_text
            or streaming_state.public_message_text
            or ""
        )

    async def _handle_a2a_response_for_room(
        self, room_agent_message: RoomAgentMessage, message_data: Any | None
    ) -> bool:
        if message_data is None:
            logger.error("DirectTransport: process_a2a_response returned None")
            return False

        if getattr(message_data, "kind", None) == "task":
            public_message_text = extract_public_completed_status_text(message_data)
            if public_message_text and room_agent_message.message_content:
                room_agent_message.message_content.message_text = public_message_text
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
                    converted = self._public_task_model(message_data)
                    if converted.artifacts:
                        existing_task.artifacts = converted.artifacts
                    room_agent_message.message_content.message_task = (
                        self._public_task_model(existing_task)
                    )
                else:
                    room_agent_message.message_content.message_task = (
                        self._public_task_model(message_data)
                    )
            else:
                room_agent_message.message_content.message_task = (
                    self._public_task_model(message_data)
                )
            return await self.tsm.persist_message(room_agent_message)

        if getattr(message_data, "kind", None) == "message":
            task = get_task(room_agent_message)
            if task:
                public_task = self._public_task_model(task)
                if (
                    self._is_agent_message(message_data)
                    and public_task.status
                    and state_str(public_task.status.state)
                    == CommonTaskState.COMPLETED.value
                ):
                    self._materialize_message_as_response_artifact(
                        public_task,
                        message_data,
                        artifact_id=f"{room_agent_message.message_id}-message",
                    )
                    public_task = self._public_terminal_output_task_model(public_task)
                room_agent_message.message_content.message_task = public_task
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
    def _resolve_task_response_status(response: dict[str, Any]) -> CommonTaskState:
        """Derive task state from interactive flags, then status, then working."""
        if response.get("requires_auth"):
            return CommonTaskState.AUTH_REQUIRED
        if response.get("requires_policy") or response.get("policy_required"):
            return CommonTaskState.POLICY_REQUIRED
        if response.get("requires_input"):
            return CommonTaskState.INPUT_REQUIRED
        raw_status = response.get("status")
        if raw_status is not None:
            return (
                CommonTaskState(raw_status)
                if isinstance(raw_status, str)
                else raw_status
            )
        return CommonTaskState.WORKING

    @staticmethod
    def _agent_card_url(agent_card: Any) -> str | None:
        """Read the declared agent URL without applying Docker host rewrites."""
        if agent_card is None:
            return None
        if isinstance(agent_card, dict):
            raw_url = agent_card.get("url")
        else:
            raw_url = getattr(agent_card, "url", None)
        if raw_url is None:
            return None
        url = str(raw_url).strip()
        return url or None

    @staticmethod
    def _parse_sync_fallback_response(
        raw_response,
        message_id: str,
    ) -> dict[str, Any]:
        """Parse a raw ``send_message_sync`` response into the dict format
        expected by ``handle_sync_response``."""
        if raw_response is None:
            return {"type": "message", "message_id": message_id, "content": ""}

        if not isinstance(raw_response, dict):
            raise A2AServiceError("A2A sync response must be a normalized dict")
        if raw_response.get("kind") == "error":
            error_payload = raw_response.get("error")
            error_message = (
                error_payload.get("message")
                if isinstance(error_payload, dict)
                else str(error_payload)
            )
            raise A2AServiceError(error_message)
        result = DirectTransport._coerce_stream_result(raw_response)

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
            if is_interactive_state(state):
                parsed["requires_input"] = (
                    state_value == CommonTaskState.INPUT_REQUIRED.value
                )
                parsed["requires_auth"] = (
                    state_value == CommonTaskState.AUTH_REQUIRED.value
                )
            if is_terminal_state(state) and result.artifacts:
                from common.utils.a2a_helpers import (
                    extract_parts_from_artifacts as _epfa,
                )

                public_task = DirectTransport._public_terminal_output_task_model(result)
                public_state = public_task.status.state
                public_state_value = state_str(public_state)
                if public_state_value == CommonTaskState.COMPLETED.value:
                    parsed["type"] = "message"
                    parsed["status"] = public_state_value
                    parsed["content"] = None
                    if public_task.artifacts:
                        extracted_task = _epfa(public_task.artifacts)
                        parsed["content"] = extracted_task.text or None
                        if extracted_task.has_non_text:
                            parsed["parts"] = (
                                extracted_task.file_parts + extracted_task.data_parts
                            )
                elif is_failure_state(public_state):
                    parsed = {
                        "type": "message",
                        "message_id": message_id,
                        "content": None,
                        "status": public_state_value,
                        "error": _safe_terminal_error(public_state),
                    }
            return parsed

        return {"type": "message", "message_id": message_id, "content": ""}

    async def handle_sync_response(
        self,
        current_message: RoomAgentMessage,
        agent_card: Any,
        prepared_message: Any,
        room_id: str,
        _user_id: str | None,
        *,
        user_message_id: str | None = None,
        token: CancellationToken | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        interactive_status_context: dict[str, str | None] | None = None,
    ) -> tuple[bool, str | None, str | None, str | None]:
        """Handle synchronous (non-streaming) response from an agent.

        Returns:
            Tuple of (success, response_text, paused_message_id, task_id).
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
            await self.delivery.send_task_submitted(
                room_id=room_id,
                message_id=current_message.message_id,
                task_id=SyntheticTaskId.DEGRADED,
                agent_name=agent_card.name,
                agent_id=current_message.agent_id,
                status=CommonTaskState.WORKING,
                related_message_id=current_message.related_message_id,
                created_at=(
                    current_message.message_created_at.isoformat()
                    if current_message.message_created_at
                    else None
                ),
                step_number=step_number,
                total_steps=total_steps,
                task_content=self._public_task_label(current_message, agent_card.name),
                client_request_id=current_message.client_request_id,
            )

        message_id = current_message.message_id

        # Check for cancellation before the (potentially long) sync agent call
        if token and token.is_cancelled:
            logger.info(
                "DirectTransport: Sync call cancelled before agent call for %s",
                message_id,
            )
            await self.tsm.transition_task(
                current_message,
                CommonTaskState.CANCELED,
                persist=True,
            )
            if task_info:
                await self._emit_terminal(ctx, CommonTaskState.CANCELED)
            return False, "", None, None

        # Call the agent
        try:
            if task_info:
                agent_coro = self.a2a_transport.send_message_to_tracked_agent(
                    agent_card=agent_card,
                    message=prepared_message,
                    message_id=message_id,
                    webhook_token=task_info["webhook_token"],
                    context_id=task_info["context_id"],
                    agent_id=current_message.agent_id,
                )
            else:

                async def _sync_fallback():
                    raw = await self.a2a_transport.send_message_sync(
                        agent_card=agent_card,
                        message=prepared_message,
                        agent_id=current_message.agent_id,
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
                current_message,
                CommonTaskState.CANCELED,
                persist=True,
            )
            if task_info:
                await self._emit_terminal(ctx, CommonTaskState.CANCELED)
            await self._try_cancel_remote_task(current_message, agent_card)
            return False, "", None, None
        except Exception as exc:
            logger.error("Agent error: %s", exc, exc_info=True)
            await self.tsm.transition_task(
                current_message,
                CommonTaskState.FAILED,
                error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                persist=True,
            )
            if task_info:
                await self._emit_terminal(
                    ctx,
                    CommonTaskState.FAILED,
                    error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                )
            await self.delivery.send_error(
                room_id,
                _PUBLIC_AGENT_FAILURE_MESSAGE,
                message_id=current_message.message_id,
            )

            # Record capability issue for the agent
            if self.capability_issue_service is not None:
                try:
                    await self.capability_issue_service.record_issue(
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

            return False, _PUBLIC_AGENT_FAILURE_MESSAGE, None, None

        # Post-call cancellation check
        if token and token.is_cancelled:
            logger.info(
                "DirectTransport: Sync call cancelled after agent response for %s",
                message_id,
            )
            await self.tsm.transition_task(
                current_message,
                CommonTaskState.CANCELED,
                persist=True,
            )
            if task_info:
                await self._emit_terminal(ctx, CommonTaskState.CANCELED)
            await self._try_cancel_remote_task(current_message, agent_card)
            return False, "", None, None

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
            interactive_status_context=interactive_status_context,
        )

    async def _process_sync_response(
        self,
        response: dict[str, Any],
        current_message: RoomAgentMessage,
        agent_card: Any,
        room_id: str,
        message_id: str,
        task_info: dict[str, Any] | None,
        ctx: ProcessingContext,
        token: CancellationToken | None,
        *,
        step_number: int | None = None,
        total_steps: int | None = None,
        interactive_status_context: dict[str, str | None] | None = None,
    ) -> tuple[bool, str | None, str | None, str | None]:
        """
        Process the parsed sync response (message or task type).
        Returns: Tuple of (success, response_text, paused_message_id, agent_task_id).
        """
        # Handle "message" response (fast path)
        if response.get("type") == "message":
            full_response_text = response.get("content") or ""
            public_message_text = response.get("public_message_text")
            if not isinstance(public_message_text, str):
                public_message_text = None
            error_text = response.get("error")
            error_code = response.get("error_code")
            non_text_parts = response.get("parts")

            # Respect the actual terminal state from the response.
            # a2a_transport returns type="message" for ALL terminal task states
            # (completed, failed, canceled, rejected), not just completed.
            actual_state_str = response.get("status")
            if actual_state_str:
                actual_state = CommonTaskState(actual_state_str)
            else:
                actual_state = CommonTaskState.COMPLETED
            public_error_text = (
                OUTPUT_DELIVERY_FAILURE_MESSAGE
                if error_code == OUTPUT_DELIVERY_FAILURE_CODE
                else error_text
            )
            if error_text and is_failure_state(actual_state):
                safe_errors = {
                    _PUBLIC_AGENT_FAILURE_MESSAGE,
                    OUTPUT_DELIVERY_FAILURE_MESSAGE,
                    *_PUBLIC_TASK_TERMINAL_ERRORS.values(),
                }
                if error_text not in safe_errors:
                    logger.error(
                        "DirectTransport: Agent returned failed sync response: %s",
                        error_text,
                    )
                    public_error_text = _PUBLIC_AGENT_FAILURE_MESSAGE

            task = get_task(current_message)
            if task is not None and error_code == OUTPUT_DELIVERY_FAILURE_CODE:
                metadata = dict(getattr(task, "metadata", None) or {})
                metadata.update(
                    {
                        "output_failure_code": OUTPUT_DELIVERY_FAILURE_CODE,
                        "remote_task_state": CommonTaskState.COMPLETED.value,
                    }
                )
                task.metadata = metadata

            if public_message_text or full_response_text:
                current_message.message_content.message_text = (
                    public_message_text or full_response_text
                )
            elif public_error_text:
                current_message.message_content.message_text = public_error_text

            # Materialize inline file parts before persistence.
            materialization_report = new_materialization_report()
            if non_text_parts:
                await self._materialize_streaming_file_parts(
                    non_text_parts,
                    room_id,
                    message_id,
                    report=materialization_report,
                )
                non_text_parts = [
                    public_part
                    for part in non_text_parts
                    if (public_part := public_part_data(part)) is not None
                ]
                task = get_task(current_message)
                if task:
                    self._materialize_non_text_parts_as_artifact(
                        task,
                        non_text_parts,
                    )
                    if (
                        actual_state == CommonTaskState.COMPLETED
                        and output_delivery_failed(
                            task.artifacts,
                            materialization_report,
                            text=(public_message_text or full_response_text),
                        )
                    ):
                        mark_task_output_delivery_failed(task)
                        actual_state = CommonTaskState.FAILED
                        public_error_text = OUTPUT_DELIVERY_FAILURE_MESSAGE
                        error_code = OUTPUT_DELIVERY_FAILURE_CODE
                    current_message.message_content.message_task = (
                        self._public_terminal_output_task_model(task)
                        if is_terminal_state(task.status.state)
                        else self._public_task_model(task)
                    )

            # On the tracked path, a2a_transport already persisted the real
            # task (with artifacts) to DB via partial $set.  The in-memory
            # current_message still holds the stale placeholder — a full-
            # document persist here would overwrite the real data.  On the
            # degraded path (task_info is None), no prior write happened so
            # persist is needed.
            #
            # If the tracked write was not confirmed (persisted=False), fall
            # back to a full-document persist so the terminal state is not
            # lost — a stale-placeholder overwrite is better than leaving
            # the task stuck in a non-terminal state after a refresh.
            if task_info:
                should_persist = not response.get("persisted", True)
                if should_persist:
                    logger.warning(
                        "DirectTransport: tracked terminal task was not confirmed "
                        "persisted for %s — falling back to full persist",
                        message_id,
                    )
            else:
                should_persist = True
            if actual_state == CommonTaskState.COMPLETED and full_response_text:
                task = get_task(current_message)
                if task:
                    self._materialize_text_as_response_artifact(
                        task,
                        full_response_text,
                        artifact_id=f"{message_id}-final",
                    )
                    self._force_terminal_state_for_projection(
                        task, CommonTaskState.COMPLETED
                    )
                    current_message.message_content.message_task = (
                        self._public_terminal_output_task_model(task)
                    )
            await self.tsm.transition_task(
                current_message,
                actual_state,
                persist=should_persist,
            )
            await self._emit_terminal(
                ctx,
                actual_state,
                error=public_error_text,
                parts=non_text_parts if non_text_parts else None,
                public_text=public_message_text,
            )

            if not task_info:
                logger.info(
                    "DirectTransport: Degraded mode — sending task_update directly for %s",
                    message_id,
                )
                await self.delivery.send_task_update(
                    room_id=room_id,
                    message_id=message_id,
                    status=actual_state,
                    content=(
                        full_response_text
                        if actual_state == CommonTaskState.COMPLETED
                        else (full_response_text or public_error_text)
                    ),
                    error=public_error_text,
                    agent_name=agent_card.name if agent_card else None,
                    agent_id=current_message.agent_id,
                    step_number=step_number,
                    total_steps=total_steps,
                    parts=non_text_parts if non_text_parts else None,
                    client_request_id=current_message.client_request_id,
                )

            # P1: Non-completed terminal states are dispatch failures so
            # QueueExecutor / SupervisorExecutor treat them correctly.
            is_success = actual_state == CommonTaskState.COMPLETED
            return is_success, full_response_text or public_error_text, None, None

        # Handle "task" response (async path)
        if response.get("type") == "task":
            status = self._resolve_task_response_status(response)
            raw_status_message = response.get("message")
            if is_interactive_state(status):
                if interactive_status_context is not None:
                    interactive_status_context["status_message"] = (
                        self._public_interactive_status_message(
                            ctx,
                            raw_status_message
                            if isinstance(raw_status_message, str)
                            else None,
                        )
                    )
            if task_info:
                await self.tsm.notify_task(
                    ctx,
                    status,
                    requires_input=(
                        response.get("requires_input", False)
                        or status == CommonTaskState.INPUT_REQUIRED
                    ),
                    requires_auth=(
                        response.get("requires_auth", False)
                        or status == CommonTaskState.AUTH_REQUIRED
                    ),
                    status_message=(
                        self._public_interactive_status_message(
                            ctx,
                            raw_status_message
                            if isinstance(raw_status_message, str)
                            else None,
                        )
                        if raw_status_message
                        else None
                    ),
                )

            # Interactive states (input-required / auth-required) are already
            # final for this dispatch cycle regardless of push capability.
            # Return immediately so QueueExecutor sees AWAITING_INPUT.
            if is_interactive_state(status):
                task_obj = get_task(current_message)
                if task_obj and task_obj.status:
                    task_obj.status.state = coerce_task_state(status)
                    task_obj.status.message = None
                if not task_info:
                    current_message.agent_url = (
                        current_message.agent_url or self._agent_card_url(agent_card)
                    )
                    await self.tsm.persist_message(current_message)
                return True, None, message_id, response.get("task_id")

            if self.a2a_transport.has_push_notification_capability(agent_card):
                return True, None, message_id, None

            # Non-push agent: poll for completion
            agent_task_id = response.get("task_id")
            if not agent_task_id:
                logger.warning(
                    "DirectTransport: Non-push agent task response without task_id"
                )
                return True, None, None, None

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
                    current_message,
                    CommonTaskState.CANCELED,
                    persist=True,
                )
                if task_info:
                    await self._emit_terminal(ctx, CommonTaskState.CANCELED)
                await self._try_cancel_remote_task(current_message, agent_card)
                return False, "", None, None

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
                    interactive_status_context=interactive_status_context,
                )
            else:
                logger.warning(
                    "DirectTransport: Polling timed out for task %s",
                    message_id,
                )
                await self.tsm.transition_task(
                    current_message,
                    CommonTaskState.FAILED,
                    error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                    persist=True,
                )
                await self._emit_terminal(
                    ctx,
                    CommonTaskState.FAILED,
                    error=_PUBLIC_AGENT_FAILURE_MESSAGE,
                )
                return False, _PUBLIC_AGENT_FAILURE_MESSAGE, None, None

        logger.error(
            "unexpected_task_tracking_response",
            extra={"response_type": type(response).__name__},
        )
        return False, "", None, None

    async def _finalize_polled_task(
        self,
        completed_task: Any,
        current_message: RoomAgentMessage,
        agent_card: Any,
        room_id: str,
        message_id: str,
        task_info: dict[str, Any] | None,
        ctx: ProcessingContext,
        *,
        step_number: int | None = None,
        total_steps: int | None = None,
        interactive_status_context: dict[str, str | None] | None = None,
    ) -> tuple[bool, str | None, str | None, str | None]:
        """Finalize a polled task that reached a terminal state."""
        public_message_text = extract_public_completed_status_text(completed_task)
        report = None
        if completed_task.artifacts:
            from common.utils.a2a_helpers import materialize_artifacts

            report = new_materialization_report()
            try:
                await materialize_artifacts(
                    completed_task.artifacts,
                    room_id,
                    message_id,
                    report=report,
                )
            except Exception as exc:
                replaced = mark_unresolved_file_parts_unavailable(
                    completed_task.artifacts
                )
                report["attempted"] = max(
                    int(report.get("attempted", 0)),
                    replaced,
                )
                report["unavailable"] = max(
                    int(report.get("unavailable", 0)),
                    replaced,
                )
                report.setdefault("failures", []).append(
                    {
                        "code": "materialization_failed",
                        "source": "unknown",
                        "exception_type": type(exc).__name__,
                    }
                )
                logger.warning(
                    "polled_artifact_materialization_failed",
                    extra={
                        "message_id": message_id,
                        "failure_code": "materialization_failed",
                        "exception_type": type(exc).__name__,
                    },
                )
        if completed_task.status.state == CommonTaskState.COMPLETED and (
            output_delivery_failed(
                completed_task.artifacts,
                report,
                text=public_message_text,
            )
        ):
            mark_task_output_delivery_failed(completed_task)

        projected_task_data = public_persisted_task_data(completed_task)
        public_task = Task.model_validate(projected_task_data)
        state = public_task.status.state
        state_value = state_str(state)
        if current_message.message_content:
            current_message.message_content.message_task = public_task
            if public_message_text:
                current_message.message_content.message_text = public_message_text

        # --- Interactive states (input_required / auth_required) ---
        # The polled agent needs user interaction.  Persist the task state
        # and return paused_message_id so the dispatch method detects it
        # and triggers HITL.
        if state in INTERACTIVE_STATES:
            raw_status_message = (
                get_text_from_message(completed_task.status.message)
                if completed_task.status.message
                else None
            )
            if interactive_status_context is not None:
                interactive_status_context["status_message"] = (
                    self._public_interactive_status_message(ctx, raw_status_message)
                )
            if task_info:
                await self._task_updater.update_task_on_message(
                    message_id,
                    projected_task_data,
                )
            logger.info(
                "DirectTransport: Polled task %s reached interactive state %s — "
                "returning paused_message_id for HITL",
                message_id,
                state_value,
            )
            return True, None, message_id, public_task.id

        final_content = None
        final_error = None
        if state_value == CommonTaskState.COMPLETED.value and public_task.artifacts:
            final_content = extract_text_from_artifacts(public_task.artifacts)
        elif is_failure_state(state):
            raw_error = extract_error_message(completed_task)
            if raw_error:
                logger.info(
                    "DirectTransport: raw polled task failure retained internally for %s",
                    message_id,
                )
            metadata = public_task.metadata or {}
            final_error = (
                OUTPUT_DELIVERY_FAILURE_MESSAGE
                if metadata.get("output_failure_code") == OUTPUT_DELIVERY_FAILURE_CODE
                else _safe_terminal_error(state)
            )

        if task_info:
            # TODO: Phase 5 -- migrate to incremental update_task_state_on_message.
            # This is the last caller of the full-document update_task_on_message;
            # the polled task has a complete Task model that doesn't yet fit the
            # incremental pattern.
            await self._task_updater.update_task_on_message(
                message_id,
                projected_task_data,
                message_text=public_message_text or final_content or None,
            )

        if task_info:
            await self._emit_terminal(
                ctx,
                state,
                error=final_error,
                public_text=public_message_text,
            )
        else:
            logger.info(
                "DirectTransport: Degraded mode — sending polled task_update for %s",
                message_id,
            )
            await self.delivery.send_task_update(
                room_id=room_id,
                message_id=message_id,
                status=state,
                content=final_content or public_message_text,
                error=final_error,
                agent_name=agent_card.name if agent_card else None,
                agent_id=current_message.agent_id,
                step_number=step_number,
                total_steps=total_steps,
                client_request_id=current_message.client_request_id,
            )
        return (
            state_str(state) == CommonTaskState.COMPLETED.value,
            (
                (final_content or public_message_text)
                if final_error is None
                else final_error
            ),
            None,
            None,
        )


def _safe_terminal_error(state: Any) -> str:
    return _PUBLIC_TASK_TERMINAL_ERRORS.get(
        state_str(state),
        _PUBLIC_AGENT_FAILURE_MESSAGE,
    )
