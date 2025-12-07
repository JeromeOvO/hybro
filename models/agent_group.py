"""
Agent Group Model

Agent groups are reusable templates for selecting agents.
They can be built-in (All Agents, Room Team) or user-created.
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentGroup(BaseModel):
    """
    Represents a group of agents that can be used as a target for messages.
    
    Groups are templates - when applied, agents are copied to the target (room/workflow).
    This ensures no cross-user dependencies when sharing.
    """
    group_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    description: Optional[str] = None
    type: str  # 'builtin' | 'user'
    owner_id: Optional[str] = None  # null for builtin groups
    agents: list[str] = Field(default_factory=list)  # List of agent IDs
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# Built-in group IDs (constants)
BUILTIN_GROUP_ALL_AGENTS = "all_agents"
BUILTIN_GROUP_ROOM_TEAM = "room_team"


def create_builtin_all_agents_group() -> AgentGroup:
    """
    Create the built-in 'All Agents' group.
    This is a special group that triggers network search instead of using a fixed agent list.
    """
    return AgentGroup(
        group_id=BUILTIN_GROUP_ALL_AGENTS,
        name="All Agents",
        description="Search the entire agent network for the best match",
        type="builtin",
        owner_id=None,
        agents=[],  # Empty = dynamic network search
    )


def create_builtin_room_team_group() -> AgentGroup:
    """
    Create the built-in 'Room Team' group.
    This is a special group that uses the room's agent_set.
    """
    return AgentGroup(
        group_id=BUILTIN_GROUP_ROOM_TEAM,
        name="Room Team",
        description="Use agents assigned to this room",
        type="builtin",
        owner_id=None,
        agents=[],  # Agents come from room.room_agent_set at runtime
    )

