from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime
import json
from models.task import RootTask, ChildTask
from common.types import Task, TaskState, TaskStatus, Message, TextPart
from services.database_service import DatabaseService
from services.openai_service import openai_service

class TaskService:
    def __init__(self):
        self.database_service = DatabaseService()
        self.openai_service = openai_service
    
    async def create_task(self, user_input: str) -> str:
        """
        Create a new task from user input
        
        Args:
            user_input: User's original task request
            session_id: Optional session ID (generated if not provided)
            
        Returns:
            str: The ID of the created task
        """
        # Generate task ID and session ID
        session_id = uuid.uuid4().hex
        task_id = uuid.uuid4().hex
        
        # Create base Task object
        base_task = Task(
            id=task_id,
            sessionId=session_id,
            status=TaskStatus(
                state=TaskState.SUBMITTED,
                timestamp=datetime.now()
            ),
            artifacts=[],
            history=[
                Message(
                    role="user",
                    parts=[TextPart(text=user_input)]
                )
            ],
            metadata={},
        )

        # Create RootTask with the base Task
        root_task = RootTask(
            task_id=task_id,
            description=user_input,
            task=base_task,
            subtasks=[]
        )
        
        # Save task to database
        task_id = await self.database_service.add_task(root_task)
        return task_id
    
    async def get_task(self, task_id: str) -> Optional[RootTask]:
        """
        Get a task by ID
        
        Args:
            task_id: ID of the task to retrieve
            
        Returns:
            RootTask or None if not found
        """
        return await self.database_service.get_task(task_id)
    
    async def get_tasks(self, query: Dict[str, Any] = None, limit: int = 0) -> List[RootTask]:
        """
        Get multiple tasks matching a query
        
        Args:
            query: Optional query filter
            limit: Maximum number of results (0 for no limit)
            
        Returns:
            List of RootTask objects
        """
        return await self.database_service.get_tasks(query, limit)
    
    async def update_task(self, task_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a task
        
        Args:
            task_id: ID of the task to update
            update_data: Dictionary of fields to update
            
        Returns:
            bool: True if update was successful
        """
        return await self.database_service.update_task(task_id, update_data)
    
    async def delete_task(self, task_id: str) -> bool:
        """
        Delete a task
        
        Args:
            task_id: ID of the task to delete
            
        Returns:
            bool: True if deletion was successful
        """
        return await self.database_service.delete_task(task_id)
    
    async def update_task_status(self, task_id: str, status: TaskStatus) -> bool:
        """
        Update a task's status
        
        Args:
            task_id: ID of the task to update
            status: New TaskStatus to set
            
        Returns:
            bool: True if update was successful
        """
        # Ensure the timestamp is set to current time if not already provided
        if not status.timestamp:
            status.timestamp = datetime.now()
        
        # 将 TaskStatus 对象转换为字典
        status_dict = status.dict() if hasattr(status, 'dict') else status.model_dump()
        
        # Create update data with just the status field
        update_data = {"task.status": status_dict}
        
        # Use the existing update_task method
        return await self.database_service.update_task(task_id, update_data)
    
    async def create_child_task(self, root_task_id: str, subtask_id: str, description: str, step: Optional[int] = None, priority: Optional[int] = None, dependencies: Optional[List[str]] = None) -> str:
        """
        Create a new child task under a root task
        
        Args:
            root_task_id: ID of the parent root task
            description: Description of the child task
            step: Optional execution order for the child task
            priority: Optional priority level for the child task
            dependencies: Optional list of task IDs this task depends on
            
        Returns:
            str: The ID of the created child task
        """
        # Get the root task to set the session ID
        root_task = await self.get_task(root_task_id)
        if not root_task:
            raise Exception(f"Root task with ID {root_task_id} not found")
        
        # Set the session ID from the parent
        sessionId = root_task.task.sessionId
        
        # Create base Task object for the child task
        child_base_task = Task(
            id=subtask_id,
            sessionId=sessionId,  # Will inherit from parent
            status=TaskStatus(
                state=TaskState.SUBMITTED,
                timestamp=datetime.now()
            ),
            artifacts=[],
            history=None,
            metadata={},
        )

        # Create ChildTask with the base Task
        child_task = ChildTask(
            task_id=subtask_id,  # Will be generated by the database service
            description=description,
            agent_id="Not Assigned",
            task=child_base_task,
            parent_id=root_task_id,
            order=step,
            priority=priority,
            dependencies=dependencies or []
        )

        # Add the child task (DatabaseService handles consistency)
        child_task_id = await self.database_service.add_child_task(root_task_id, child_task)
        return child_task_id

    async def get_child_task(self, child_task_id: str) -> Optional[ChildTask]:
        """
        Get a child task by its ID
        
        Args:
            child_task_id: ID of the child task to retrieve
            
        Returns:
            ChildTask or None if not found
        """
        return await self.database_service.get_child_task(child_task_id)

    async def get_child_tasks_by_parent(self, root_task_id: str) -> List[ChildTask]:
        """
        Get all child tasks for a parent task
        
        Args:
            root_task_id: ID of the parent task
            
        Returns:
            List[ChildTask]: List of child task objects
        """
        return await self.database_service.get_child_tasks_by_parent(root_task_id)

    async def update_child_task(self, child_task_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a child task
        
        Args:
            child_task_id: ID of the child task to update
            update_data: Dictionary of fields to update
            
        Returns:
            bool: True if update was successful
        """
        return await self.database_service.update_child_task(child_task_id, update_data)

    async def delete_child_task(self, child_task_id: str) -> bool:
        """
        Delete a child task
        
        Args:
            child_task_id: ID of the child task to delete
            
        Returns:
            bool: True if deletion was successful
        """
        return await self.database_service.delete_child_task(child_task_id)

    async def update_child_task_status(self, child_task_id: str, status: TaskStatus) -> bool:
        """
        Update a child task's status
        
        Args:
            child_task_id: ID of the child task to update
            status: New TaskStatus to set
            
        Returns:
            bool: True if update was successful
        """
        # Ensure the timestamp is set to current time if not already provided
        if not status.timestamp:
            status.timestamp = datetime.now()
        
        # 将 TaskStatus 对象转换为字典
        status_dict = status.dict() if hasattr(status, 'dict') else status.model_dump()
        
        # Create update data with just the status field
        update_data = {"task.status": status_dict}
        
        # Use the existing update_child_task method
        return await self.update_child_task(child_task_id, update_data)
    
    async def create_subtasks_with_openai_content(self, root_task_id: str, content: str) -> RootTask:
        """
        Process OpenAI content and create subtasks for a root task
        
        Args:
            root_task: The root task to update
            content: The JSON content from OpenAI
            
        Returns:
            RootTask: The updated root task with child tasks
        """
        try:
            # Parse the JSON content
            try:
                decomposition_result = json.loads(content)
            except json.JSONDecodeError:
                print(f"Error parsing JSON: {content}")
                error_status = TaskStatus(
                    state=TaskState.FAILED,
                    message=Message(
                        role="agent",
                        parts=[TextPart(text=f"Failed to decompose task: JSON parse error")]
                    ),
                    timestamp=datetime.now()
                )
                await self.update_task_status(root_task_id, error_status)
                return root_task_id
            
            # Get the root task to set the session ID
            root_task = await self.get_task(root_task_id)
            if not root_task:
                raise Exception(f"Root task with ID {root_task_id} not found")
            
            # Convert decomposition results to ChildTask objects
            for idx, subtask_data in enumerate(decomposition_result.get("subtasks", [])):
                subtask_id = uuid.uuid4().hex
                subtask_description = subtask_data.get("description", "")
                subtask_step = subtask_data.get("step", 1)
                subtask_priority = subtask_data.get("priority", 1)
                subtask_dependencies = subtask_data.get("dependencies", [])

                await self.create_child_task(
                    root_task_id, 
                    subtask_id, 
                    subtask_description, 
                    subtask_step,
                    subtask_priority, 
                    subtask_dependencies
                )

            return root_task_id
            
        except Exception as e:
            print(f"Error creating subtasks: {str(e)}")
            # Update task status to reflect error
            await self.update_task_as_failed(root_task_id, f"Failed to create subtasks: {str(e)}")
            return root_task_id
    
    async def update_task_as_failed(self, task_id: str, error_message: str) -> None:
        """
        Mark a task as failed with the provided error message
        
        Args:
            task_id: ID of the task to update
            error_message: Error message explaining the failure
        """
        error_status = TaskStatus(
            state=TaskState.FAILED,
            message=Message(
                role="agent",
                parts=[TextPart(text=error_message)]
            ),
            timestamp=datetime.now()
        )
        await self.update_task_status(task_id, error_status)