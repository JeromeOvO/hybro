"""
Supervisor Pattern Data Models

These models support the Room Supervisor Pattern for multi-agent orchestration.
The Supervisor produces structured execution plans, reviews step results,
and synthesizes multi-agent responses.

See docs/SUPERVISOR_PATTERN_DESIGN.md for full architecture details.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

from common.utils.time import utcnow

if TYPE_CHECKING:
    from models.agent import Agent, AgentStatus


class SupervisorStrategy(StrEnum):
    """Execution strategy for the Supervisor plan."""

    DIRECT = "direct"  # Single agent, direct response
    PARALLEL = "parallel"  # Multiple agents, independent tasks
    SEQUENTIAL = "sequential"  # Multiple agents, dependent tasks
    DEBATE = "debate"  # Multiple agents, contrasting perspectives
    CLARIFY = "clarify"  # Need more information from user


class ReviewAction(StrEnum):
    """Action to take after reviewing a completed step."""

    CONTINUE = "continue"  # Proceed with the next step
    REVISE = "revise"  # Replace remaining steps with revised plan
    RETRY = "retry"  # Re-execute the current step with refinement
    SKIP = "skip"  # Skip remaining steps, proceed to synthesis


class SupervisorStep(BaseModel):
    """A single step in the execution plan."""

    step_id: str  # e.g., "step_1"
    agent_id: str | None = None  # Which agent to delegate to (None = unresolved, needs auto-assignment)
    agent_name: str  # For display/logging
    task_description: str  # The prompt/task to send to the agent
    depends_on: list[str] = Field(default_factory=list)  # step_ids this depends on
    context_from_steps: list[str] = Field(
        default_factory=list
    )  # step_ids whose results should be included in prompt
    priority: int = 0  # For parallel execution ordering
    max_retries: int = 1  # How many times to retry on failure


class SupervisorPlan(BaseModel):
    """Structured execution plan produced by the Supervisor LLM.

    Stored in user_message.extend_info.supervisor_plan for auditability.
    """

    plan_id: str = Field(default_factory=lambda: uuid4().hex)
    strategy: SupervisorStrategy
    reasoning: str  # Why this strategy was chosen (for logging only)
    steps: list[SupervisorStep]
    synthesis_instruction: str | None = None  # How to combine results at the end
    created_at: datetime = Field(default_factory=utcnow)


class SupervisorReview(BaseModel):
    """Result of the Supervisor reviewing a completed step.

    Determines whether to continue with the plan, revise it, retry the step,
    or skip remaining steps.
    """

    action: ReviewAction
    reasoning: str
    revised_steps: list[SupervisorStep] | None = None  # Only if action == "revise"
    retry_with_refinement: str | None = None  # Refined prompt if action == "retry"


class AgentProfile(BaseModel):
    """Compact agent description for the Supervisor's context window.

    Contains only the information the Supervisor needs to make routing decisions.
    """

    agent_id: str
    agent_name: str
    description: str  # From agent_card.description
    capabilities: list[str] = Field(default_factory=list)  # From agent_card.skills
    success_rate: float = 1.0  # Computed from call_success_count / call_count
    is_healthy: bool = True  # From agent status check

    @classmethod
    def from_agent(cls, agent: "Agent") -> "AgentProfile":
        """Create an AgentProfile from a full Agent model."""
        from models.agent import AgentStatus

        card = agent.agent_card
        total = agent.call_count or 1
        return cls(
            agent_id=agent.agent_id,
            agent_name=card.name,
            description=card.description or "",
            capabilities=[s.id for s in (card.skills or [])],
            success_rate=agent.call_success_count / total,
            is_healthy=agent.agent_status == AgentStatus.active,
        )


class RoomConfig(BaseModel):
    """Room configuration relevant to the Supervisor.

    Extracted from Room model for Supervisor context.
    """

    is_debate_mode: bool = False
    room_agent_set: dict[str, str] = Field(
        default_factory=dict
    )  # {agent_id: agent_name}


class StepResult(BaseModel):
    """Result of a completed step, used for synthesis.

    Tracks the agent's response and metadata for the Supervisor's
    review and final synthesis phases.
    """

    step_id: str
    agent_id: str
    agent_name: str
    task_description: str
    response_text: str
    success: bool = True
    error_message: str | None = None
    completed_at: datetime = Field(default_factory=utcnow)
