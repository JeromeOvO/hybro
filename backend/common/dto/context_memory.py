from datetime import datetime
from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class ContextBlock(FrozenDTO):
    block_id: str
    room_id: str
    content: str
    token_count: int
    block_type: str = "context"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssembledContext(FrozenDTO):
    room_id: str
    blocks: list[ContextBlock] = Field(default_factory=list)
    total_tokens: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompactionResult(FrozenDTO):
    room_id: str
    compacted_count: int
    tokens_saved: int
    memory_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchResult(FrozenDTO):
    room_id: str
    content: str
    keyword_score: float
    relevance_score: float
    temporal_decay_factor: float
    memory_id: str | None = None
    source_message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoomMemoryInfo(FrozenDTO):
    room_id: str
    memory_id: str
    content: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    token_count: int | None = None


class UserMemory(FrozenDTO):
    user_id: str
    memory_id: str
    content: str
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AssembledContext",
    "CompactionResult",
    "ContextBlock",
    "MemorySearchResult",
    "RoomMemoryInfo",
    "UserMemory",
]
