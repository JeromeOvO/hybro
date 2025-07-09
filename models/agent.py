from enum import Enum
from pydantic import BaseModel
from typing import Optional, Dict, Any
from a2a.types import AgentCard


class AgentStatus(Enum):
    active = "active"
    inactive = "inactive"
    deleted = "deleted"

class Agent(BaseModel):

    # Primary identification field
    agent_id: str

    # Agent card
    agent_card: AgentCard

    # Agent status
    agent_status: AgentStatus = AgentStatus.active

    # Count for agent usage
    call_count: int = 0

    # Count for agent success usage
    call_success_count: int = 0

    # Like count from user
    like_count: int = 0

    # Dislike count from user   
    dislike_count: int = 0
