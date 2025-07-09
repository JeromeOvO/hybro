from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from a2a.types import TaskState, AgentCard, Message, Task
from models.agent import Agent
from models.task import MetaTask, BaseTask, TaskSession

class Step(BaseModel):
    step_id: str
    description: str
    agent_id: Optional[str] = None
    status: str = TaskState.submitted
    input_data: Optional[Any] = None
    output_data: Optional[Any] = None
    priority: int = 2  # Default priority
    dependencies: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    result: Optional[Any] = None
    agent_name: Optional[str] = None
    is_remote_agent: Optional[bool] = False

class TaskResponse(BaseModel):
    task_id: str
    status: str = TaskState.submitted  
    steps: List[Step] = Field(default_factory=list)
    result: Optional[Any] = None
    error: Optional[str] = None 

class UserResponse(BaseModel):
    session_id: str
    task_id: str
    result: str

class InspectionCenterResponse(BaseModel):
    agent_url: str
    agent_card: Optional[AgentCard] = None
    result: List[str]
    status_code: int = 200

class InsepectionCenterConnectionValidationResponse(BaseModel):
    agent_url: str
    agent_card: Optional[AgentCard] = None
    is_valid: bool
    result: Optional[List[str]] = None
    status_code: int = 200

class OrchestrationCenterResponse(BaseModel):
    task_id: Optional[str] = None
    meta_task_ids: Optional[List[str]] = None
    agent_id: Optional[str] = None
    success: bool
    error: Optional[str] = None
    status_code: int = 200

class DebatationCenterResponse(BaseModel):
    task_id: str
    agent_id: str
    step_id: str
    result: Optional[Any] = None
    error: Optional[str] = None
    status_code: int = 200

class AgentCenterResponse(BaseModel):
    agent_id: Optional[str] = None
    agent_card: Optional[AgentCard] = None
    agent: Optional[Agent] = None
    agents: Optional[List[Agent]] = None
    success: bool
    error: Optional[str] = None
    status_code: int = 200

class TaskCenterResponse(BaseModel):
    task_id: Optional[str] = None
    user_name: Optional[str] = None
    parent_task_id: Optional[str] = None
    session_id: Optional[str] = None
    task: Optional[Task] = None
    meta_task: Optional[MetaTask] = None
    base_task: Optional[BaseTask] = None
    task_session: Optional[TaskSession] = None
    meta_tasks: Optional[List[MetaTask]] = None
    base_tasks: Optional[List[BaseTask]] = None
    task_sessions: Optional[List[TaskSession]] = None
    success: bool
    error: Optional[str] = None
    status_code: int = 200

class ChatResponse(BaseModel):
    user_name: str
    user_input: str
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    success: bool
    error: Optional[str] = None
    status_code: int = 200