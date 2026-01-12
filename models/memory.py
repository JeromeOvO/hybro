from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from common.utils.time import utcnow

class ContextData(BaseModel):
    context_content: str | None = Field(default="")


class ChatContext(BaseModel):
    """
    A ChatContext represents a chat context between a user and the multi-agent system.
    It tracks session metadata like creation time, user info, and context content.
    Multiple ChatContext objects can belong to one TaskSession during a conversation.
    """

    memory_id: str
    user_name: str
    session_id: str
    context_data: ContextData | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    extend_info: Any | None = None


class ConversationTurn(BaseModel):
    """
    A single turn in the conversation (ChatGPT/Claude style).
    Represents either a user message or an agent response.
    """

    role: Literal["user", "agent"]
    content: str  # Clean text, no raw @mention UUIDs
    agent_id: str | None = None  # Only for agent messages
    agent_name: str | None = None  # Only for agent messages
    user_id: str | None = None  # Only for user messages
    timestamp: datetime = Field(default_factory=utcnow)


class MemoryContent(BaseModel):
    """
    Room conversation memory with structured history.
    Similar to ChatGPT/Claude conversation context management.
    """

    # Summarized older context (when history exceeds window)
    summary: str | None = None

    # Recent conversation turns (sliding window, e.g., last 20 turns)
    conversation_history: list[ConversationTurn] = Field(default_factory=list)

    # Legacy field (for backward compatibility/migration)
    memory_text: str | None = None


class RoomMemory(BaseModel):
    room_id: str
    memory_id: str
    memory_content: MemoryContent = Field(default_factory=MemoryContent)
    memory_created_at: datetime = Field(default_factory=utcnow)
    extend_info: Any | None = None
