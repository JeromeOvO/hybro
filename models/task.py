from typing import List, Optional
from pydantic import BaseModel, Field
from a2a.types import Task, Message
from datetime import datetime


class SubTask(BaseModel):
    """Model for a subtask created from decomposition - meta task with remote a2a agent"""

    task_id: str
    parent_id: str
    agent_id: str = Field(default="Not Assigned")
    task_description: Optional[str] = Field(default="")
    task: Task
    execution_order: int = 0
    sub_tasks: List[str] = Field(default_factory=list)


class RootTask(BaseModel):
    """Model for a root task - one time meta task between user and hybro"""

    task_id: str
    task: Task
    sub_tasks: List[str] = Field(default_factory=list)


class TaskSession(BaseModel):
    """Model for a task session. One meta session for one chat session"""

    session_id: str
    user_name: str = Field(default="")
    session_name: str
    session_description: Optional[str] = Field(default="")
    session_created_at: datetime = Field(default_factory=datetime.now)
    session_updated_at: datetime = Field(default_factory=datetime.now)
    root_tasks: List[str] = Field(default_factory=list)