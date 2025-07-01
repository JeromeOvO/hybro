from models.request import OrchestrationCenterRequest
from models.response import OrchestrationCenterResponse
from services.task_service import TaskService
from services.openai_service import OpenAIService

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

        task_id = request.task_id

        root_task = await self.task_service.get_task(task_id)
        if not root_task:
            raise RuntimeError(f"Task {task_id!r} not found")

        llm_res = await self.openai_service.decompose_rootTask(root_task)
        root_id = await self.task_service.create_subtasks_with_openai_content(
            root_task.task_id, llm_res
        )
        root_task = await self.task_service.get_task(root_id)

        if root_task.task.status.state == TaskState.failed:
            text = root_task.task.status.message.parts[0].text
            raise RuntimeError(f"OpenAI decomposition failed: {text}")

        return root_task.task_id

        pass