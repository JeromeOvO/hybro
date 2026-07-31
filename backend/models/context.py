"""
Context models for session management.

This module defines:
- SessionContext: Ephemeral context for a single request processing cycle

Token budget configuration is in models/context_config.py (property-based, §14.3).

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.1 for design details.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from common.utils.time import utcnow

if TYPE_CHECKING:
    pass


class SessionContext(BaseModel):
    """
    Ephemeral context for a single request processing cycle.

    Created when user sends message → Destroyed after all agents respond.

    NOTE: There is no SupervisorPlan — the adaptive loop uses the durable
    OrchestrationRunState as its single source of truth.

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §4.1 for specification.
    """

    session_id: str
    room_id: str
    user_id: str

    # Current request
    user_message: str
    user_message_id: str
    created_at: datetime = Field(default_factory=utcnow)

    # Snapshot of room history passed to the supervisor LLM prompt. Agent
    # results are read from the durable orchestration state on every step.
    conversation_context: str | None = None

    # Token tracking
    estimated_tokens: int = 0
    max_tokens: int = 128000  # Model-specific, overridden from settings
