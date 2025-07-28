from datetime import datetime
from enum import Enum
from typing import Any

from a2a.types import Task
from pydantic import BaseModel, Field


class TaskDefaultValue(Enum):
    NOT_ASSIGNED = "Not Assigned"


class MetaTask(BaseModel):
    """A meta task model represents the smallest atomic tasks in the system, usually subtasks from decomposition. It is designed for convenient a2a agent communication."""

    task_id: str
    parent_task_id: str
    agent_id: str = Field(default=TaskDefaultValue.NOT_ASSIGNED.value)
    task_description: str | None = Field(default="")
    task: Task | None = None
    execution_order: int = 0
    extend_info: Any | None = None


class BaseTask(BaseModel):
    """A base task model for one request from user"""

    task_id: str
    session_id: str
    user_name: str
    task: Task
    extend_info: Any | None = None


class TaskSession(BaseModel):
    """Model for a task session. One meta session for one chat session"""

    session_id: str
    user_name: str
    session_name: str
    session_description: str | None = Field(default="")
    session_created_at: datetime = Field(default_factory=datetime.now)
    session_updated_at: datetime = Field(default_factory=datetime.now)
    extend_info: Any | None = None
