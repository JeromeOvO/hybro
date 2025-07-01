from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from a2a.types import TaskState, AgentCard, Message
from models.agent import Agent


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
    task_id: str
    agent_id: str
    step_id: str
    input_data: Any
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    message: Optional[Message] = None

class DebatationCenterResponse(BaseModel):
    task_id: str
    agent_id: str
    step_id: str
    result: Optional[Any] = None
    error: Optional[str] = None
    status_code: int = 200

class AgentCenterResponse(BaseModel):
    agent_id: str
    agent_card: Optional[AgentCard] = None
    agent: Optional[Agent] = None
    agents: Optional[List[Agent]] = None
    success: bool
    error: Optional[str] = None
    status_code: int = 200