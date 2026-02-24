"""
Context models for session management and token budgeting.

This module defines:
- SessionContext: Ephemeral context for a single request processing cycle
- TokenBudget: Token allocation for context assembly

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.1 and §5.2 for design details.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from common.utils.time import utcnow

if TYPE_CHECKING:
    from models.supervisor_v2 import SupervisorTrajectory


class SessionContext(BaseModel):
    """
    Ephemeral context for a single request processing cycle.

    Created when user sends message → Destroyed after all agents respond.

    NOTE: There is no SupervisorPlan — V2 uses an adaptive loop.
    The trajectory (all actions + results so far) is the single source of truth.
    It lives in user_message.extend_info.supervisor_trajectory (SupervisorTrajectory).

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.1 for specification.
    """

    session_id: str
    room_id: str
    user_id: str

    # Current request
    user_message: str
    user_message_id: str
    created_at: datetime = Field(default_factory=utcnow)

    # Supervisor V2 state (if multi-agent)
    # The trajectory is the single source of truth for the adaptive loop.
    supervisor_trajectory: Any | None = None  # Actually SupervisorTrajectory

    # Snapshot of room history passed to the supervisor LLM prompt.
    # Built once in _prepare_for_supervisor_v2() and FROZEN for the loop duration.
    # Agent results written during the loop are NOT reflected here — they come
    # through trajectory_summary in the supervisor prompt instead.
    conversation_context: str | None = None

    # Token tracking
    estimated_tokens: int = 0
    max_tokens: int = 128000  # Model-specific, overridden from settings


class TokenBudget(BaseModel):
    """
    Token allocation for context assembly.

    Defines how the context window is divided among different components.
    Values are loaded from environment variables via settings.

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §5.2 for specification.
    """

    model_context_window: int = 128000

    # Fixed allocations
    system_prompt: int = 2000
    tool_schemas: int = 3000
    response_reserve: int = 4000

    # Dynamic allocations (percentages of remaining)
    room_context_pct: float = 0.15  # Room facts, agent roster
    conversation_history_pct: float = 0.60  # Full + compact turns
    current_task_pct: float = 0.25  # Current request + step context

    @property
    def available_for_content(self) -> int:
        """Calculate tokens available for dynamic content after fixed allocations."""
        return self.model_context_window - (
            self.system_prompt + self.tool_schemas + self.response_reserve
        )

    @property
    def room_context_tokens(self) -> int:
        """Tokens allocated for room context (facts, agent roster)."""
        return int(self.available_for_content * self.room_context_pct)

    @property
    def conversation_history_tokens(self) -> int:
        """Tokens allocated for conversation history."""
        return int(self.available_for_content * self.conversation_history_pct)

    @property
    def current_task_tokens(self) -> int:
        """Tokens allocated for current task/request."""
        return int(self.available_for_content * self.current_task_pct)

    def get_budget_summary(self) -> dict[str, int]:
        """Get a summary of token allocations."""
        return {
            "model_context_window": self.model_context_window,
            "system_prompt": self.system_prompt,
            "tool_schemas": self.tool_schemas,
            "response_reserve": self.response_reserve,
            "available_for_content": self.available_for_content,
            "room_context": self.room_context_tokens,
            "conversation_history": self.conversation_history_tokens,
            "current_task": self.current_task_tokens,
        }
