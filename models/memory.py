from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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

