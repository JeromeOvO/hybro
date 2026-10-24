from typing import Any

from pydantic import Field

from common.dto.base import FrozenDTO


class CompactionResult(FrozenDTO):
    conversation_id: str
    compacted_count: int
    tokens_saved: int
    memory_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "CompactionResult",
]
