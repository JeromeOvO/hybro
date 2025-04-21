from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from enum import Enum

class AgentType(str, Enum):
    MATH = "math"
    CODING = "coding"
    RESEARCH = "research"
    WRITING = "writing"
    GENERAL = "general"
    # Add more agent types as needed

class Agent(BaseModel):
    id: str
    name: str
    description: str
    agent_type: AgentType
    capabilities: List[str]
    parameters: Dict[str, Any] = {}
    embedding: Optional[List[float]] = None
    model: str
    is_remote: bool = False  # Flag indicating if this is a remote agent
    endpoint: Optional[str] = None  # API endpoint for remote agents
    prompt: Optional[str] = None  # System prompt for local agents
    
    class Config:
        schema_extra = {
            "example": {
                "id": "math-solver-1",
                "name": "Math Problem Solver",
                "description": "Expert at solving complex mathematical problems",
                "agent_type": "math",
                "capabilities": ["algebra", "calculus", "statistics"],
                "parameters": {"precision": "high"},
                "model": "gpt-4o",
                "is_remote": False,
                "prompt": "You are an expert mathematics problem solver specialized in algebra, calculus, and statistics."
            }
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert agent to dictionary format for storage"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_type": self.agent_type,
            "capabilities": self.capabilities,
            "parameters": self.parameters,
            "embedding": self.embedding,
            "model": self.model,
            "is_remote": self.is_remote,
            "endpoint": self.endpoint,
            "prompt": self.prompt
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Agent":
        """Create agent from dictionary"""
        return cls(**data) 