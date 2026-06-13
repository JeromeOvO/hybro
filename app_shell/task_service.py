from typing import Any

from a2a_adapter.remote_task import fetch_remote_task
from common.types import Task
from common.utils.logger import get_logger

logger = get_logger(__name__)


class TaskService:
    async def get_task_from_agent(self, agent_card: Any, task_id: str) -> Task | None:
        """Get task from agent via the SDK-confined A2A adapter."""
        card_data = (
            agent_card.model_dump(mode="json")
            if hasattr(agent_card, "model_dump")
            else agent_card
            if isinstance(agent_card, dict)
            else {}
        )
        return await fetch_remote_task(card_data, task_id)


task_service = TaskService()
