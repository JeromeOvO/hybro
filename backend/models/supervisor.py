"""Supervisor data models — adaptive loop orchestration.

These models support the supervisor's step-at-a-time adaptive loop.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, Field

from common.utils.a2a_file_modes import agent_input_modes, agent_supports_any_file
from common.utils.time import utcnow
from models.orchestration import DispatchContentRef, DispatchExpectedOutput

if TYPE_CHECKING:
    from models.agent import Agent

# =========================================================================
# Shared models
# =========================================================================


class AgentProfile(BaseModel):
    """Compact agent description for the Supervisor's context window.

    Contains only the information the Supervisor needs to make routing decisions.
    """

    agent_id: str
    agent_name: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=list)
    supports_file_upload: bool = False
    success_rate: float = 1.0
    is_healthy: bool = True

    @classmethod
    def from_agent(cls, agent: Agent) -> AgentProfile:
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
            input_modes=sorted(agent_input_modes(card)),
            output_modes=[
                str(mode)
                for mode in (getattr(card, "default_output_modes", None) or [])
                if str(mode)
            ],
            supports_file_upload=agent_supports_any_file(card),
            success_rate=max(0.0, min(1.0, raw_rate)),
            is_healthy=agent.agent_status == AgentStatus.active,
        )


class RoomConfig(BaseModel):
    """Room configuration relevant to the Supervisor."""

    is_debate_mode: bool = False
    room_agent_set: dict[str, str] = Field(default_factory=dict)
    explicit_mentions: list[dict] = Field(default_factory=list)


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
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: str | None = None
    required_resource_refs: list[str] = Field(default_factory=list)
    context_refs: list[DispatchContentRef] = Field(default_factory=list)
    artifact_refs: list[DispatchContentRef] = Field(default_factory=list)
    attachment_refs: list[DispatchContentRef] = Field(default_factory=list)
    expected_outputs: list[DispatchExpectedOutput] = Field(default_factory=list)
    attachment_policy: str = "explicit_refs_only"


class ClarifyQuestion(BaseModel):
    """A single question within a CLARIFY action's questions array."""

    prompt: str
    prompt_type: str | None = None
    choices: list[str] | None = None
    blocker_keys: list[str] = Field(default_factory=list)
    required_obligation_keys: list[str] = Field(default_factory=list)
    blocker_obligations: dict[str, list[str]] = Field(default_factory=dict)


class SupervisorAction(BaseModel):
    """Single next-action decision produced by the Supervisor LLM."""

    action: ActionType
    reasoning: str

    # DELEGATE fields
    targets: list[DelegateTarget] = Field(default_factory=list)

    # SYNTHESIZE fields
    synthesis_instruction: str | None = None

    # CLARIFY fields (used for all supervisor questions, pre-plan or mid-loop)
    clarification_question: str | None = None
    prompt_type: str | None = None
    choices: list[str] | None = None
    questions: list[ClarifyQuestion] | None = None


# =========================================================================
# Step result
# =========================================================================


class StepStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    PAUSED = "paused"
    AWAITING_INPUT = "awaiting_input"


class StepResult(BaseModel):
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

    # HITL fields (populated when status == AWAITING_INPUT)
    a2a_task_id: str | None = None
    a2a_context_id: str | None = None
    status_message: str | None = None
    interactive_state: str | None = None
    requires_auth: bool = False
    requires_policy: bool = False


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
    results: list[StepResult] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None


class TrajectoryStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    CLARIFYING = "clarifying"  # deprecated: kept for legacy trajectory deserialization
    # Transient state used by the DB layer when claiming a stuck trajectory
    # for recovery; never set by the executor itself.
    RECOVERING = "recovering"
    AWAITING_INPUT = "awaiting_input"


class SupervisorTrajectory(BaseModel):
    """Full execution trajectory for a user message.

    Stored in ``user_message.extend_info.supervisor_trajectory`` for
    auditability.
    """

    trajectory_id: str = Field(default_factory=lambda: uuid4().hex)
    entries: list[TrajectoryEntry] = Field(default_factory=list)
    status: TrajectoryStatus = TrajectoryStatus.RUNNING
    total_supervisor_calls: int = 0
    created_at: datetime = Field(default_factory=utcnow)

    debate_agent_ids: list[str] | None = None

    clarify_user_reply: str | None = None
    """The user's reply to a CLARIFY question.  Set by the clarify-resume
    path before calling ``SupervisorExecutor.run(resumed_trajectory=...)``.
    The supervisor prompt formatter includes this so the LLM knows the
    user answered."""

    clarify_original_message_id: str | None = None
    """The ``user_message_id`` of the message that originally triggered the
    clarification workflow. Used only during the clarify-resume path so
    we can cross-reference the original question if needed."""

    system_agent_message_id: str | None = None
    """The message ID of the orchestrator task (system:hybro). Stored here so it
    can be reused across PAUSED interrupts when the loop resumes."""

    hitl_user_reply: str | None = None
    """The user's reply to a CLARIFY question (pre-plan or mid-loop).
    Set by _handle_supervisor_response() before calling
    resume_queue_from_continuation(). Replaces clarify_user_reply for
    new HITL flows. Resume code reads both for backward compat."""

    hitl_original_message_id: str | None = None
    """The user_message_id of the message whose loop was paused by CLARIFY.
    Replaces clarify_original_message_id for new HITL flows."""


# =========================================================================
# Run result
# =========================================================================


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    PAUSED = "paused"
    CLARIFYING = "clarifying"  # deprecated: kept for legacy backward compat
    AWAITING_INPUT = "awaiting_input"


class SupervisorRunResult(BaseModel):
    status: RunStatus
    trajectory: SupervisorTrajectory | None = None
    run_id: str | None = None
    run_state: Any | None = None
    synthesis_text: str | None = None
    clarification_question: str | None = None
    terminal_reason: str | None = None
