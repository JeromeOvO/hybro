from models.request import OrchestrationCenterRequest
from models.response import OrchestrationCenterResponse
from services.task_service import TaskService
from services.openai_service import OpenAIService

from a2a.types import TaskState

class OrchestrationCenter:

    def __init__(self):
        self.task_service = TaskService()
        self.openai_service = OpenAIService()

    async def decompose_task(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:

        """
        Decompose a root task into subtasks using OpenAI.
        
        Args:
            task_id: The ID of the root task to decompose
            
        Returns:
            List[str]: List of subtask IDs
            
        Raises:
            RuntimeError: If task not found or decomposition fails
        """
        pass

    async def process_meta_task(self, request: OrchestrationCenterRequest) -> OrchestrationCenterResponse:
        pass