"""
Memory search models for hybrid retrieval.

This module defines:
- MemorySearchConfig: Configuration for hybrid search
- MemorySearchResult: Individual search result
- MemorySearchResponse: Collection of search results

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §8 for design details.
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from common.utils.time import utcnow

if TYPE_CHECKING:
    from models.memory import TurnRole


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


class MemorySearchConfig(BaseModel):
    """
    Configuration for memory search.

    Values are loaded from environment variables via settings.
    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §8.2 and §14.3 for specification.
    """

    enabled: bool = True
    vector_weight: float = 0.7  # Weight for semantic similarity
    keyword_weight: float = 0.3  # Weight for BM25 keyword matching
    temporal_decay_enabled: bool = True  # Enable recency boost
    half_life_days: int = 30  # Days for score to decay 50%
    mmr_lambda: float = 0.7  # Diversity vs relevance tradeoff (0=diverse, 1=relevant)
    max_results: int = 10  # Maximum results returned
    max_snippet_chars: int = 500  # Max chars per snippet
    index_name: str = "room-memory"  # Pinecone index for memory


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
    vector_score: float = 0.0  # Semantic similarity score
    keyword_score: float = 0.0  # BM25 keyword score
    combined_score: float = 0.0  # Final weighted score
    temporal_decay_factor: float = 1.0  # Decay multiplier applied

    # Metadata
    timestamp: datetime | None = None
    role: str | None = None  # "user", "agent", "supervisor" (uses memory.TurnRole values)
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
    vector_search_used: bool = True
    keyword_search_used: bool = True
    temporal_decay_applied: bool = True
    mmr_applied: bool = True
