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
    StepStatus,
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
3. CLARIFY: The user's message is ambiguous or needs confirmation.
   - Use sparingly — only when you truly cannot proceed without user input.
   - NEVER re-ask questions the user has already answered. If the trajectory
     shows a CLARIFY step followed by a "User's Clarification Reply", treat
     those answers as final and proceed to DELEGATE or DONE.
   - After receiving user replies, you MUST move forward (DELEGATE or DONE).
     Do not ask follow-up clarification unless the user's answer is genuinely
     unintelligible or contradictory.
   - Put each question in a separate object inside the "questions" array.
     Each object has "prompt" (the question text), "prompt_type", and optional "choices".
   - "prompt_type" controls how the user responds:
     * "text"         — open-ended reply (default). Use when you need free-form input.
     * "choice"       — multiple-choice. Provide a "choices" array. Use when there are
                         clear, enumerable options (e.g., destinations, themes, formats).
     * "confirmation" — yes / no approval. Use when you need the user to approve or
                         reject a proposed plan or action before proceeding.
   - When you need multiple pieces of information, create a separate question for each.
     The user will see them as paginated cards and answer one at a time.
4. DONE: The work is complete. No synthesis needed (e.g., single agent already answered fully).

## Rules
- Prefer DELEGATE with a single target unless sub-tasks are truly independent.
- After each agent result, evaluate quality. If inadequate, you can delegate to the
  same agent with a refined task — no special "retry" mechanism needed.
- If an agent's result changes what you planned to do next, simply adapt.
- Do NOT delegate to agents that are unhealthy (status: unhealthy).
- You have a maximum of {max_steps} actions. Use SYNTHESIZE or DONE before the limit.
- You may CLARIFY at most once. After you receive the user's answers, you MUST
  proceed with DELEGATE — do not issue another CLARIFY.

## Room Conversation Background
{conversation_context}

## Output Schema
{{
  "action": "delegate" | "synthesize" | "clarify" | "done",
  "reasoning": "Brief explanation",
  "targets": [
    {{"agent_id": "uuid", "agent_name": "Name", "task": "What to do"}}
  ],
  "synthesis_instruction": "How to combine results" | null,
  "questions": [
    {{"prompt": "Your question", "prompt_type": "text" | "choice" | "confirmation", "choices": ["A", "B"] | null}}
  ] | null
}}"""

SUPERVISOR_V2_USER_PROMPT = """{debate_mode_note}

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
                conversation_context=conversation_context or "No prior conversation.",
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
            completed_results = [
                r
                for entry in trajectory.entries
                for r in entry.results
                if r.success and r.status == StepStatus.SUCCESS
            ]
            if len(completed_results) >= 2:
                return SupervisorAction(
                    action=ActionType.SYNTHESIZE,
                    reasoning=f"Supervisor failed ({e}), synthesizing available results",
                    synthesis_instruction="The supervisor encountered an error. Synthesize the available agent results into a coherent response.",
                )
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

    # Maximum number of recent trajectory entries to include in full detail.
    # Older entries are summarized to prevent the prompt from growing unbounded.
    _TRAJECTORY_WINDOW: int = 5

    @classmethod
    def _format_trajectory(
        cls,
        trajectory: SupervisorTrajectory,
        *,
        window: int | None = None,
    ) -> str:
        """Format the trajectory for inclusion in the supervisor prompt.

        When the trajectory has more than ``window`` entries, older entries
        are collapsed into a one-line summary to keep the prompt within
        reasonable token limits.
        """
        if window is None:
            window = cls._TRAJECTORY_WINDOW
        if not trajectory.entries:
            return "No actions taken yet."

        entries = trajectory.entries
        lines: list[str] = []

        if len(entries) > window:
            older = entries[: len(entries) - window]
            summary_parts: list[str] = []
            for e in older:
                action_type = e.action.action.upper()
                if e.results:
                    for r in e.results:
                        if r.status == StepStatus.PAUSED:
                            tag = f"{r.agent_name}(PAUSED)"
                        elif r.success:
                            tag = r.agent_name
                        else:
                            tag = f"{r.agent_name}(FAILED)"
                        summary_parts.append(tag)
                elif action_type == "CLARIFY":
                    summary_parts.append("CLARIFY(answered)")
                elif action_type == "DONE":
                    summary_parts.append("DONE")
                else:
                    summary_parts.append(action_type)
            summary_text = ", ".join(summary_parts) if summary_parts else "no actions"
            lines.append(
                f"Steps 1–{older[-1].step_number}: "
                f"[{summary_text}]"
            )
            entries = entries[len(entries) - window :]

        for entry in entries:
            lines.append(
                f"### Step {entry.step_number}: {entry.action.action.upper()}"
            )
            if entry.action.action == ActionType.DELEGATE:
                for target in entry.action.targets:
                    lines.append(f"  Delegated to {target.agent_name}: {target.task}")
                for result in entry.results:
                    if result.status == StepStatus.PAUSED:
                        status = "PAUSED (awaiting external response)"
                    elif result.success:
                        status = "SUCCESS"
                    else:
                        status = f"FAILED: {result.error_message}"
                    response_preview = result.response_text[:500]
                    if len(result.response_text) > 500:
                        response_preview += " [truncated]"
                    lines.append(
                        f"  → {result.agent_name} [{status}]: "
                        f"{response_preview}"
                    )
            elif entry.action.action == ActionType.CLARIFY:
                if entry.action.questions:
                    for qi, q in enumerate(entry.action.questions, 1):
                        lines.append(f"  Question {qi}: {q.prompt}")
                elif entry.action.clarification_question:
                    lines.append(
                        f"  Asked user: {entry.action.clarification_question}"
                    )
            elif entry.action.action == ActionType.SYNTHESIZE:
                lines.append(
                    f"  Instruction: {entry.action.synthesis_instruction}"
                )
            elif entry.action.action == ActionType.DONE:
                lines.append(f"  Reasoning: {entry.action.reasoning}")

        if trajectory.hitl_user_reply:
            lines.append(
                f"\n### User's Clarification Reply\n{trajectory.hitl_user_reply}"
            )
        elif trajectory.clarify_user_reply:
            lines.append(
                f"\n### User's Clarification Reply\n{trajectory.clarify_user_reply}"
            )

        return "\n".join(lines)

    def _parse_v2_action(self, response_json: dict) -> SupervisorAction:
        """Parse the LLM JSON response into a ``SupervisorAction``."""
        from models.supervisor_v2 import ClarifyQuestion, DelegateTarget

        raw_action = response_json.get("action", "done")
        action_str = str(raw_action).lower() if raw_action is not None else "done"
        try:
            action_type = ActionType(action_str)
        except ValueError:
            logger.warning(
                "Supervisor V2: unknown action '%s' (raw: '%s'), defaulting to DONE",
                action_str,
                raw_action,
            )
            action_type = ActionType.DONE

        targets = []
        raw_targets = response_json.get("targets") or []
        for t in raw_targets:
            if isinstance(t, dict) and "agent_id" in t:
                targets.append(
                    DelegateTarget(
                        agent_id=t["agent_id"],
                        agent_name=t.get("agent_name", "Unknown"),
                        task=t.get("task", ""),
                    )
                )
            else:
                logger.warning(
                    "Supervisor V2: dropping malformed target (missing agent_id): %s",
                    t,
                )

        if action_type == ActionType.DELEGATE and raw_targets and not targets:
            logger.warning(
                "Supervisor V2: all %d targets were malformed, converting DELEGATE to DONE",
                len(raw_targets),
            )
            action_type = ActionType.DONE

        # Sanitize CLARIFY fields — LLM may return unexpected types
        raw_prompt_type = response_json.get("prompt_type")
        prompt_type = (
            raw_prompt_type
            if isinstance(raw_prompt_type, str)
            and raw_prompt_type in ("text", "choice", "confirmation")
            else None
        )

        raw_choices = response_json.get("choices")
        choices = (
            raw_choices
            if isinstance(raw_choices, list)
            and all(isinstance(c, str) for c in raw_choices)
            else None
        )

        # Parse structured questions array (multi-question CLARIFY)
        parsed_questions: list[ClarifyQuestion] | None = None
        raw_questions = response_json.get("questions")
        if isinstance(raw_questions, list) and raw_questions:
            valid = []
            for q in raw_questions:
                if not isinstance(q, dict) or not isinstance(q.get("prompt"), str):
                    continue
                q_pt = q.get("prompt_type")
                q_choices = q.get("choices")
                valid.append(ClarifyQuestion(
                    prompt=q["prompt"],
                    prompt_type=(
                        q_pt if isinstance(q_pt, str)
                        and q_pt in ("text", "choice", "confirmation")
                        else None
                    ),
                    choices=(
                        q_choices if isinstance(q_choices, list)
                        and all(isinstance(c, str) for c in q_choices)
                        else None
                    ),
                ))
            if valid:
                parsed_questions = valid

        return SupervisorAction(
            action=action_type,
            reasoning=response_json.get("reasoning", ""),
            targets=targets,
            synthesis_instruction=response_json.get("synthesis_instruction"),
            clarification_question=response_json.get("clarification_question"),
            prompt_type=prompt_type,
            choices=choices,
            questions=parsed_questions,
        )

    @staticmethod
    def _fallback_v2_synthesis(trajectory: SupervisorTrajectory) -> str:
        """Simple fallback synthesis when the LLM call fails."""
        lines = ["Here's a summary of the agent responses:\n"]
        for entry in trajectory.entries:
            for result in entry.results:
                if result.status == StepStatus.PAUSED:
                    continue
                elif result.success:
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
