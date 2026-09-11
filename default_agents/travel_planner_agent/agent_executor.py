from typing import override

from a2a.helpers import (
    get_message_text,
    new_task,
    new_text_artifact_update_event,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message,
    Role,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import UnsupportedOperationError
from agent import TravelPlannerAgent
from google.protobuf.struct_pb2 import Struct
from interaction_metadata import build_input_required_metadata


def _build_history_from_task(context: RequestContext) -> list[dict[str, str]]:
    """Build conversation history from the task the request continues.

    The current user message is handed over separately as the query, so the
    history only covers the turns already persisted on the task.  The agent's
    last status message (e.g. a clarification question) chronologically came
    after those turns and before the current query.
    """
    history: list[dict[str, str]] = []
    task = context.current_task
    if task is None:
        return history

    for msg in task.history:
        text = get_message_text(msg)
        if not text.strip():
            continue
        role = "user" if msg.role == Role.ROLE_USER else "agent"
        history.append({"role": role, "text": text})

    if task.status.HasField("message"):
        status_message: Message = task.status.message
        text = get_message_text(status_message)
        if text.strip():
            role = "user" if status_message.role == Role.ROLE_USER else "agent"
            history.append({"role": role, "text": text})

    return history


def _build_status_message(
    task_id: str,
    context_id: str,
    text: str,
    *,
    input_required: bool,
) -> Message:
    """Build the final status message, carrying typed HITL metadata."""
    message = new_text_message(text, context_id=context_id, task_id=task_id)
    if input_required:
        metadata = Struct()
        metadata.update(build_input_required_metadata(task_id, text))
        message.metadata.CopyFrom(metadata)
    return message


class TravelPlannerAgentExecutor(AgentExecutor):
    """travel planner AgentExecutor Example."""

    def __init__(self):
        self.agent = TravelPlannerAgent()

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        if not context.message:
            raise Exception("No message provided")

        query = context.get_user_input()
        task_id = context.task_id
        context_id = context.context_id
        history = _build_history_from_task(context)

        # The SDK requires a Task before any task-scoped update event.  A
        # continuation already has a persisted Task, and re-sending one would
        # make the SDK drop the answering user message from the task history.
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task(
                    task_id=task_id,
                    context_id=context_id,
                    state=TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message],
                )
            )

        artifact_id = f"{task_id}-current-result"
        chunks: list[str] = []
        is_input_required = False
        async for event in self.agent.stream(query, history=history):
            if event.get("status") == "input_required":
                is_input_required = True
            chunk = event.get("content") or ""
            if not isinstance(chunk, str):
                chunk = (
                    "".join(str(part) for part in chunk)
                    if isinstance(chunk, list)
                    else str(chunk)
                )
            if chunk:
                chunks.append(chunk)
            if event.get("done"):
                break

        final_text = "".join(chunks)
        if final_text and not is_input_required:
            await self._emit_text(
                event_queue,
                task_id,
                context_id,
                artifact_id,
                final_text,
            )

        status_message = None
        if final_text.strip():
            status_message = _build_status_message(
                task_id,
                context_id,
                final_text,
                input_required=is_input_required,
            )
        final_state = (
            TaskState.TASK_STATE_INPUT_REQUIRED
            if is_input_required
            else TaskState.TASK_STATE_COMPLETED
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=final_state,
                    message=status_message,
                ),
            )
        )

    @staticmethod
    async def _emit_text(
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        artifact_id: str,
        content: str,
    ) -> None:
        await event_queue.enqueue_event(
            new_text_artifact_update_event(
                task_id=task_id,
                context_id=context_id,
                name="current_result",
                text=content,
                append=False,
                last_chunk=True,
                artifact_id=artifact_id,
            )
        )

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # This agent never supported cancellation; keep raising the SDK's
        # "not supported" error instead of silently accepting the request.
        raise UnsupportedOperationError("cancel not supported")
