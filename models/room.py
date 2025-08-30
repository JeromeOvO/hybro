from datetime import datetime
from typing import Any, Optional, Dict
from uuid import uuid4

from pydantic import BaseModel, Field
from a2a.types import Task


class Room(BaseModel):
    room_id: str = Field(default_factory=lambda: uuid4().hex)
    room_name: str
    room_owner_id: str
    room_owner_name: str
    room_agent_set: Dict[str, str] = Field(default_factory=dict) # key: agent_id, value: agent_name
    room_created_at: datetime = Field(default_factory=datetime.now)
    extend_info: Optional[Any] = None

class MessageContent(BaseModel):
    #markdown
    message_text: str

class RoomUserMessage(BaseModel):
    room_id: str
    message_id: str
    related_message_id: Optional[str] = None

    user_id: str
    user_name: str

    message_content: MessageContent

    message_created_at: datetime = Field(default_factory=datetime.now)
    extend_info: Optional[Any] = None


class RoomAgentMessage(BaseModel):
    room_id: str
    message_id: str
    related_message_id: Optional[str] = None
    
    agent_id: str
    agent_name: str

    message_content: Task
    message_created_at: datetime = Field(default_factory=datetime.now)
    extend_info: Optional[Any] = None


class MemoryContent(BaseModel):
    memory_text: str

class RoomMemory(BaseModel):
    room_id: str
    memory_id: str
    memory_content: MemoryContent
    memory_created_at: datetime = Field(default_factory=datetime.now)
    extend_info: Optional[Any] = None

class RoomMessage(BaseModel):
    """Unified room message format for both user and agent messages"""
    message_id: str
    message_type: str  # "user" or "agent"
    message_content: str
    message_created_at: datetime
    user_name: str | None = None
    agent_name: str | None = None