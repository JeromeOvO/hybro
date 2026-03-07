"""
Agent Group Model

An agent group is a set of agent IDs with two scopes:
  - Saved group: reusable across chats and rooms (persisted via CRUD).
  - Room group: room-scoped snapshot that only lives with the room.

Applying a saved group to a room copies its members into the room snapshot.
Later edits to the saved group do not change existing rooms.
"""

from datetime import datetime
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class MessageTargetMode(StrEnum):
    """Candidate-scope selector for non-mention message flows."""

    ROOM_DEFAULT = "room_default"
    ALL_AGENTS = "all_agents"
    SAVED_GROUP = "saved_group"


class AgentGroup(BaseModel):
    """Persisted saved-group model.

    A group is only a set of agent IDs — it carries no runtime semantics.
    """

    group_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    description: Optional[str] = None
    type: str  # 'builtin' | 'user'
    owner_id: Optional[str] = None
    agents: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# Legacy built-in group IDs (kept for backward compatibility)
BUILTIN_GROUP_ALL_AGENTS = "all_agents"
BUILTIN_GROUP_ROOM_TEAM = "room_team"


def normalize_legacy_target_group(target_group: str) -> tuple[str, str | None]:
    """Normalize a legacy target_group value into (MessageTargetMode, target_group_id).

    Canonical fields always win; use this only when canonical fields are absent.
    """
    if target_group == BUILTIN_GROUP_ROOM_TEAM:
        return (MessageTargetMode.ROOM_DEFAULT, None)
    if target_group == BUILTIN_GROUP_ALL_AGENTS:
        return (MessageTargetMode.ALL_AGENTS, None)
    return (MessageTargetMode.SAVED_GROUP, target_group)


def create_builtin_all_agents_group() -> AgentGroup:
    return AgentGroup(
        group_id=BUILTIN_GROUP_ALL_AGENTS,
        name="All Agents",
        description="Search the entire agent network for the best match",
        type="builtin",
        owner_id=None,
        agents=[],
    )


def create_builtin_room_team_group() -> AgentGroup:
    """Legacy built-in group.  New code should use MessageTargetMode.ROOM_DEFAULT."""
    return AgentGroup(
        group_id=BUILTIN_GROUP_ROOM_TEAM,
        name="Room Team",
        description="Use agents assigned to this room",
        type="builtin",
        owner_id=None,
        agents=[],
    )

