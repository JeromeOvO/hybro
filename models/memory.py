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
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from common.utils.time import utcnow

if TYPE_CHECKING:
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

    # COMPACT representation: pointer to storage (typed Any to avoid circular import;
    # coerced to ContentReference at validation time via _coerce_content_ref below)
    content_ref: Any | None = None

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

    @model_validator(mode="before")
    @classmethod
    def _coerce_content_ref(cls, values: Any) -> Any:
        """Coerce content_ref from raw dict to ContentReference on deserialization.

        content_ref is typed as Any to avoid a circular import between memory.py
        and compaction.py.  When Pydantic constructs a ConversationTurn from a
        MongoDB document (dict), it leaves content_ref as a plain dict.  This
        validator converts it back to a ContentReference so that downstream code
        (.storage_type, .to_compact_string(), expand_content_reference()) works.
        """
        if isinstance(values, dict):
            ref = values.get("content_ref")
            if isinstance(ref, dict):
                from models.compaction import ContentReference

                values["content_ref"] = ContentReference(**ref)
        return values

    @model_validator(mode="after")
    def _validate_content_for_representation(self) -> "ConversationTurn":
        """Enforce that FULL-representation turns always have content set."""
        if (
            self.representation == TurnRepresentation.FULL
            and self.content is None
        ):
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
            from models.compaction import ContentReference

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
    key_decisions: list[str] = Field(default_factory=list)  # Decisions that should persist
    open_questions: list[str] = Field(default_factory=list)  # Unresolved questions or blockers
    recent_agent_contributions: list[str] = Field(
        default_factory=list
    )  # Last 3-5 agent result summaries
    important_constraints: list[str] = Field(default_factory=list)  # Hard constraints stated

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

    NOTE: This is the legacy structure. New code should use RoomMemory.conversation_history
    directly. This class is kept for backward compatibility during migration.
    """

    # Summarized older context (when history exceeds window)
    summary: str | None = None

    # Recent conversation turns (sliding window, e.g., last 20 turns)
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

    # Legacy: nested MemoryContent (kept for backward compatibility during migration)
    # New code should use conversation_history directly when memory_content is None
    memory_content: MemoryContent | None = Field(default_factory=MemoryContent)

    # NEW: Direct conversation history (mix of full and compact representations)
    # During migration, this may be None while memory_content is populated
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
        """
        Get conversation history, handling both legacy and new structures.

        Returns conversation_history if populated, otherwise falls back to
        memory_content.conversation_history for backward compatibility.
        """
        if self.conversation_history:
            return self.conversation_history
        if self.memory_content and self.memory_content.conversation_history:
            return self.memory_content.conversation_history
        return []

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
    preferred_agents: list[str] = Field(default_factory=list)  # agent_ids user frequently uses
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
