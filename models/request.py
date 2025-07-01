from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
from uuid import uuid4
from a2a.types import Message, TextPart
from a2a.types import AgentCard
from models.agent import Agent


class TaskRequest(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    message: Optional[Message] = None
    
    def to_message(self) -> Message:
        """Convert request to A2A protocol Message"""
        if self.message:
            return self.message
            
        # Create message if not provided
        parts = [TextPart(text=self.query)]
        return Message(
            role="user",
            parts=parts,
            metadata=self.context
        )

class AgentTaskRequest(BaseModel):
    task_id: str
    agent_id: str
    step_id: str
    input_data: Any
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    message: Optional[Message] = None
    
    def to_message(self) -> Message:
        """Convert agent task request to A2A protocol Message"""
        if self.message:
            return self.message
            
        # Create message from input data
        if isinstance(self.input_data, str):
            parts = [TextPart(text=self.input_data)]
        elif isinstance(self.input_data, dict) and "text" in self.input_data:
            parts = [TextPart(text=self.input_data["text"])]
        else:
            # Try to convert to string or use as-is
            try:
                text = str(self.input_data)
                parts = [TextPart(text=text)]
            except:
                # Use generic text if conversion fails
                parts = [TextPart(text=f"Processing step {self.step_id}")]
        
        # Add metadata
        metadata = {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "step_id": self.step_id,
            **self.context
        }
        
        return Message(
            role="user",
            parts=parts,
            metadata=metadata
        )

# for user
class UserInput(BaseModel):
    user_name: str
    user_input: str
    session_id: Optional[str] = None


# for task id input
class TaskIdInput(BaseModel):
    task_id: str


class SessionInput(BaseModel):
    user_name: str
    session_id: Optional[str] = None

class InspectionCenterRequest(BaseModel):
    agent_id: Optional[str] = None
    agent_url: str

class OrchestrationCenterRequest(BaseModel):
    task_id: str
 
class DebatationCenterRequest(BaseModel):
    task_id: str

class AgentCenterRequest(BaseModel):
    agent_id: Optional[str] = None
    agent_card: Optional[AgentCard] = None
    call_increment: Optional[int] = 0
    call_success_increment: Optional[int] = 0
    like_increment: Optional[int] = 0
    dislike_increment: Optional[int] = 0
    query_text: Optional[str] = None
    agent: Optional[Agent] = None
    agent_count: Optional[int] = 0