from pydantic import BaseModel
from typing import Optional, Dict, Any
from a2a.types import AgentCard


class Agent(BaseModel):

    # Primary identification field
    agent_id: str

    # Agent provider
    agent_provider: str

    # Agent card
    agent_card: AgentCard

    # Agent status
    agent_status: bool = False

    # RAG URL
    rag_url: Optional[str] = None

    call_count: int = 0

    call_success_count: int = 0

    like_count: int = 0

    dislike_count: int = 0


    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Agent":
        """Create agent from dictionary"""
        return cls(**data)

    def to_agent_card(self) -> AgentCard:
        """Return the agent card directly"""
        return self.agentCard
