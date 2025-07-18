from services.openai_service import OpenAIService
from services.database_service import DatabaseService
from services.agent_service import AgentService
from models.task import TaskSession, BaseTask, MetaTask, TaskDefaultValue
from models.agent import Agent
from models.request import TaskCenterRequest
from models.response import TaskCenterResponse
from models.error import TaskIdRequiredError, ParentTaskIdRequiredError, SessionIdRequiredError, IllgalParameterError
from uuid import uuid4
from a2a.types import Task, TaskStatus, TaskState, Message, TextPart, Role


class TaskService:
    def __init__(self):
        self.openai_service = OpenAIService()
        self.database_service = DatabaseService()
        self.agent_service = AgentService()

    async def create_a2a_message(self, role: Role, text: str) -> Message:
        return Message(
            messageId=str(uuid4()),
            role=role,
            parts=[TextPart(text=text)]
        )

    async def create_a2a_task(self) -> Task:
        return Task(
            id=str(uuid4()),
            kind="task",
            status=TaskStatus(
                state=TaskState.submitted,
            ),
            history=[],
            contextId=str(uuid4()),
            metadata={},
            artifacts=[]
        )
        
    # Task Sessions
    async def create_new_session(self, request: TaskCenterRequest) -> TaskCenterResponse:

        session_id = str(uuid4())
        user_name = request.user_name
        session_name = request.user_input
        session_description = request.user_input

        new_task_session = TaskSession(
            session_id=session_id,
            user_name=user_name,
            session_name=session_name,
            session_description=session_description)
        
        success = await self.database_service.add_task_session(new_task_session)
        if success:
            return TaskCenterResponse(
                session_id=session_id,
                user_name=user_name,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                user_name=user_name, 
                success=False,
                error="Failed to create new session",
                status_code=500
            )
    
    # Base Tasks

    async def create_new_base_task(self, request: TaskCenterRequest) -> TaskCenterResponse:

        task_id = str(uuid4())
        session_id = request.session_id
        user_name = request.user_name
        task = request.task

        new_base_task = BaseTask(
            task_id=task_id,
            session_id=session_id,
            user_name=user_name,
            task=task
        )

        success = await self.database_service.add_base_task(new_base_task)
        if success:
            return TaskCenterResponse(
                task_id=task_id,
                session_id=session_id,
                user_name=user_name,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                user_name=user_name,
                success=False,
                error="Failed to create new base task",
                status_code=500
            )

    # Meta Tasks

    async def create_new_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:

        task_id = str(uuid4())
        parent_task_id = request.parent_task_id
        user_name = request.user_name
        task = request.task
        user_input = request.user_input

        new_meta_task = MetaTask(
            task_id=task_id,
            task_description=user_input,
            agent_id=TaskDefaultValue.NOT_ASSIGNED.value,
            parent_task_id=parent_task_id,
            task=task
        )

        success = await self.database_service.add_meta_task(new_meta_task)
        if success:
            return TaskCenterResponse(
                task_id=task_id,
                parent_task_id=parent_task_id,
                user_name=user_name,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                user_name=user_name,
                success=False,
                error="Failed to create new meta task",
                status_code=500
            )
    
    async def query_base_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        if request.task_id is None:
            raise TaskIdRequiredError()
        
        task_id = request.task_id
        base_task = await self.database_service.get_base_task_by_task_id(task_id)
        if base_task:
            return TaskCenterResponse(
                base_task=base_task,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to query base task",
                status_code=500
            )

    async def query_meta_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        
        if request.task_id is None:
            raise TaskIdRequiredError()

        task_id = request.task_id
        meta_task = await self.database_service.get_meta_task_by_task_id(task_id)
        if meta_task:
            return TaskCenterResponse(
                meta_task=meta_task,
                success=True,
                error=None, 
                status_code=200
            )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to query meta task",
                status_code=500
            )
        
    async def query_meta_tasks_by_parent_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        if request.parent_task_id is None:
            raise ParentTaskIdRequiredError()
        
        parent_task_id = request.parent_task_id
        meta_tasks = await self.database_service.get_meta_tasks_by_parent_task_id(parent_task_id)
        if meta_tasks:
            return TaskCenterResponse(
                meta_tasks=meta_tasks,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to query meta tasks",
                status_code=500
            )
        
    async def query_all_sessions(self, request: TaskCenterRequest) -> TaskCenterResponse:
        user_name = request.user_name
        sessions = await self.database_service.get_task_sessions_by_user_name(user_name)
        if sessions:
            return TaskCenterResponse(
                task_sessions=sessions,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to query all sessions",
                status_code=500
            )
    
    async def delete_meta_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """
        Delete a meta task by its task ID
        """
        try:
            task_id = request.task_id
            if not task_id:
                raise TaskIdRequiredError()
            
            # Execute actual deletion logic
            success = await self.database_service.delete_meta_task_by_task_id(task_id)
            
            if success:
                return TaskCenterResponse(
                    task_id=task_id,
                    success=True,
                    error=None,
                    status_code=200
                )
            else:
                return TaskCenterResponse(
                    task_id=task_id,
                    success=False,
                    error="Failed to delete meta task from database",
                    status_code=500
                )
            
        except Exception as e:
            return TaskCenterResponse(
                task_id=request.task_id,
                success=False,
                error=str(e),
                status_code=500
            )
    
    async def update_meta_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        if request.task_id is None:
            raise TaskIdRequiredError()

        if request.meta_task is None:
            raise IllgalParameterError()
        
        task_id = request.task_id
        meta_task = request.meta_task
        success = await self.database_service.update_meta_task_by_task_id(task_id, meta_task)
        if success:
            return TaskCenterResponse(
                meta_task=meta_task,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to update meta task",
                status_code=500
            )
    
    async def add_message_to_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        if request.task_id is None:
            raise TaskIdRequiredError()
        
        if request.message is None:
            raise IllgalParameterError()
        
        task_id = request.task_id
        message = request.message

        meta_task = await self.database_service.get_meta_task_by_task_id(task_id)
        if meta_task:
            meta_task.task.history.append(message)
            success = await self.database_service.update_meta_task_by_task_id(task_id, meta_task)
            if success:
                return TaskCenterResponse(
                    meta_task=meta_task,
                    success=True,
                    error=None,
                    status_code=200
                )
            else:
                return TaskCenterResponse(
                    success=False,
                    error="Failed to update message to meta task",
                    status_code=500
                )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to update message to meta task",
                status_code=500
            )
    
    async def update_agent_id_of_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        if request.task_id is None:
            raise TaskIdRequiredError()
        
        if request.agent_id is None:
            raise IllgalParameterError()
        
        task_id = request.task_id
        agent_id = request.agent_id

        meta_task = await self.database_service.get_meta_task_by_task_id(task_id)
        if meta_task:
            meta_task.agent_id = agent_id
            success = await self.database_service.update_meta_task_by_task_id(task_id, meta_task)
            if success:
                return TaskCenterResponse(
                    meta_task=meta_task,
                    success=True,
                    error=None,
                    status_code=200
                )
            else:
                return TaskCenterResponse(
                    success=False,
                    error="Failed to update agent id of meta task",
                    status_code=500
                )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to update agent id of meta task",
                status_code=500
            )
    
    async def update_task_of_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        if request.task_id is None:
            raise TaskIdRequiredError()
        
        if request.task is None:
            raise IllgalParameterError()
        
        task_id = request.task_id
        task = request.task

        meta_task = await self.database_service.get_meta_task_by_task_id(task_id)
        if meta_task:
            meta_task.task = task
            success = await self.database_service.update_meta_task_by_task_id(task_id, meta_task)
        if success:
            return TaskCenterResponse(
                meta_task=meta_task,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse( 
                success=False,
                error="Failed to update task of meta task",
                status_code=500
            )
        
    async def update_base_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        if request.task_id is None:
            raise TaskIdRequiredError()
        
        if request.base_task is None:
            raise IllgalParameterError()
        
        task_id = request.task_id
        base_task = request.base_task
        success = await self.database_service.update_base_task_by_task_id(task_id, base_task)
        if success:
            return TaskCenterResponse(
                base_task=base_task,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to update base task",
                status_code=500
            )

    async def query_base_tasks_by_session_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        if request.session_id is None:
            raise SessionIdRequiredError()
        
        session_id = request.session_id
        base_tasks = await self.database_service.get_base_tasks_by_session_id(session_id)
        if base_tasks:
            return TaskCenterResponse(
                base_tasks=base_tasks,
                success=True,
                error=None,
                status_code=200
            )
        else:
            return TaskCenterResponse(
                success=False,
                error="Failed to query base tasks by session id",
                status_code=500
            )
    
task_service = TaskService()