from typing import List, Optional
from services.task_service import TaskService
from services.openai_service import openai_service
from common.types import TaskState

class HostAgent:
    def __init__(self):
        self.task_service = TaskService()
        self.openai_service = openai_service

    async def create_task_from_input(self, user_input: str) -> str:
        """
        Create a task from user input and store it in the database
        
        Args:
            user_input: User's original task request
            
        Returns:
            str: The ID of the created task
        """
        # Call the task_service to create a task
        task_id = await self.task_service.create_task(user_input)


        return task_id
    
    async def decompose_task(self, task_id: str) -> List[str]:
        """
        Decompose a task into multiple subtasks using OpenAI
        
        Args:
            task_id: ID of the task to decompose
            
        Returns:
            List[str]: List of created child task IDs
        """
        # Get the task from database
        root_task = await self.task_service.get_task(task_id)
        if not root_task:
            raise Exception(f"Task with ID {task_id} not found")
        
        # Call OpenAI to decompose the task
        decomposed_task_reuslt_from_llm = await self.openai_service.decompose_rootTask(root_task)
        
        # Create child tasks for each subtask
        root_task_id = await self.task_service.create_subtasks_with_openai_content(root_task.task_id, decomposed_task_reuslt_from_llm)
        root_task = await self.task_service.get_task(root_task_id)
        if not root_task:
            raise Exception(f"Root task with ID {root_task_id} not found")

        if(root_task.task.status.state == TaskState.FAILED):
            raise Exception(f"Failed to create subtasks: {root_task.task.status.message.parts[0].text}")
        
        return root_task.subtasks

    