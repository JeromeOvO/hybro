from datetime import datetime
from enum import Enum
from typing import Any

from a2a.types import Task
from pydantic import BaseModel, Field


class TaskDefaultValue(Enum):
    NOT_ASSIGNED = "Not Assigned"


class MetaTask(BaseModel):
    """
    A MetaTask represents an atomic subtask created from decomposing a larger user request(BaseTask).
    These are the individual work units assigned to specific agents in the multi-agent system.
    Each MetaTask contains a Task object with the actual agent communication data.
    """

    task_id: str
    parent_task_id: str
    agent_id: str = Field(default=TaskDefaultValue.NOT_ASSIGNED.value)
    task_description: str | None = Field(default="")
    task: Task | None = None
    execution_order: int = 0
    # Track dependencies and context
    depends_on_tasks: list[str] = Field(default_factory=list)
    context_from_previous: dict[str, Any] = Field(default_factory=dict)
    extend_info: Any | None = None


class BaseTask(BaseModel):
    """
    A BaseTask represents a complete user request and serves as the top-level container.
    It wraps a Task object and includes session/user metadata for tracking purposes.
    This is the main task that gets decomposed into MetaTasks for multi-agent processing.
    """

    task_id: str
    session_id: str
    user_name: str
    task: Task
    extend_info: Any | None = None


class TaskSession(BaseModel):
    """
    A TaskSession represents a chat conversation between a user and the multi-agent system.
    It tracks session metadata like creation time, user info, and session description.
    Multiple BaseTask objects can belong to one TaskSession during a conversation.
    """

    session_id: str
    user_name: str
    session_name: str
    session_description: str | None = Field(default="")
    session_created_at: datetime = Field(default_factory=datetime.now)
    session_updated_at: datetime = Field(default_factory=datetime.now)
    extend_info: Any | None = None
