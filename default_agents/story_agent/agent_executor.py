from typing import override

from a2a.helpers import (
    new_task_from_user_message,
    new_text_artifact_update_event,
    new_text_status_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
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

        task = new_task_from_user_message(context.message)

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

        # The task must be published before any task-scoped update event.
        await event_queue.enqueue_event(task)

        if final_text:
            await self._emit_text(
                task,
                event_queue,
                final_text,
            )

        if final_text.strip():
            status_event = new_text_status_update_event(
                task_id=task.id,
                context_id=task.context_id,
                state=TaskState.TASK_STATE_COMPLETED,
                text=final_text,
            )
        else:
            status_event = TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        await event_queue.enqueue_event(status_event)

    @staticmethod
    async def _emit_text(
        task: Task,
        event_queue: EventQueue,
        content: str,
    ) -> None:
        await event_queue.enqueue_event(
            new_text_artifact_update_event(
                task_id=task.id,
                context_id=task.context_id,
                name="current_result",
                text=content,
                append=False,
                last_chunk=True,
                artifact_id=f"{task.id}-current-result",
            )
        )

    @override
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")
