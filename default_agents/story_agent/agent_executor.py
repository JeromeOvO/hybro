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
from agent import StoryAgent


class StoryAgentExecutor(AgentExecutor):
    """story AgentExecutor Example."""

    def __init__(self):
        self.agent = StoryAgent()

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        query = context.get_user_input()
        if not context.message:
            raise Exception("No message provided")

        artifact_id = f"{context.task_id}-current-result"
        chunks: list[str] = []
        async for event in self.agent.stream(query):
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
        if final_text:
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
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                contextId=context.context_id,  # type: ignore
                taskId=context.task_id,  # type: ignore
                status=TaskStatus(
                    state=TaskState.completed,
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
