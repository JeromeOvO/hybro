import asyncio

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    FilePart,
    FileWithBytes,
    InternalError,
    InvalidParamsError,
    Part,
    Task,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import (
    completed_task,
    new_artifact,
)
from a2a.utils.errors import ServerError
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
            print(f'Generated image key: {image_key}')
        except Exception as e:
            print(f'Error invoking agent: {e}')
            raise ServerError(
                error=InternalError(message=f'Error invoking agent: {e}')
            ) from e

        data = self.agent.get_image_data(
            session_id=context.context_id, image_key=image_key
        )

        if data and not data.error:
            parts = [
                Part(
                    root=FilePart(
                        file=FileWithBytes(
                            bytes=data.bytes,
                            mime_type=data.mime_type,
                            name=data.id,
                        )
                    )
                )
            ]
        else:
            error_msg = (data.error if data else None) or 'Failed to generate image'
            parts = [
                Part(
                    root=TextPart(text=error_msg),
                )
            ]
        await event_queue.enqueue_event(
            completed_task(
                context.task_id,
                context.context_id,
                [new_artifact(parts, f'image_{context.task_id}')],
                [context.message],
            )
        )

    async def cancel(
        self, request: RequestContext, event_queue: EventQueue
    ) -> Task | None:
        raise ServerError(error=UnsupportedOperationError())
