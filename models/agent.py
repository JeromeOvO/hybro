from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
from enum import Enum
from common.types import AgentCard

class Agent(BaseModel):
    # Primary identification field
    agent_id: str
    
    # Main agent card containing all agent metadata and capabilities
    agentCard: AgentCard
    
    # Deployment related fields
    is_remote: bool = False
    ragUrl: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Agent":
        """Create agent from dictionary"""
        return cls(**data)
    
    def to_agent_card(self) -> AgentCard:
        """Return the agent card directly"""
        return self.agentCard
    
    class Config:
        schema_extra = {
            "example": {
                "agent_id": "math-solver-1",
                "agentCard": {
                    "name": "Math Problem Solver",
                    "description": "Expert at solving complex mathematical problems",
                    "url": "http://localhost:10000",
                    "provider": {
                        "organization": "MathSolvers Inc",
                        "url": "https://mathsolvers.example.com"
                    },
                    "version": "1.0",
                    "capabilities": {
                        "streaming": True,
                        "pushNotifications": False,
                        "stateTransitionHistory": False
                    },
                    "defaultInputModes": ["text"],
                    "defaultOutputModes": ["text"],
                    "skills": [
                        {
                            "id": "algebra-solving",
                            "name": "Algebra Solver",
                            "description": "Solves algebraic equations",
                            "tags": ["algebra", "equations"]
                        }
                    ]
                },
                "is_remote": False,
                "ragUrl": "http://localhost:11000/rag"
            }
        } 