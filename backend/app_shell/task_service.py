from typing import Any

from a2a_adapter.remote_task import fetch_remote_task
from common.types import AgentCard, Task
from common.utils.logger import get_logger

logger = get_logger(__name__)


class TaskService:
    async def get_task_from_agent(
        self,
        agent_card: AgentCard | dict[str, Any],
        task_id: str,
    ) -> Task | None:
        """Get task from agent via the SDK-confined A2A adapter."""
        return await fetch_remote_task(agent_card, task_id)


task_service = TaskService()
