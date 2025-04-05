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
    
    class Config:
        schema_extra = {
            "example": {
                "id": "math-solver-1",
                "name": "Math Problem Solver",
                "description": "Expert at solving complex mathematical problems",
                "agent_type": "math",
                "capabilities": ["algebra", "calculus", "statistics"],
                "parameters": {"precision": "high"},
                "model": "gpt-4o"
            }
        } 