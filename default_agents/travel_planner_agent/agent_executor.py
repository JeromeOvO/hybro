import uuid
from typing import override

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from a2a.utils import new_text_artifact
from agent import TravelPlannerAgent


def _extract_text_from_message(msg: Message) -> str:
    """Extract text content from an A2A Message."""
    parts = []
    for part in msg.parts:
        inner = part.root if hasattr(part, "root") else part
        if hasattr(inner, "text"):
            parts.append(inner.text)
    return "\n".join(parts)


def _build_history_from_task(context: RequestContext) -> list[dict[str, str]]:
    """Build conversation history from the task's message history.

    Excludes the latest user message since that's passed separately as the
    current query.  Places the agent's last status message (e.g. a
    clarification question) chronologically after prior user messages but
    before the current query.
    """
    history: list[dict[str, str]] = []
    task = context.current_task
    if task is None:
        return history

    messages = list(task.history) if task.history else []

    # The SDK appends the current user message to history before calling
    # execute, so drop the trailing user message to avoid duplication
    # (it's passed separately as the current query).
    if messages and messages[-1].role == Role.user:
        messages = messages[:-1]

    for msg in messages:
        text = _extract_text_from_message(msg)
        if not text.strip():
            continue
        role = "user" if msg.role == Role.user else "agent"
        history.append({"role": role, "text": text})

    # Append the agent's last status message (the clarification question
    # from the previous turn) — chronologically it came after the messages
    # above and before the current user query.
    if task.status and task.status.message is not None:
        text = _extract_text_from_message(task.status.message)
        if text.strip():
            role = "user" if task.status.message.role == Role.user else "agent"
            history.append({"role": role, "text": text})

    return history


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
        query = context.get_user_input()
        if not context.message:
            raise Exception("No message provided")

        history = _build_history_from_task(context)

        artifact_id = f"{context.task_id}-current-result"
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
                context,
                event_queue,
                artifact_id,
                final_text,
            )

        status_message = None
        if final_text.strip():
            status_message = Message(
                messageId=uuid.uuid4().hex,
                role=Role.agent,
                parts=[Part(root=TextPart(text=final_text))],
            )
        final_state = (
            TaskState.input_required if is_input_required else TaskState.completed
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                contextId=context.context_id,  # type: ignore
                taskId=context.task_id,  # type: ignore
                status=TaskStatus(
                    state=final_state,
                    message=status_message,
                ),
                final=True,
            )
        )

    @staticmethod
    async def _emit_text(
        context: RequestContext,
        event_queue: EventQueue,
        artifact_id: str,
        content: str,
    ) -> None:
        artifact = new_text_artifact(name="current_result", text=content)
        artifact.artifact_id = artifact_id
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                contextId=context.context_id,  # type: ignore
                taskId=context.task_id,  # type: ignore
                artifact=artifact,
                append=False,
                lastChunk=True,
            )
        )

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")
