from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime
import json
from models.task import RootTask, ChildTask, TaskSession
from a2a.types import Task, TaskState, TaskStatus, Message, TextPart
from services.database_service import DatabaseService
from services.openai_service import OpenAIService

class TaskService:
    def __init__(self):
        self.database_service = DatabaseService()
        self.openai_service = OpenAIService()
    
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
        # base_task = Task(
        #     id=task_id,
        #     sessionId=session_id,
        #     status=TaskStatus(
        #         state=TaskState.submitted,
        #         timestamp=datetime.now().isoformat()
        #     ),
        #     artifacts=[],
        #     history=[
        #         Message(
        #             role="user",
        #             parts=[TextPart(text=user_input)]
        #         )
        #     ],
        #     metadata={},
        # )

        # Create RootTask with the base Task
        root_task = RootTask(
            task_id=task_id,
            description=user_input,
            task=None,
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
    
    async def update_task_history(self, task_id: str, history: List[Dict[str, Any]]) -> bool:
        """
        Update the history of a task
        """
        old_history = await self.get_task(task_id)['task']['history']
        new_history = old_history.append(history)

        return await self.database_service.update_task_history(task_id, new_history)
    
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
            status.timestamp = datetime.now().isoformat()
        
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
                state=TaskState.submitted,
                timestamp=datetime.now().isoformat()
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
            status.timestamp = datetime.now().isoformat()
        
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
                    state=TaskState.failed,
                    message=Message(
                        role="agent",
                        parts=[TextPart(text=f"Failed to decompose task: JSON parse error")]
                    ),
                    timestamp=datetime.now().isoformat()
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
            state=TaskState.failed,
            message=Message(
                role="agent",
                parts=[TextPart(text=error_message)]
            ),
            timestamp=datetime.now().isoformat()
        )
        await self.update_task_status(task_id, error_status)

    async def create_task_session(self, user_name: str, session_name: str, session_description: str) -> str:
        """
        Create a new task session
        
        Args:
            session_name: Name of the task session
            session_description: Description of the task session
            
        Returns:
            str: ID of the created task session
        """
        task_session = TaskSession(
            session_id=uuid.uuid4().hex,
            user_name=user_name,
            session_name=session_name,
            session_description=session_description,
            session_created_at=datetime.now(),
            session_updated_at=datetime.now(),
            rootTasks=[]
        )
        return await self.database_service.add_task_session(task_session)
    
    async def get_task_session(self, session_id: str) -> TaskSession:
        """
        Get a task session by ID
        
        Args:
            session_id: ID of the task session to retrieve
            
        Returns:
            TaskSession: The task session object or None if not found
        """
        return await self.database_service.get_task_session(session_id)
    
    async def update_task_session(self, session_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a task session
        
        Args:
            session_id: ID of the task session to update
            update_data: New data to update
            
        Returns:
            bool: True if update was successful
        """
        return await self.database_service.update_task_session(session_id, update_data)
    
    async def delete_task_session(self, session_id: str) -> bool:
        """
        Delete a task session
        
        Args:
            session_id: ID of the task session to delete
            
        Returns:
            bool: True if deletion was successful
        """
        return await self.database_service.delete_task_session(session_id)
    
    async def add_root_task_to_session(self, session_id: str, root_task_id: str) -> bool:
        """
        Add a root task to a task session
        
        Args:
            session_id: ID of the task session to add the root task to
            root_task: RootTask object to add
            
        Returns:
            bool: True if addition was successful
        """
        return await self.database_service.add_root_task_to_session(session_id, root_task_id)
    
    async def get_root_tasks_by_session(self, session_id: str) -> List[RootTask]:
        """
        Get all root tasks for a task session
        
        Args:
            session_id: ID of the task session to get root tasks from   

        Returns:
            List[RootTask]: List of root task objects
        """
        return await self.database_service.get_root_tasks_by_session(session_id)
    
    async def update_root_task_in_session(self, session_id: str, root_task_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update a root task in a task session

        Args:
            session_id: ID of the task session to update the root task in
            root_task_id: ID of the root task to update
            update_data: New data to update
            
        Returns:
            bool: True if update was successful
        """
        return await self.database_service.update_root_task_in_session(session_id, root_task_id, update_data)  
    
    async def delete_root_task_from_session(self, session_id: str, root_task_id: str) -> bool:
        """
        Delete a root task from a task session
        
        Args:
            session_id: ID of the task session to delete the root task from
            root_task_id: ID of the root task to delete
            
        Returns:
            bool: True if deletion was successful
        """
        return await self.database_service.delete_root_task_from_session(session_id, root_task_id)
    
    async def get_all_task_sessions(self) -> List[TaskSession]:
        """
        Get all task sessions
        
        Returns:
            List[TaskSession]: List of task session objects
        """
        return await self.database_service.get_all_task_sessions()
    
    async def get_task_session_by_user_name(self, user_name: str) -> TaskSession:
        """
        Get a task session by user name

        Args:
            user_name: Name of the user to get task sessions for
            
        Returns:
            TaskSession: The task session object or None if not found
        """
        return await self.database_service.get_task_session_by_user_name(user_name)
    

    