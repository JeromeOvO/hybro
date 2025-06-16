from typing import List, Optional, Any
from pydantic import BaseModel, Field
from a2a.types import TaskState

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