from typing import Any, Optional

from a2a.types import AgentCard, Task, TaskState
from pydantic import BaseModel, Field

from models.agent import Agent
from models.task import BaseTask, MetaTask, TaskSession
from models.memory import ChatContext
from datetime import datetime

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

class RoomCenterRoomSettingResponse(BaseModel):
    room_id: str | None = None
    room_name: str | None = None
    room_owner_id: str | None = None
    room_owner_name: str | None = None
    room_agent_set: list[str] | None = None
    room_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    success: bool
    error: str | None = None
    status_code: int = 200

class RoomCenterUserMessageResponse(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    related_message_id: Optional[str] = None
    user_id: str | None = None
    user_name: str | None = None
    user_input: str | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    success: bool
    error: str | None = None
    status_code: int = 200

class RoomCenterAgentMessageResponse(BaseModel):
    room_id: str | None = None
    message_id: str | None = None
    related_message_id: Optional[str] = None
    agent_id: str | None = None
    agent_name: str | None = None
    agent_message_content: Task | None = None
    message_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    success: bool
    error: str | None = None
    status_code: int = 200

class RoomCenterMemoryResponse(BaseModel):  
    room_id: str | None = None
    memory_id: str | None = None
    memory_content: str | None = None
    memory_created_at: datetime | None = None
    extend_info: dict[str, Any] | None = None
    success: bool
    error: str | None = None
    status_code: int = 200