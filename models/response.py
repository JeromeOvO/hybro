from typing import Any

from a2a.types import AgentCard, Task, TaskState
from pydantic import BaseModel, Field

from models.agent import Agent
from models.memory import ChatContext
from models.task import BaseTask, MetaTask, TaskSession


class Step(BaseModel):
    step_id: str
    description: str
    agent_id: str | None = None
    status: str = TaskState.submitted
    input_data: Any | None = None
    output_data: Any | None = None
    priority: int = 2  # Default priority
    dependencies: list[str] = Field(default_factory=list)
    error: str | None = None
    result: Any | None = None
    agent_name: str | None = None
    is_remote_agent: bool | None = False


class TaskResponse(BaseModel):
    task_id: str
    status: str = TaskState.submitted
    steps: list[Step] = Field(default_factory=list)
    result: Any | None = None
    error: str | None = None


class UserResponse(BaseModel):
    session_id: str
    task_id: str
    result: str


class InspectionCenterResponse(BaseModel):
    agent_url: str
    agent_card: AgentCard | None = None
    result: list[str]
    status_code: int = 200


class InsepectionCenterConnectionValidationResponse(BaseModel):
    agent_url: str
    agent_card: AgentCard | None = None
    is_valid: bool
    result: list[str] | None = None
    status_code: int = 200


class OrchestrationCenterResponse(BaseModel):
    task_id: str | None = None
    meta_task_ids: list[str] | None = None
    agent_id: str | None = None
    success: bool
    error: str | None = None
    status_code: int = 200


class DebatationCenterResponse(BaseModel):
    task_id: str
    agent_id: str
    step_id: str
    result: Any | None = None
    error: str | None = None
    status_code: int = 200


class AgentCenterResponse(BaseModel):
    agent_id: str | None = None
    agent_card: AgentCard | None = None
    agent: Agent | None = None
    agents: list[Agent] | None = None
    success: bool
    error: str | None = None
    status_code: int = 200


class TaskCenterResponse(BaseModel):
    task_id: str | None = None
    user_name: str | None = None
    parent_task_id: str | None = None
    session_id: str | None = None
    task: Task | None = None
    meta_task: MetaTask | None = None
    base_task: BaseTask | None = None
    task_session: TaskSession | None = None
    meta_tasks: list[MetaTask] | None = None
    base_tasks: list[BaseTask] | None = None
    task_sessions: list[TaskSession] | None = None
    success: bool
    error: str | None = None
    status_code: int = 200


class ChatResponse(BaseModel):
    user_name: str
    user_input: str
    session_id: str | None = None
    task_id: str | None = None
    success: bool
    error: str | None = None
    status_code: int = 200


class ChatMemoryResponse(BaseModel):
    user_name: str
    chat_context: ChatContext | None = None
    success: bool
    error: str | None = None
    status_code: int = 200
