"""
Memory search models for Mongo keyword retrieval.

This module defines:
- MemorySearchResult: Individual search result
- MemorySearchResponse: Collection of search results

Configuration is handled by models/context_config.py (MemorySearchConfig).
See CONTEXT_MEMORY_SYSTEM_DESIGN.md §8 for design details.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from common.utils.time import utcnow


class MemorySourceType(str, Enum):
    """
    Source type for memory search results.

    TURN: Result from conversation history
    FACT: Result from room_facts
    SUMMARY: Result from room_summary
    """

    TURN = "turn"
    FACT = "fact"
    SUMMARY = "summary"


class MemorySearchResult(BaseModel):
    """
    A single memory search result.

    Represents a turn or fact that matched the search query.
    """

    # Source identification
    turn_id: str | None = None  # If from conversation history
    fact_id: str | None = None  # If from room_facts
    room_id: str
    source_type: MemorySourceType

    # Content
    content: str  # The matched content or snippet
    content_preview: str | None = None  # Truncated preview for display

    # Scoring
    keyword_score: float = 0.0  # BM25 keyword score
    relevance_score: float = 0.0  # Keyword score after temporal decay
    temporal_decay_factor: float = 1.0  # Decay multiplier applied

    # Metadata
    timestamp: datetime | None = None
    role: str | None = (
        None  # "user", "agent", "supervisor" (uses memory.TurnRole values)
    )
    agent_name: str | None = None

    # For expansion
    is_compact: bool = False  # Whether this is a compact turn
    can_expand: bool = False  # Whether full content can be retrieved


class MemorySearchResponse(BaseModel):
    """
    Response from a memory search operation.
    """

    query: str
    room_id: str
    results: list[MemorySearchResult] = Field(default_factory=list)
    total_matches: int = 0  # Total matches before limit
    search_time_ms: float = 0.0
    searched_at: datetime = Field(default_factory=utcnow)

    # Search metadata
    keyword_search_used: bool = True
    temporal_decay_applied: bool = True
