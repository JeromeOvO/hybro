from typing import List, Optional, Dict, Any
from services.task_service import TaskService
from services.openai_service import openai_service
from common.types import TaskState
from common.utils.remote_agent_connection import RemoteAgentConnections
from common.types import Message, TextPart, TaskState, AgentCard, TaskSendParams, AgentCapabilities, AgentSkill, AgentProvider, TaskSendParams, PushNotificationConfig
from services.database_service import DatabaseService
from models.task import ChildTask

class HostAgent:
    def __init__(self):
        self.task_service = TaskService()
        self.openai_service = openai_service
        self.database_service = DatabaseService()

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

    async def send_task_to_agent(self, child_task_id: str) -> Dict[str, Any]:
        """
        Execute a remote agent to process a child task  
        
        Args:
            child_task_id: ID of the child task
            
        Returns:
            Dict: The execution result
        """
        # Get the child task
        child_task = await self.database_service.get_child_task(child_task_id)
        if not child_task:
            raise ValueError(f"Child task with ID {child_task_id} not found")
        
        if child_task.get("agent_id"):
            agent_id = child_task["agent_id"]
        else:
            agent_id = await self.find_best_agent_for_task(child_task_id)
        
        # Get the agent
        agent = await self.database_service.get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent with ID {agent_id} not found")
        

        # Create A2A client
        agent_card = AgentCard(**agent["agentCard"])
        client = RemoteAgentConnections(agent_card)
        child_task = ChildTask(**child_task)
        
        # Prepare payload for the agent
        payload = TaskSendParams(
            id=child_task.task_id,
            sessionId=child_task.task.sessionId,
            message=Message(
                role="user",
                parts=[TextPart(text=child_task.description)]
            ),
            acceptedOutputModes=["text"],
            pushNotification=None,
            historyLength=None,
            metadata=child_task.task.metadata
        )

        task_result = await client.send_task(payload, None)
        child_task = await self.database_service.update_child_task(child_task_id, task_result)

        return child_task

        

        # try:
        #     # Send task to the agent
        #     task_result = await client.send_task(payload)
            
        #     # Update child task with agent ID and response
        #     await self.database_service.update_child_task(child_task_id, {
        #         "agent_id": agent_id,
        #         "task.status.state": TaskState.COMPLETED,
        #         "task.history": task_result.history if task_result and task_result.history else []
        #     })
            
        #     return {
        #         "success": True,
        #         "task_id": child_task_id,
        #         "agent_id": agent_id,
        #         "result": task_result
        #     }
        # except Exception as e:
        #     # Update child task with error status
        #     await self.database_service.update_child_task(child_task_id, {
        #         "agent_id": agent_id,
        #         "task.status.state": TaskState.FAILED,
        #         "task.status.message": {
        #             "role": "agent", 
        #             "parts": [{"type": "text", "text": f"Error executing agent: {str(e)}"}]
        #         }
        #     })
