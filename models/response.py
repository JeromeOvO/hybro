from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class Step(BaseModel):
    step_id: str
    description: str
    agent_id: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    input_data: Optional[Any] = None
    output_data: Optional[Any] = None

class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    steps: List[Step] = []
    result: Optional[Any] = None
    error: Optional[str] = None 