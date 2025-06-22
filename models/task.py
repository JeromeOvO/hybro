from typing import List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field, ConfigDict
from a2a.types import Task
from datetime import datetime


class ChildTask(BaseModel):
    """Model for a subtask created from AI decomposition.

    Contains a Task from common/types.py and adds fields
    for task decomposition relationships.
    """

    task_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_id: str = Field(default="Not Assigned")
    description: Optional[str] = Field(default="")
    task: Task  # The base task from common/types
    parent_id: str  # ID of the parent task (either RootTask or another ChildTask)
    order: int = 0  # Execution order within siblings
    priority: int = 0  # Priority for execution (higher number = higher priority)
    dependencies: List[int] = Field(
        default_factory=list
    )  # List of other task IDs this depends on
    subtasks: List["ChildTask"] = Field(
        default_factory=list
    )  # For hierarchical decomposition
    depth: int = 1  # Depth in the task tree (root = 0, first level = 1, etc.)


class RootTask(BaseModel):
    """Enhanced Task model for MongoDB storage.

    Contains a Task from common/types.py and adds task_id and subtasks fields
    for storing AI-decomposed subtasks.
    """

    task_id: str = Field(default_factory=lambda: uuid4().hex)
    task: Optional[Task] = None  # The base task from common/types
    description: Optional[str] = Field(default="")  # Description of the root task
    subtasks: List[ChildTask] = Field(default_factory=list)

    # model_config = ConfigDict(
    #     populate_by_name=True,
    #     json_encoders={
    #         # Add any custom encoders if needed for MongoDB serialization
    #     },
    # )

class TaskSession(BaseModel):
    user_name: str = Field(default="")
    session_id: str
    session_name: str
    session_description: Optional[str] = Field(default="")
    session_created_at: datetime = Field(default_factory=datetime.now)
    session_updated_at: datetime = Field(default_factory=datetime.now)
    rootTasks: List[str] = Field(default_factory=list)