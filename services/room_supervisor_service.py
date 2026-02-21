"""Room Supervisor Service (V2 only)

Provides adaptive-loop orchestration for multi-agent chat rooms:
1. DECIDE_NEXT: Given the trajectory so far, decide the next action
2. SYNTHESIZE: Produce a unified response from collected agent results

V1 plan-and-execute methods (create_plan, review_step, synthesize_results)
were removed in Phase 5.

See docs/SUPERVISOR_V2_DESIGN.md for full architecture details.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.utils.logger import get_logger
from models.supervisor_v2 import (
    ActionType,
    AgentProfile,
    RoomConfig,
    SupervisorAction,
    SupervisorTrajectory,
)

if TYPE_CHECKING:
    from services.database_service import DatabaseService
    from services.openai_service import OpenAIService

logger = get_logger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class SupervisorPlanningError(Exception):
    """Raised when the Supervisor LLM fails on the first decide_next call.

    Callers should catch this and handle the error appropriately.
    """

    pass


# =============================================================================
# V2 Prompts (adaptive loop)
# =============================================================================

SUPERVISOR_V2_SYSTEM_PROMPT = """You are a Supervisor coordinating specialist agents in a chat room.

## Available Agents
{agent_registry}

## Your Job
Decide the NEXT action. You will be called repeatedly — once after each agent responds.
Output ONLY valid JSON matching the schema below.

## Action Types
1. DELEGATE: Send a task to one or more agents.
   - Single target: the agent works alone.
   - Multiple targets: they work concurrently on independent sub-tasks.
   - Write each task as a clear, specific instruction tailored for that agent.
   - Include relevant context from prior results when the agent needs it.
2. SYNTHESIZE: All needed agent results are collected. Produce a unified answer.
   - Only use when 2+ agents have responded and their results need combining.
3. CLARIFY: The user's message is ambiguous. Ask a clarification question.
   - Use sparingly — only when you truly cannot determine which agent to use.
4. DONE: The work is complete. No synthesis needed (e.g., single agent already answered fully).

## Rules
- Prefer DELEGATE with a single target unless sub-tasks are truly independent.
- After each agent result, evaluate quality. If inadequate, you can delegate to the
  same agent with a refined task — no special "retry" mechanism needed.
- If an agent's result changes what you planned to do next, simply adapt.
- Do NOT delegate to agents that are unhealthy (status: unhealthy).
- You have a maximum of {max_steps} actions. Use SYNTHESIZE or DONE before the limit.

## Output Schema
{{
  "action": "delegate" | "synthesize" | "clarify" | "done",
  "reasoning": "Brief explanation",
  "targets": [
    {{"agent_id": "uuid", "agent_name": "Name", "task": "What to do"}}
  ],
  "synthesis_instruction": "How to combine results" | null,
  "clarification_question": "What to ask the user" | null
}}"""

SUPERVISOR_V2_USER_PROMPT = """## Conversation Context
{conversation_context}

{debate_mode_note}

## User Message
{message_text}

## Execution So Far
{trajectory_summary}

## What should happen next?"""

SUPERVISOR_V2_SYNTHESIS_SYSTEM_PROMPT = """You are synthesizing the results from multiple specialist agents into a single coherent response for the user.

## Execution Trajectory
{trajectory_summary}

## Synthesis Instructions
{synthesis_instruction}

## Rules
- Attribute insights to their source agent when helpful: "According to [Agent Name]..."
- Resolve contradictions by noting both perspectives.
- If one agent failed, note what was successfully completed and what was not.
- Be concise. The user has already seen each agent's individual response.
- Focus on the unified answer, not a recap of each agent's full response.
"""


class RoomSupervisorService:
    """Supervisor for multi-agent room orchestration (V2 adaptive loop).

    Responsibilities:
    1. DECIDE_NEXT: Analyze trajectory + agent registry -> SupervisorAction
    2. SYNTHESIZE: Combine agent results into a unified response
    """

    def __init__(
        self,
        openai_service: "OpenAIService | None" = None,
        database_service: "DatabaseService | None" = None,
    ):
        if openai_service is None:
            from services.openai_service import openai_service as _openai_service

            self._openai_service = _openai_service
        else:
            self._openai_service = openai_service

        if database_service is None:
            from services.database_service import db_service

            self._database_service = db_service
        else:
            self._database_service = database_service

    # =========================================================================
    # Agent registry formatting
    # =========================================================================

    @staticmethod
    def _format_agent_registry(agents: list[AgentProfile]) -> str:
        """Format agent profiles for the Supervisor prompt."""
        lines = []
        for agent in agents:
            capabilities = ", ".join(agent.capabilities) if agent.capabilities else "General"
            health = "healthy" if agent.is_healthy else "unhealthy"
            lines.append(
                f"- {agent.agent_name} (ID: {agent.agent_id})\n"
                f"  Description: {agent.description}\n"
                f"  Capabilities: {capabilities}\n"
                f"  Success Rate: {agent.success_rate:.0%}, Status: {health}"
            )
        return "\n".join(lines)

    # =========================================================================
    # V2: Adaptive Loop — decide_next / synthesize
    # =========================================================================

    async def decide_next(
        self,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        trajectory: SupervisorTrajectory,
        conversation_context: str | None = None,
        max_steps: int = 8,
    ) -> SupervisorAction:
        """Ask the Supervisor LLM for the next action (V2 adaptive loop).

        Called once per loop iteration by the ``SupervisorExecutor``.

        Returns:
            ``SupervisorAction`` — what to do next.
            On LLM failure with no prior entries, raises
            ``SupervisorPlanningError`` so the caller can surface the error.
            On LLM failure with prior entries, returns a DONE action (fail-open).
        """
        try:
            agent_registry_str = self._format_agent_registry(agent_registry)
            system_prompt = SUPERVISOR_V2_SYSTEM_PROMPT.format(
                agent_registry=agent_registry_str,
                max_steps=max_steps,
            )

            debate_note = ""
            if room_config.is_debate_mode:
                debate_note = (
                    "## Room Mode\n"
                    "DEBATE MODE is enabled. Delegate the SAME user message to ALL "
                    "agents concurrently as a single multi-target DELEGATE. Each agent "
                    "must respond independently. Do NOT synthesize — use DONE after all "
                    "agents respond."
                )

            trajectory_summary = self._format_trajectory(trajectory)
            user_prompt = SUPERVISOR_V2_USER_PROMPT.format(
                conversation_context=conversation_context or "No prior conversation.",
                debate_mode_note=debate_note,
                message_text=message_text,
                trajectory_summary=trajectory_summary,
            )

            response_json = await self._call_supervisor_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            action = self._parse_v2_action(response_json)

            logger.info(
                "Supervisor V2 decide_next",
                extra={
                    "action": action.action,
                    "reasoning": action.reasoning[:100],
                    "target_count": len(action.targets),
                },
            )

            return action

        except Exception as e:
            logger.warning("Supervisor V2 decide_next failed: %s", e)
            if not trajectory.entries:
                raise SupervisorPlanningError(str(e)) from e
            return SupervisorAction(
                action=ActionType.DONE,
                reasoning=f"Supervisor failed ({e}), stopping with current results",
            )

    async def synthesize_v2(
        self,
        trajectory: SupervisorTrajectory,
        synthesis_instruction: str,
    ) -> str:
        """Produce a synthesis from collected results (V2 adaptive loop).

        Called when ``decide_next`` returns SYNTHESIZE, or when the step
        budget is exhausted and results need combining.
        """
        trajectory_summary = self._format_trajectory(trajectory)
        system_prompt = SUPERVISOR_V2_SYNTHESIS_SYSTEM_PROMPT.format(
            trajectory_summary=trajectory_summary,
            synthesis_instruction=synthesis_instruction
            or "Combine the agent responses into a unified, coherent answer.",
        )
        user_prompt = "Synthesize the agent results into a unified response for the user."

        try:
            response = await self._call_supervisor_llm_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            logger.info(
                "Supervisor V2 synthesis completed",
                extra={
                    "trajectory_id": trajectory.trajectory_id,
                    "synthesis_length": len(response),
                },
            )
            return response
        except Exception as e:
            logger.error("Supervisor V2 synthesis failed: %s", e)
            return self._fallback_v2_synthesis(trajectory)

    # -------------------------------------------------------------------------
    # V2 helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _format_trajectory(trajectory: SupervisorTrajectory) -> str:
        """Format the trajectory for inclusion in the supervisor prompt."""
        if not trajectory.entries:
            return "No actions taken yet."

        lines: list[str] = []
        for entry in trajectory.entries:
            lines.append(
                f"### Step {entry.step_number}: {entry.action.action.upper()}"
            )
            if entry.action.action == ActionType.DELEGATE:
                for target in entry.action.targets:
                    lines.append(f"  Delegated to {target.agent_name}: {target.task}")
                for result in entry.results:
                    status = (
                        "SUCCESS"
                        if result.success
                        else f"FAILED: {result.error_message}"
                    )
                    lines.append(
                        f"  → {result.agent_name} [{status}]: "
                        f"{result.response_text[:500]}"
                    )
            elif entry.action.action == ActionType.CLARIFY:
                lines.append(
                    f"  Asked user: {entry.action.clarification_question}"
                )
                if trajectory.clarify_user_reply:
                    lines.append(
                        f"  User replied: {trajectory.clarify_user_reply}"
                    )
            elif entry.action.action == ActionType.SYNTHESIZE:
                lines.append(
                    f"  Instruction: {entry.action.synthesis_instruction}"
                )
            elif entry.action.action == ActionType.DONE:
                lines.append(f"  Reasoning: {entry.action.reasoning}")

        return "\n".join(lines)

    def _parse_v2_action(self, response_json: dict) -> SupervisorAction:
        """Parse the LLM JSON response into a ``SupervisorAction``."""
        from models.supervisor_v2 import DelegateTarget

        action_str = response_json.get("action", "done")
        try:
            action_type = ActionType(action_str)
        except ValueError:
            logger.warning(
                "Supervisor V2: unknown action '%s', defaulting to DONE",
                action_str,
            )
            action_type = ActionType.DONE

        targets = []
        for t in response_json.get("targets") or []:
            if isinstance(t, dict) and "agent_id" in t:
                targets.append(
                    DelegateTarget(
                        agent_id=t["agent_id"],
                        agent_name=t.get("agent_name", "Unknown"),
                        task=t.get("task", ""),
                    )
                )

        return SupervisorAction(
            action=action_type,
            reasoning=response_json.get("reasoning", ""),
            targets=targets,
            synthesis_instruction=response_json.get("synthesis_instruction"),
            clarification_question=response_json.get("clarification_question"),
        )

    @staticmethod
    def _fallback_v2_synthesis(trajectory: SupervisorTrajectory) -> str:
        """Simple fallback synthesis when the LLM call fails."""
        lines = ["Here's a summary of the agent responses:\n"]
        for entry in trajectory.entries:
            for result in entry.results:
                if result.success:
                    lines.append(
                        f"**{result.agent_name}**: {result.response_text[:500]}"
                    )
                else:
                    lines.append(
                        f"**{result.agent_name}**: (Failed - {result.error_message})"
                    )
        return "\n\n".join(lines)

    # =========================================================================
    # LLM Helpers (delegate to openai_service)
    # =========================================================================

    async def _call_supervisor_llm(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        """Call the Supervisor LLM and return JSON response."""
        return await self._openai_service.call_supervisor_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    async def _call_supervisor_llm_text(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Call the Supervisor LLM and return text response (for synthesis)."""
        return await self._openai_service.call_supervisor_llm_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )


# Singleton instance
room_supervisor_service = RoomSupervisorService()
