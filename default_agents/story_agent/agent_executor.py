from typing import override
import uuid

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    Message,
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
            raise Exception('No message provided')

        artifact_id = uuid.uuid4().hex
        final_text = ''
        async for event in self.agent.stream(query):
            chunk = event.get('content') or ''
            if not isinstance(chunk, str):
                chunk = ''.join(str(part) for part in chunk) if isinstance(
                    chunk, list
                ) else str(chunk)
            if chunk:
                final_text += chunk
            # Skip empty done sentinel so clients do not append a blank chunk.
            if chunk or not event.get('done'):
                artifact = new_text_artifact(
                    name='current_result',
                    text=chunk,
                )
                artifact.artifact_id = artifact_id
                message = TaskArtifactUpdateEvent(
                    contextId=context.context_id,  # type: ignore
                    taskId=context.task_id,  # type: ignore
                    artifact=artifact,
                    append=True,
                )
                await event_queue.enqueue_event(message)
            if event['done']:
                break

        # Hybro (and other A2A clients) extract public display text from the
        # completed status message. Artifact-only completion leaves the chat
        # bubble empty even when streamed text was received.
        status_message = None
        if final_text.strip():
            status_message = Message(
                messageId=uuid.uuid4().hex,
                role=Role.agent,
                parts=[Part(root=TextPart(text=final_text))],
            )
        status = TaskStatusUpdateEvent(
            contextId=context.context_id,  # type: ignore
            taskId=context.task_id,  # type: ignore
            status=TaskStatus(state=TaskState.completed, message=status_message),
            final=True,
        )
        await event_queue.enqueue_event(status)

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise Exception('cancel not supported')
