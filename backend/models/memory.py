"""
Memory models for room conversation history and context management.

This module defines the core data structures for:
- Conversation turns (full and compact representations)
- Room memory with rolling summaries
- User and agent memory for cross-session learning

See CONTEXT_MEMORY_SYSTEM_DESIGN.md for design details.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from common.utils.context_utils import bind_context_turn_factory
from common.utils.time import utcnow
from models.compaction import ContentReference


class TurnRole(str, Enum):
    """
    Role of a conversation turn participant.

    Used in ConversationTurn.role.
    """

    USER = "user"
    AGENT = "agent"
    SUPERVISOR = "supervisor"


class TurnRepresentation(str, Enum):
    """
    Content representation mode for a conversation turn.

    FULL: Actual content stored in the `content` field
    COMPACT: Content stored externally, `content_ref` points to it

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.2 for specification.
    """

    FULL = "full"
    COMPACT = "compact"


class ContentType(str, Enum):
    """
    Type of content in a conversation turn.

    TEXT: Plain text message
    TOOL_RESULT: Result from a tool/function call
    AGENT_RESPONSE: Response from an agent

    Future extensions: IMAGE, FILE, VIDEO, AUDIO (see §6.8)
    """

    TEXT = "text"
    TOOL_RESULT = "tool_result"
    AGENT_RESPONSE = "agent_response"


class TurnType(str, Enum):
    """
    Semantic classification of a turn's purpose.

    MESSAGE: Normal conversation message (default)
    HITL_QUESTION: Agent/supervisor question requiring human input
    HITL_REPLY: User's response to a HITL question

    See HITL_DESIGN.md §10.1 for HITL turn recording details.
    """

    MESSAGE = "message"
    HITL_QUESTION = "hitl_question"
    HITL_REPLY = "hitl_reply"


class ConversationTurn(BaseModel):
    """
    A single turn in the conversation. Supports full and compact representations.

    Full representation: actual content stored in `content` field
    Compact representation: content stored externally, `content_ref` points to it

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §6.2 for canonical specification.
    """

    # Core identification
    turn_id: str = Field(default_factory=lambda: str(uuid4()))
    role: TurnRole
    agent_id: str | None = None  # Only for agent/supervisor messages
    agent_name: str | None = None  # Only for agent/supervisor messages
    user_id: str | None = None  # Only for user messages
    timestamp: datetime = Field(default_factory=utcnow)

    # Content representation mode
    representation: TurnRepresentation = TurnRepresentation.FULL

    # FULL representation: actual content in context
    content: str | None = None

    # COMPACT representation: pointer to external storage.
    # Pydantic v2 handles dict → ContentReference coercion natively.
    content_ref: ContentReference | None = None

    # Content metadata (always present regardless of representation)
    content_type: ContentType = ContentType.TEXT

    # Turn type — semantic classification of the turn's purpose
    # Most turns are "message" (default). HITL interactions use "hitl_question" and
    # "hitl_reply" to distinguish agent/supervisor questions and user responses.
    turn_type: TurnType = TurnType.MESSAGE

    # Token estimates — populated at turn creation time via estimate_tokens(content)
    # estimated_tokens_full MUST be set when the turn is created (not left at 0),
    # otherwise compaction triggers and budget accounting won't work correctly.
    estimated_tokens_full: int = 0  # Tokens if expanded; set at write time
    estimated_tokens_compact: int = 20  # Tokens as pointer (~20-50); static default

    # Optional brief summary for very old turns (50+). Populated at compaction time
    # as a human-readable hint alongside the pointer.
    brief_summary: str | None = None

    # Write-time structured note (Zettelkasten / A-MEM pattern)
    # Populated when the turn is first stored via extract_turn_notes(content).
    # Schema: {"keywords": [...], "entities": [...], "tags": [...], "one_liner": "..."}
    turn_notes: dict | None = None

    # Success tracking for learning from failures (§2.1 Principle 4: Preserve Errors)
    # None = unknown/not applicable, True = successful, False = failed
    # Used by compaction to prioritize retaining failed turns for model learning
    was_successful: bool | None = None

    @model_validator(mode="after")
    def _validate_content_for_representation(self) -> "ConversationTurn":
        """Enforce that FULL-representation turns always have content set."""
        if self.representation == TurnRepresentation.FULL and self.content is None:
            raise ValueError(
                f"ConversationTurn {self.turn_id}: content must not be None "
                f"when representation is FULL"
            )
        return self

    def to_context_string(self) -> str:
        """
        Render this turn for inclusion in a context window.

        Full turns render their content directly.
        Compact turns render a pointer string with optional brief_summary.
        """
        role_prefix = self._get_role_prefix()

        if self.representation == TurnRepresentation.FULL and self.content:
            return f"{role_prefix}: {self.content}"

        # Compact representation
        if self.content_ref:
            if isinstance(self.content_ref, ContentReference):
                pointer = self.content_ref.to_compact_string()
            else:
                pointer = "[Content stored externally]"
        else:
            pointer = "[Content unavailable]"

        if self.brief_summary:
            return f"{role_prefix}: {self.brief_summary} {pointer}"
        return f"{role_prefix}: {pointer}"

    def _get_role_prefix(self) -> str:
        """Get the display prefix for this turn's role."""
        if self.role == TurnRole.USER:
            return "User"
        elif self.role == TurnRole.AGENT:
            return self.agent_name or "Agent"
        elif self.role == TurnRole.SUPERVISOR:
            return "Supervisor"
        return "Unknown"


bind_context_turn_factory(ConversationTurn)


class RoomSummary(BaseModel):
    """
    Rolling structured summary of the room's current state — the "Knowledge Block"
    (Focus paper, arXiv 2601.07190). Maintained at each synthesis boundary and
    updated incrementally as conversations progress.

    Always kept FULL in context (it replaces scanning 50+ old turns).
    This is NOT a replacement for lossless compaction — it is a structured overlay
    that avoids the round-trip cost of fetch_turn_content for common context queries.

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.2 for specification.
    """

    # Structured named slots (agent-maintainable)
    current_goal: str | None = None  # What the user/room is trying to accomplish
    key_decisions: list[str] = Field(
        default_factory=list
    )  # Decisions that should persist
    open_questions: list[str] = Field(
        default_factory=list
    )  # Unresolved questions or blockers
    recent_agent_contributions: list[str] = Field(
        default_factory=list
    )  # Last 3-5 agent result summaries
    important_constraints: list[str] = Field(
        default_factory=list
    )  # Hard constraints stated

    # Metadata
    last_updated_at: datetime | None = None
    updated_after_turn_id: str | None = None  # Which turn triggered the last update


class RoomFact(BaseModel):
    """
    A durable fact extracted from room conversations.

    Facts are explicit statements that should persist across sessions,
    e.g., "User prefers Python over JavaScript" or "Project deadline is March 15".
    """

    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    content: str  # The fact statement
    source_turn_id: str | None = None  # Which turn this was extracted from
    confidence: float = 1.0  # Confidence score (0-1)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None  # Optional expiry for time-sensitive facts


# Type alias: design §4.3 specifies UserFact; structurally identical to RoomFact.
UserFact = RoomFact


class AgentSuccessRecord(BaseModel):
    """
    Track an agent's success history in a room.

    Used for agent selection optimization and failure pattern detection.
    """

    agent_id: str
    total_calls: int = 0
    successful_calls: int = 0
    last_called_at: datetime | None = None
    total_response_time_ms: float = 0.0
    average_response_time_ms: float = 0.0
    failure_reasons: list[str] = Field(default_factory=list)  # Recent failure reasons

    @model_validator(mode="before")
    @classmethod
    def _compute_average_response_time(cls, values: Any) -> Any:
        if isinstance(values, dict):
            total = values.get("total_response_time_ms", 0.0)
            calls = values.get("total_calls", 0)
            if calls > 0 and total > 0:
                values["average_response_time_ms"] = round(total / calls, 2)
        return values


class MemoryContent(BaseModel):
    """
    Room conversation memory with structured history.
    Similar to ChatGPT/Claude conversation context management.

    NOTE: This is a compatibility structure. Runtime persistence stores only
    ``summary`` here; conversation history is canonical on ``RoomMemory``.
    """

    # Summarized older context (when history exceeds window)
    summary: str | None = None

    # Compatibility-only input for legacy context helpers. Persistence adapters
    # must not serialize this nested history.
    conversation_history: list[ConversationTurn] = Field(default_factory=list)

    # Legacy field (for backward compatibility/migration)
    memory_text: str | None = None


class RoomMemory(BaseModel):
    """
    Durable memory for a chat room.

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.2 for specification.
    """

    room_id: str
    memory_id: str = Field(default_factory=lambda: str(uuid4()))

    # Nested compatibility content retains summary metadata only in persistence.
    memory_content: MemoryContent | None = Field(default_factory=MemoryContent)

    # Canonical conversation history (mix of full and compact representations).
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    max_history_turns: int = 100  # Total turns to keep (full + compact)

    # Rolling structured summary — always in context, updated at synthesis boundaries
    room_summary: RoomSummary = Field(default_factory=RoomSummary)

    # Room-level learned facts (extracted from conversations)
    room_facts: list[RoomFact] = Field(default_factory=list)

    # Agent interaction patterns
    agent_success_history: dict[str, AgentSuccessRecord] = Field(default_factory=dict)

    # Metadata
    memory_created_at: datetime = Field(default_factory=utcnow)
    last_activity_at: datetime = Field(default_factory=utcnow)
    total_messages: int = 0
    total_compactions: int = 0  # Number of compaction operations performed

    # Legacy field (kept for backward compatibility)
    extend_info: Any | None = None

    def get_conversation_history(self) -> list[ConversationTurn]:
        """Return the canonical top-level conversation history."""
        return self.conversation_history

    def get_summary(self) -> str | None:
        """
        Get the legacy summary string for backward compatibility.

        New code should use room_summary instead.
        """
        if self.memory_content:
            return self.memory_content.summary
        return None


class TaskTypeMetrics(BaseModel):
    """Metrics for a specific task type."""

    task_type: str
    total_calls: int = 0
    successful_calls: int = 0
    average_response_time_ms: float = 0.0


class FailurePattern(BaseModel):
    """A detected failure pattern for an agent."""

    pattern_id: str = Field(default_factory=lambda: str(uuid4()))
    description: str
    occurrence_count: int = 1
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)


class UserMemory(BaseModel):
    """
    Durable memory for a user across all rooms.

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.3 for specification.
    """

    user_id: str

    # User preferences (explicit)
    preferences: dict[str, Any] = Field(default_factory=dict)

    # Learned patterns
    preferred_agents: list[str] = Field(
        default_factory=list
    )  # agent_ids user frequently uses
    communication_style: str | None = None  # Detected style

    # Cross-room facts
    user_facts: list[UserFact] = Field(default_factory=list)

    # Metadata
    created_at: datetime = Field(default_factory=utcnow)
    last_active_at: datetime = Field(default_factory=utcnow)
    total_interactions: int = 0


class AgentMemory(BaseModel):
    """
    Durable memory for an agent's learned context.

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.4 for specification.
    """

    agent_id: str

    # Performance metrics
    total_calls: int = 0
    successful_calls: int = 0
    total_response_time_ms: float = 0.0
    average_response_time_ms: float = 0.0

    # Task type performance
    task_type_success: dict[str, TaskTypeMetrics] = Field(default_factory=dict)

    # Common failure patterns
    failure_patterns: list[FailurePattern] = Field(default_factory=list)

    # Metadata
    last_called_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _compute_avg_response_time(cls, values: Any) -> Any:
        if isinstance(values, dict):
            total = values.get("total_response_time_ms", 0.0)
            calls = values.get("total_calls", 0)
            if calls > 0 and total > 0:
                values["average_response_time_ms"] = round(total / calls, 2)
        return values
