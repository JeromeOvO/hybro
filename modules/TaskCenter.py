from services.task_service import TaskService
from services.openai_service import OpenAIService
from services.database_service import DatabaseService
from services.agent_service import AgentService
from models.task import TaskSession, BaseTask, MetaTask
from models.agent import Agent
from models.request import TaskCenterRequest
from models.response import TaskCenterResponse
from models.error import TaskIdRequiredError, ParentTaskIdRequiredError, SessionIdRequiredError, IllgalParameterError
from uuid import uuid4

class TaskCenter:
    def __init__(self):
        self.openai_service = OpenAIService()
        self.database_service = DatabaseService()
        self.agent_service = AgentService()
        self.task_service = TaskService()

    # Task Sessions
    async def create_new_session(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Create a new task session for the user."""
        return await self.task_service.create_new_session(request)

    async def create_new_base_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Create a new base task within a session."""
        return await self.task_service.create_new_base_task(request)


    async def create_new_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Create a new meta task linked to a parent task."""
        return await self.task_service.create_new_meta_task(request)

    async def query_meta_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Query a meta task by its task ID."""
        return await self.task_service.query_meta_task_by_task_id(request)

    async def query_meta_tasks_by_parent_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Query meta tasks by their parent task ID (duplicate method name, should be renamed)."""
        return await self.task_service.query_meta_tasks_by_parent_task_id(request)

    async def query_base_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Query a base task by its task ID."""
        return await self.task_service.query_base_task_by_task_id(request)

    async def delete_meta_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Delete a meta task by its task ID."""
        return await self.task_service.delete_meta_task_by_task_id(request)


    async def update_meta_task_by_task_id(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Update a meta task by its task ID."""
        return await self.task_service.update_meta_task_by_task_id(request)


    async def add_message_to_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Add a message to a meta task."""
        return await self.task_service.add_message_to_meta_task(request)


    async def update_agent_id_of_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Update the agent ID associated with a meta task."""
        return await self.task_service.update_agent_id_of_meta_task(request)


    async def update_task_of_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Update the task details of a meta task."""
        return await self.task_service.update_task_of_meta_task(request)


    async def add_message_to_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Add a message to a meta task (duplicate method, should be removed or renamed)."""
        return await self.task_service.add_message_to_meta_task(request)


    async def update_agent_id_of_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Update the agent ID of a meta task (duplicate method, should be removed or renamed)."""
        return await self.task_service.update_agent_id_of_meta_task(request)


    async def update_task_of_meta_task(self, request: TaskCenterRequest) -> TaskCenterResponse:
        """Update the task of a meta task (duplicate method, should be removed or renamed)."""
        return await self.task_service.update_task_of_meta_task(request)


task_center = TaskCenter()