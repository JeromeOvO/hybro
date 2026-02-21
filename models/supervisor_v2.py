"""Supervisor V2 Data Models — adaptive loop orchestration.

These models support the V2 Supervisor's step-at-a-time adaptive loop.
Since Phase 5, V1 models have been removed and this is the sole supervisor
model module.

See docs/SUPERVISOR_V2_DESIGN.md for full architecture details.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from common.utils.time import utcnow

if TYPE_CHECKING:
    from models.agent import Agent, AgentStatus


# =========================================================================
# Shared models (formerly in models/supervisor.py)
# =========================================================================


class AgentProfile(BaseModel):
    """Compact agent description for the Supervisor's context window.

    Contains only the information the Supervisor needs to make routing decisions.
    """

    agent_id: str
    agent_name: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    success_rate: float = 1.0
    is_healthy: bool = True

    @classmethod
    def from_agent(cls, agent: "Agent") -> "AgentProfile":
        """Create an AgentProfile from a full Agent model."""
        from models.agent import AgentStatus

        card = agent.agent_card
        total = max(agent.call_count or 0, 1)
        raw_rate = (agent.call_success_count or 0) / total
        return cls(
            agent_id=agent.agent_id,
            agent_name=card.name,
            description=card.description or "",
            capabilities=[s.id for s in (card.skills or [])],
            success_rate=max(0.0, min(1.0, raw_rate)),
            is_healthy=agent.agent_status == AgentStatus.active,
        )


class RoomConfig(BaseModel):
    """Room configuration relevant to the Supervisor."""

    is_debate_mode: bool = False
    room_agent_set: dict[str, str] = Field(default_factory=dict)


# =========================================================================
# Action types (LLM output)
# =========================================================================


class ActionType(StrEnum):
    DELEGATE = "delegate"
    SYNTHESIZE = "synthesize"
    CLARIFY = "clarify"
    DONE = "done"


class DelegateTarget(BaseModel):
    """A single agent delegation within a DELEGATE action."""

    agent_id: str
    agent_name: str
    task: str


class SupervisorAction(BaseModel):
    """Single next-action decision produced by the Supervisor LLM."""

    action: ActionType
    reasoning: str

    # DELEGATE fields
    targets: list[DelegateTarget] = Field(default_factory=list)

    # SYNTHESIZE fields
    synthesis_instruction: str | None = None

    # CLARIFY fields
    clarification_question: str | None = None


# =========================================================================
# Step result
# =========================================================================


class StepStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    PAUSED = "paused"


class V2StepResult(BaseModel):
    """Result of a completed (or paused) agent delegation."""

    step_number: int
    agent_id: str
    agent_name: str
    task: str
    response_text: str
    success: bool = True
    error_message: str | None = None
    completed_at: datetime = Field(default_factory=utcnow)

    status: StepStatus = StepStatus.SUCCESS
    paused_message_id: str | None = None
    agent_message_id: str | None = None


# =========================================================================
# Trajectory
# =========================================================================


class TrajectoryEntry(BaseModel):
    """One step in the execution trajectory.

    Created for ALL action types (DELEGATE, SYNTHESIZE, CLARIFY, DONE),
    not just DELEGATE.  This ensures the trajectory is a complete audit
    log of every supervisor decision.
    """

    step_number: int
    action: SupervisorAction
    results: list[V2StepResult] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None


class SupervisorTrajectory(BaseModel):
    """Full execution trajectory for a user message.

    Stored in ``user_message.extend_info.supervisor_trajectory`` for
    auditability.
    """

    trajectory_id: str = Field(default_factory=lambda: uuid4().hex)
    entries: list[TrajectoryEntry] = Field(default_factory=list)
    status: Literal["running", "completed", "failed", "canceled", "clarifying"] = "running"
    total_supervisor_calls: int = 0
    created_at: datetime = Field(default_factory=utcnow)

    clarify_user_reply: str | None = None
    """The user's reply to a CLARIFY question.  Set by the clarify-resume
    path before calling ``SupervisorExecutor.run(resumed_trajectory=...)``.
    The supervisor prompt formatter includes this so the LLM knows the
    user answered."""

    clarify_original_message_id: str | None = None
    """The ``user_message_id`` of the message that originally triggered the
    CLARIFY action.  Carried through pause/resume so that
    ``_handle_v2_run_result`` can update the original message's trajectory
    status even when the clarify-resume itself gets paused by a push
    notification and later resumes via the webhook path."""


# =========================================================================
# Run result
# =========================================================================


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"
    CLARIFYING = "clarifying"


class SupervisorRunResult(BaseModel):
    status: RunStatus
    trajectory: SupervisorTrajectory
    synthesis_text: str | None = None
    clarification_question: str | None = None
