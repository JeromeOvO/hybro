from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TruncationReason(str, Enum):
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    TURN_COUNT_EXCEEDED = "turn_count_exceeded"
    CHAR_LIMIT_EXCEEDED = "char_limit_exceeded"


@dataclass(slots=True)
class ContentReferenceData:
    storage_type: str = "mongodb"
    collection: str | None = "conversation_content"
    document_id: str | None = None
    content_hash: str | None = None
    created_at: datetime | str | None = None
    s3_bucket: str | None = None
    s3_key: str | None = None
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
                "s3_bucket": self.s3_bucket,
                "s3_key": self.s3_key,
                "url": self.url,
            }.items()
            if value is not None
        }

    def to_compact_string(self) -> str:
        if self.storage_type == "mongodb":
            return f"[Content stored: db/{self.collection}/{self.document_id}]"
        if self.storage_type == "s3":
            return f"[Content stored: s3://{self.s3_bucket}/{self.s3_key}]"
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
    turn_notes: dict[str, Any] | None = None
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
            "turn_notes": self.turn_notes,
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

    def has_content(self) -> bool:
        return bool(
            self.current_goal
            or self.key_decisions
            or self.open_questions
            or self.recent_agent_contributions
            or self.important_constraints
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_goal": self.current_goal,
            "key_decisions": list(self.key_decisions),
            "open_questions": list(self.open_questions),
            "recent_agent_contributions": list(self.recent_agent_contributions),
            "important_constraints": list(self.important_constraints),
            "last_updated_at": self.last_updated_at,
            "updated_after_turn_id": self.updated_after_turn_id,
        }


@dataclass(slots=True)
class RoomMemoryState:
    room_id: str
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


@dataclass(slots=True)
class AssemblyResult:
    context: str
    total_tokens: int
    occupancy_pct: float
    was_truncated: bool
    truncation_reason: TruncationReason | None
    turns_included: int
    turns_truncated: int
    stable_prefix_tokens: int
    dynamic_suffix_tokens: int


@dataclass(slots=True)
class SearchRankingRecord:
    turn_id: str
    room_id: str
    content: str = ""
    keyword_score: float = 0.0
    raw_keyword_score: float | None = None
    relevance_score: float = 0.0
    temporal_decay_factor: float = 1.0
    timestamp: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
