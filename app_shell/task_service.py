from a2a.types import (
    AgentCard,
    GetTaskRequest,
    JSONRPCErrorResponse,
    Task,
    TaskQueryParams,
)

from common.utils.logger import get_logger
from app_shell.a2a_runtime import a2a_service

logger = get_logger(__name__)

class TaskService:
    def __init__(self):
        self.a2a_service = a2a_service

    async def get_task_from_agent(self, agent_card: AgentCard, task_id: str) -> Task | None:
        """Get task from agent via A2A client"""

        try:
            async with self.a2a_service.create_a2a_client(agent_card) as a2a_client:
                response = await a2a_client.get_task(GetTaskRequest(id=task_id, params=TaskQueryParams(id=task_id)))
                if not response or isinstance(response.root, JSONRPCErrorResponse):
                    logger.error(
                        f"Failed to get task from agent, error: {getattr(response.root, 'error', 'Unknown error')}"
                    )
                    return None
                return response.root.result
        except Exception as e:
            logger.error(f"Failed to get task from agent: {e}", exc_info=True)
            return None

task_service = TaskService()
