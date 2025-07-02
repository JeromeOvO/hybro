from typing import Any, List, Optional
from pydantic import BaseModel, Field
from a2a.types import Task, Message
from datetime import datetime


class MetaTask(BaseModel):
    """A meta task model in the system and also represent a subtask created from decomposition, design for a2a agent communication"""

    task_id: str
    parent_task_id: str
    agent_id: str = Field(default="Not Assigned")
    task_description: Optional[str] = Field(default="")
    task: Task | None = None
    execution_order: int = 0
    extend_info: Optional[Any] = None


class BaseTask(BaseModel):
    """A base task mode for one request from user"""

    task_id: str
    session_id: str
    user_name: str
    task: Task
    extend_info: Optional[Any] = None


class TaskSession(BaseModel):
    """Model for a task session. One meta session for one chat session"""

    session_id: str
    user_name: str
    session_name: str
    session_description: Optional[str] = Field(default="")
    session_created_at: datetime = Field(default_factory=datetime.now)
    session_updated_at: datetime = Field(default_factory=datetime.now)
    extend_info: Optional[Any] = None