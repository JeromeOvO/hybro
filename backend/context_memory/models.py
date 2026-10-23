from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ContentReferenceData:
    storage_type: str = "mongodb"
    collection: str | None = "conversation_content"
    document_id: str | None = None
    content_hash: str | None = None
    created_at: datetime | str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "storage_type": self.storage_type,
                "collection": self.collection,
                "document_id": self.document_id,
                "content_hash": self.content_hash,
                "created_at": self.created_at,
                "url": self.url,
            }.items()
            if value is not None
        }

    def to_compact_string(self) -> str:
        if self.storage_type == "mongodb":
            return f"[Content stored: db/{self.collection}/{self.document_id}]"
        if self.storage_type == "url":
            return f"[Content from: {self.url}]"
        return "[Content reference]"


@dataclass(slots=True)
class ConversationTurnData:
    turn_id: str
    role: str
    content: str | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    user_id: str | None = None
    timestamp: datetime | str | None = None
    representation: str = "full"
    content_ref: ContentReferenceData | None = None
    content_type: str = "text"
    turn_type: str = "message"
    estimated_tokens_full: int = 0
    estimated_tokens_compact: int = 20
    brief_summary: str | None = None
    was_successful: bool | None = None

    def role_prefix(self) -> str:
        if self.role == "user":
            return "User"
        if self.role == "agent":
            return self.agent_name or "Agent"
        if self.role == "supervisor":
            return "Supervisor"
        return "Unknown"

    def to_context_string(self) -> str:
        prefix = self.role_prefix()
        if self.representation == "full" and self.content:
            return f"{prefix}: {self.content}"
        pointer = (
            self.content_ref.to_compact_string()
            if self.content_ref is not None
            else "[Content unavailable]"
        )
        if self.brief_summary:
            return f"{prefix}: {self.brief_summary} {pointer}"
        return f"{prefix}: {pointer}"

    def to_dict(self) -> dict[str, Any]:
        data = {
            "turn_id": self.turn_id,
            "role": self.role,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
            "representation": self.representation,
            "content": self.content,
            "content_ref": self.content_ref.to_dict() if self.content_ref else None,
            "content_type": self.content_type,
            "turn_type": self.turn_type,
            "estimated_tokens_full": self.estimated_tokens_full,
            "estimated_tokens_compact": self.estimated_tokens_compact,
            "brief_summary": self.brief_summary,
            "was_successful": self.was_successful,
        }
        return {key: value for key, value in data.items() if value is not None}


@dataclass(slots=True)
class RoomSummaryData:
    current_goal: str | None = None
    key_decisions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    recent_agent_contributions: list[str] = field(default_factory=list)
    important_constraints: list[str] = field(default_factory=list)
    last_updated_at: datetime | str | None = None
    updated_after_turn_id: str | None = None


@dataclass(slots=True)
class RoomMemoryState:
    conversation_id: str
    memory_id: str
    conversation_history: list[ConversationTurnData] = field(default_factory=list)
    summary: str | None = None
    room_summary: RoomSummaryData = field(default_factory=RoomSummaryData)
    room_facts: list[dict[str, Any]] = field(default_factory=list)
    memory_created_at: datetime | str | None = None
    last_activity_at: datetime | str | None = None
    total_messages: int = 0
    total_compactions: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
