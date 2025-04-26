from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
from enum import Enum

class AgentType(str, Enum):
    MATH = "math"
    CODING = "coding"
    RESEARCH = "research"
    WRITING = "writing"
    GENERAL = "general"
    # Add more agent types as needed

class AgentProvider(BaseModel):
    organization: str
    url: Optional[str] = None

class AgentCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False

class AgentAuthentication(BaseModel):
    schemes: List[str]
    credentials: Optional[str] = None

class AgentSkill(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    examples: Optional[List[str]] = None
    inputModes: Optional[List[str]] = None
    outputModes: Optional[List[str]] = None

class Agent(BaseModel):
    # Core identification fields
    id: str
    name: str
    description: Optional[str] = None
    
    # AgentCard compatibility fields
    url: Optional[str] = None
    provider: Optional[AgentProvider] = None
    version: Optional[str] = "1.0"
    documentationUrl: Optional[str] = None
    capabilities: Union[AgentCapabilities, List[str]] = Field(default_factory=AgentCapabilities)
    authentication: Optional[AgentAuthentication] = None
    defaultInputModes: List[str] = ["text"]
    defaultOutputModes: List[str] = ["text"]
    skills: Optional[List[AgentSkill]] = None
    
    # Legacy fields (maintained for backward compatibility)
    agent_type: Optional[AgentType] = None
    parameters: Dict[str, Any] = {}
    embedding: Optional[List[float]] = None
    model: Optional[str] = None
    is_remote: bool = False
    endpoint: Optional[str] = None
    prompt: Optional[str] = None
    
    class Config:
        schema_extra = {
            "example": {
                "id": "math-solver-1",
                "name": "Math Problem Solver",
                "description": "Expert at solving complex mathematical problems",
                "agent_type": "math",
                "capabilities": {
                    "streaming": True,
                    "pushNotifications": False,
                    "stateTransitionHistory": False
                },
                "parameters": {"precision": "high"},
                "model": "gpt-4o",
                "is_remote": False,
                "url": "http://localhost:10000",
                "provider": {
                    "organization": "MathSolvers Inc",
                    "url": "https://mathsolvers.example.com"
                },
                "version": "1.0",
                "skills": [
                    {
                        "id": "algebra-solving",
                        "name": "Algebra Solver",
                        "description": "Solves algebraic equations",
                        "tags": ["algebra", "equations"]
                    }
                ],
                "prompt": "You are an expert mathematics problem solver specialized in algebra, calculus, and statistics."
            }
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Agent":
        """Create agent from dictionary"""
        return cls(**data)
    
    def to_agent_card(self) -> Dict[str, Any]:
        """Convert to AgentCard format"""
        # Start with the base fields that match AgentCard
        card_dict = {
            "name": self.name,
            "description": self.description,
            "url": self.url or self.endpoint or "",
            "version": self.version or "1.0",
            "defaultInputModes": self.defaultInputModes,
            "defaultOutputModes": self.defaultOutputModes,
        }
        
        # Add provider if available
        if self.provider:
            card_dict["provider"] = self.provider.model_dump(exclude_none=True)
        
        # Add capabilities
        if isinstance(self.capabilities, AgentCapabilities):
            card_dict["capabilities"] = self.capabilities.model_dump(exclude_none=True)
        else:
            # Create default capabilities if only legacy list exists
            card_dict["capabilities"] = AgentCapabilities().model_dump()
            
        # Add authentication if available
        if self.authentication:
            card_dict["authentication"] = self.authentication.model_dump(exclude_none=True)
            
        # Add skills
        if self.skills:
            card_dict["skills"] = [skill.model_dump(exclude_none=True) for skill in self.skills]
        else:
            # Create a default skill from agent_type and capabilities if skills not provided
            default_skill = {
                "id": f"{self.id}-default-skill" if self.id else "default-skill",
                "name": f"{self.name} Skill" if self.name else "Default Skill",
                "description": self.description,
            }
            
            # Add tags from legacy capabilities if they're a list
            if isinstance(self.capabilities, list):
                default_skill["tags"] = self.capabilities
                
            card_dict["skills"] = [default_skill]
            
        # Add documentation URL if available
        if self.documentationUrl:
            card_dict["documentationUrl"] = self.documentationUrl
            
        return card_dict 