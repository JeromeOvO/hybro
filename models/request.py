from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import uuid4

class TaskRequest(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    context: Optional[Dict[str, Any]] = {}

class AgentTaskRequest(BaseModel):
    task_id: str
    agent_id: str
    step_id: str
    input_data: Any
    context: Optional[Dict[str, Any]] = {} 