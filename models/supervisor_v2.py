"""Supervisor V2 Data Models — adaptive loop orchestration.

These models support the V2 Supervisor's step-at-a-time adaptive loop.
They are intentionally separate from V1 models in ``models/supervisor.py``
to avoid conflicts during the migration period (Phases 1–4).

V1 models are removed in Phase 5 when V1 is deprecated.

See docs/SUPERVISOR_V2_DESIGN.md for full architecture details.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from common.utils.time import utcnow


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
    """Result of a completed (or paused) agent delegation.

    Named ``V2StepResult`` to avoid import conflicts with V1's ``StepResult``
    in ``models/supervisor.py`` during the migration period.
    """

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
