from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum
from common.types import TaskState

class Step(BaseModel):
    step_id: str
    description: str
    agent_id: Optional[str] = None
    status: str = TaskState.SUBMITTED
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
    status: str = TaskState.SUBMITTED  
    steps: List[Step] = Field(default_factory=list)
    result: Optional[Any] = None
    error: Optional[str] = None 