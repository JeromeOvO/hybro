from typing import override

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import new_text_artifact
from agent import TravelPlannerAgent


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

        artifact_id = f"{context.task_id}-current-result"
        chunks: list[str] = []

        async for event in self.agent.stream(query):
            if event["content"]:
                chunks.append(event["content"])
            if event["done"]:
                break

        if chunks:
            await self._emit_text_chunk(
                context,
                event_queue,
                artifact_id,
                "".join(chunks),
                append=False,
                last_chunk=True,
            )

        status = TaskStatusUpdateEvent(
            contextId=context.context_id,  # type: ignore
            taskId=context.task_id,  # type: ignore
            status=TaskStatus(state=TaskState.completed),
            final=True,
        )
        await event_queue.enqueue_event(status)

    @staticmethod
    async def _emit_text_chunk(
        context: RequestContext,
        event_queue: EventQueue,
        artifact_id: str,
        content: str,
        *,
        append: bool,
        last_chunk: bool,
    ) -> None:
        artifact = new_text_artifact(name="current_result", text=content)
        artifact.artifact_id = artifact_id
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                contextId=context.context_id,  # type: ignore
                taskId=context.task_id,  # type: ignore
                artifact=artifact,
                append=append,
                lastChunk=last_chunk,
            )
        )

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")
