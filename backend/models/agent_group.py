"""
Agent Group Model

An agent group is a set of agent IDs with two scopes:
  - Saved group: reusable across chats and rooms (persisted via CRUD).
  - Room group: room-scoped snapshot that only lives with the room.

Applying a saved group to a room copies its members into the room snapshot.
Later edits to the saved group do not change existing rooms.
"""

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentGroup(BaseModel):
    """Persisted saved-group model.

    A group is only a set of agent IDs — it carries no runtime semantics.
    """

    group_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    description: str | None = None
    type: str  # 'builtin' | 'user'
    owner_id: str | None = None
    agents: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# Legacy built-in group IDs (kept for backward compatibility)
BUILTIN_GROUP_ALL_AGENTS = "all_agents"
BUILTIN_GROUP_ROOM_TEAM = "room_team"
