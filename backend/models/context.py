"""
Context models for session management.

This module defines:
- SessionContext: Ephemeral context for a single request processing cycle

Token budget configuration is in models/context_config.py (property-based, §14.3).

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.1 for design details.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from common.utils.time import utcnow

if TYPE_CHECKING:
    pass


class SessionContext(BaseModel):
    """
    Ephemeral context for a single request processing cycle.

    Created when user sends message → Destroyed after all agents respond.

    NOTE: There is no SupervisorPlan — uses an adaptive loop.
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

    # Supervisor state (if multi-agent)
    # The trajectory is the single source of truth for the adaptive loop.
    supervisor_trajectory: Any | None = None  # Actually SupervisorTrajectory

    # Snapshot of room history passed to the supervisor LLM prompt.
    # Built once in _prepare_for_supervisor() and FROZEN for the loop duration.
    # Agent results written during the loop are NOT reflected here — they come
    # through trajectory_summary in the supervisor prompt instead.
    conversation_context: str | None = None

    # Token tracking
    estimated_tokens: int = 0
    max_tokens: int = 128000  # Model-specific, overridden from settings
