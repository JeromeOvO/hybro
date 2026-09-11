import asyncio
import base64

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers import new_artifact, new_task
from a2a.types import (
    InternalError,
    Part,
    Task,
    TaskState,
    UnsupportedOperationError,
)
from agent import ImageGenerationAgent


class ImageGenerationAgentExecutor(AgentExecutor):
    """Image generation AgentExecutor."""

    def __init__(self) -> None:
        self.agent = ImageGenerationAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        query = context.get_user_input()
        try:
            loop = asyncio.get_running_loop()
            image_key = await loop.run_in_executor(
                None, self.agent.invoke, query, context.context_id
            )
            print(f"Generated image key: {image_key}")
        except Exception as e:
            print(f"Error invoking agent: {e}")
            raise InternalError(
                message=f"Error invoking agent: {e}"
            ) from e

        data = self.agent.get_image_data(
            session_id=context.context_id, image_key=image_key
        )

        if data and not data.error:
            parts = [
                Part(
                    # The agent caches the image base64-encoded; Part.raw is
                    # raw bytes, which the SDK base64-encodes on the wire.
                    raw=base64.b64decode(data.bytes),
                    media_type=data.mime_type or "",
                    filename=data.id or "",
                )
            ]
        else:
            error_msg = (data.error if data else None) or "Failed to generate image"
            parts = [
                Part(text=error_msg),
            ]
        await event_queue.enqueue_event(
            new_task(
                context.task_id,
                context.context_id,
                TaskState.TASK_STATE_COMPLETED,
                artifacts=[new_artifact(parts, f"image_{context.task_id}")],
                history=[context.message],
            )
        )

    async def cancel(
        self, request: RequestContext, event_queue: EventQueue
    ) -> Task | None:
        raise UnsupportedOperationError()
