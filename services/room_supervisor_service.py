"""
Room Supervisor Service

Provides unified orchestration for multi-agent chat rooms through:
1. PLAN: Analyze user message + agent registry -> SupervisorPlan
2. REVIEW: After each agent step, evaluate result and optionally revise plan
3. SYNTHESIZE: After all steps complete, generate unified summary

This service replaces fragmented orchestration across:
- openai_service.parse_user_message_by_llm() (planning portion)
- DebateService.inject_short_debate_for_agent_message()
- RoomCoordinatorService.on_room_user_message_completed()

See docs/SUPERVISOR_PATTERN_DESIGN.md for full architecture details.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.utils.logger import get_logger
from models.supervisor import (
    AgentProfile,
    ReviewAction,
    RoomConfig,
    StepResult,
    SupervisorPlan,
    SupervisorReview,
    SupervisorStep,
    SupervisorStrategy,
)
from models.supervisor_v2 import (
    ActionType,
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
    """Raised when Supervisor fails to create a valid execution plan.

    Callers should catch this and fall back to the legacy parser.
    """

    pass


# =============================================================================
# System Prompts
# =============================================================================

SUPERVISOR_PLANNING_SYSTEM_PROMPT = """You are a Supervisor that routes user messages to specialist agents in a chat room.

## Available Agents
{agent_registry}

## Your Job
Analyze the user's message and create an execution plan. Output ONLY valid JSON matching the schema below.

## Decision Rules
1. DIRECT: If the message is clearly for one agent, route directly. Set strategy="direct".
2. PARALLEL: If multiple agents can work independently on different aspects, use strategy="parallel".
3. SEQUENTIAL: If Agent B needs Agent A's output, use strategy="sequential" with depends_on.
4. DEBATE: If the room is in debate mode, send the same task to multiple agents for contrasting perspectives. Set strategy="debate".
5. CLARIFY: If the message is ambiguous and you cannot determine which agent(s) to use, set strategy="clarify" with a clarification question.

## Context Passing Rules
- For sequential steps, set context_from_steps to include the step_ids whose results the agent needs.
- Write each step's task_description as a clear, specific instruction. Do NOT just forward the raw user message -- tailor it for the target agent.
- Include relevant conversation context in the task_description when it helps the agent.

## Output Schema
{{
  "strategy": "direct" | "parallel" | "sequential" | "debate" | "clarify",
  "reasoning": "Brief explanation of your routing decision",
  "steps": [
    {{
      "step_id": "step_1",
      "agent_id": "uuid",
      "agent_name": "Agent Name",
      "task_description": "What this agent should do",
      "depends_on": [],
      "context_from_steps": [],
      "priority": 0,
      "max_retries": 1
    }}
  ],
  "synthesis_instruction": "How to combine results (null for single-agent)" | null
}}"""

SUPERVISOR_REVIEW_SYSTEM_PROMPT = """You are reviewing the result of a step in a multi-agent execution plan.

## Completed Step
Agent: {agent_name}
Task: {task_description}
Result: {agent_result}

## Remaining Plan
{remaining_steps}

## Your Decision
Evaluate the result and decide the next action. Output ONLY valid JSON.

Rules:
- "continue": Result is acceptable. Proceed with the remaining plan as-is.
- "revise": Result changes what remaining steps should do. Provide revised_steps.
- "retry": Result is poor/empty. Retry this step with a refined prompt (max {retries_left} retries left).
- "skip": This step's result makes remaining steps unnecessary. Skip them.

For simple cases (single agent, result looks fine), always return "continue".
Only trigger "revise" or "retry" when clearly warranted.

{{
  "action": "continue" | "revise" | "retry" | "skip",
  "reasoning": "Brief explanation",
  "revised_steps": [...] | null,
  "retry_with_refinement": "refined prompt" | null
}}"""

SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT = """You are synthesizing the results from multiple specialist agents into a single coherent response for the user.

## Agent Results
{agent_results}

## Synthesis Instructions
{synthesis_instruction}

## Rules
- Attribute insights to their source agent when helpful: "According to [Agent Name]..."
- Resolve contradictions by noting both perspectives.
- If one agent failed, note what was successfully completed and what was not.
- Be concise. The user has already seen each agent's individual response.
- Focus on the unified answer, not a recap of each agent's full response.
"""

# ---------------------------------------------------------------------------
# V2 Prompts (adaptive loop)
# ---------------------------------------------------------------------------

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
    """
    Supervisor for multi-agent room orchestration.

    Responsibilities:
    1. PLAN: Analyze user message + agent registry -> SupervisorPlan
    2. REVIEW: After each agent step, evaluate result and optionally revise plan
    3. SYNTHESIZE: After all steps complete, generate unified summary

    This service is called by RoomServices (for planning) and
    RoomMessageCenter (for review and synthesis).
    """

    def __init__(
        self,
        openai_service: "OpenAIService | None" = None,
        database_service: "DatabaseService | None" = None,
    ):
        # Lazy import to avoid circular dependencies
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
    # Planning Phase
    # =========================================================================

    async def create_plan(
        self,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        conversation_context: str | None = None,
    ) -> SupervisorPlan:
        """Create an execution plan for a user message.

        Args:
            message_text: The user's message to process
            agent_registry: List of available agents with their profiles
            room_config: Room configuration (debate mode, agent set)
            conversation_context: Optional recent conversation history

        Returns:
            SupervisorPlan with strategy and steps

        Raises:
            SupervisorPlanningError: If LLM call fails or returns invalid response.
                Callers should catch this and fall back to the legacy parser.
        """
        try:
            # Build agent registry string for the prompt
            agent_registry_str = self._format_agent_registry(agent_registry)

            # Build the system prompt
            system_prompt = SUPERVISOR_PLANNING_SYSTEM_PROMPT.format(
                agent_registry=agent_registry_str
            )

            # Build the user prompt
            user_prompt = self._build_planning_user_prompt(
                message_text=message_text,
                room_config=room_config,
                conversation_context=conversation_context,
            )

            # Call the LLM
            response_json = await self._call_supervisor_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            # Parse and validate the response
            plan = self._parse_plan_response(response_json)

            logger.info(
                "Supervisor plan created",
                extra={
                    "strategy": plan.strategy,
                    "num_steps": len(plan.steps),
                    "agents": [s.agent_id for s in plan.steps],
                    "reasoning": plan.reasoning,
                },
            )

            return plan

        except SupervisorPlanningError:
            raise
        except Exception as e:
            logger.error(f"Supervisor planning failed: {e}")
            raise SupervisorPlanningError(f"Failed to create execution plan: {e}") from e

    def _format_agent_registry(self, agents: list[AgentProfile]) -> str:
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

    def _build_planning_user_prompt(
        self,
        message_text: str,
        room_config: RoomConfig,
        conversation_context: str | None,
    ) -> str:
        """Build the user prompt for planning."""
        parts = []

        if conversation_context:
            parts.append(f"## Recent Conversation\n{conversation_context}\n")

        if room_config.is_debate_mode:
            parts.append("## Room Mode\nDEBATE MODE is enabled. Route to multiple agents for contrasting perspectives.\n")

        parts.append(f"## User Message\n{message_text}")

        return "\n".join(parts)

    def _parse_plan_response(self, response_json: dict) -> SupervisorPlan:
        """Parse and validate the LLM response into a SupervisorPlan.

        Raises:
            SupervisorPlanningError: If the response is missing required fields.
        """
        try:
            # Validate required top-level field
            if "strategy" not in response_json:
                raise SupervisorPlanningError(
                    f"Supervisor LLM returned invalid plan: missing 'strategy' field. "
                    f"Response keys: {list(response_json.keys())}"
                )

            steps = []
            for i, step in enumerate(response_json.get("steps", [])):
                # Validate required step fields
                missing_fields = [
                    f for f in ("agent_id", "agent_name", "task_description")
                    if f not in step
                ]
                if missing_fields:
                    raise SupervisorPlanningError(
                        f"Supervisor LLM returned invalid plan: step {i+1} missing "
                        f"required fields {missing_fields}. Step data: {step}"
                    )

                steps.append(
                    SupervisorStep(
                        step_id=step.get("step_id", f"step_{i+1}"),
                        agent_id=step["agent_id"],
                        agent_name=step["agent_name"],
                        task_description=step["task_description"],
                        depends_on=step.get("depends_on", []),
                        context_from_steps=step.get("context_from_steps", []),
                        priority=step.get("priority", 0),
                        max_retries=step.get("max_retries", 1),
                    )
                )

            return SupervisorPlan(
                strategy=response_json["strategy"],
                reasoning=response_json.get("reasoning", ""),
                steps=steps,
                synthesis_instruction=response_json.get("synthesis_instruction"),
            )

        except SupervisorPlanningError:
            raise
        except Exception as e:
            raise SupervisorPlanningError(
                f"Supervisor LLM returned invalid plan: {e}. "
                f"Response: {response_json}"
            ) from e

    # =========================================================================
    # Review Phase
    # =========================================================================

    def _should_review_step(
        self,
        plan: SupervisorPlan,
        completed_step: SupervisorStep,
    ) -> bool:
        """Decide whether to invoke the Supervisor review after a step.

        The review step adds latency (~300-800ms per LLM call). It should be
        skipped when unnecessary to optimize performance.

        Returns True if review should be performed, False to skip.
        """
        total_steps = len(plan.steps)

        # Skip review for single-step plans (nothing to adjust)
        if total_steps <= 1:
            return False

        # Find the step index
        step_index = next(
            (i for i, s in enumerate(plan.steps) if s.step_id == completed_step.step_id),
            -1,
        )

        # Skip review for the last step (nothing remaining to adjust)
        if step_index >= total_steps - 1:
            return False

        # Check if downstream steps depend on this step's output
        has_dependencies = any(
            completed_step.step_id in s.context_from_steps
            for s in plan.steps[step_index + 1:]
        )

        # Always review if downstream steps depend on this step
        if has_dependencies:
            return True

        # For independent steps, skip review to reduce latency
        return False

    async def review_step(
        self,
        plan: SupervisorPlan,
        completed_step: SupervisorStep,
        agent_result: str,
        remaining_steps: list[SupervisorStep],
        retries_left: int = 0,
    ) -> SupervisorReview:
        """Review a completed step and decide next action.

        Args:
            plan: The original execution plan
            completed_step: The step that just completed
            agent_result: The agent's response text
            remaining_steps: Steps still to be executed
            retries_left: Number of retries remaining for this step

        Returns:
            SupervisorReview with action and optional revisions
        """
        # Format remaining steps for the prompt
        remaining_steps_str = self._format_remaining_steps(remaining_steps)

        # Build the system prompt
        system_prompt = SUPERVISOR_REVIEW_SYSTEM_PROMPT.format(
            agent_name=completed_step.agent_name,
            task_description=completed_step.task_description,
            agent_result=agent_result[:2000],  # Truncate long results
            remaining_steps=remaining_steps_str,
            retries_left=retries_left,
        )

        user_prompt = "Review the completed step and decide the next action."

        try:
            response_json = await self._call_supervisor_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            review = self._parse_review_response(response_json)

            logger.info(
                "Supervisor review completed",
                extra={
                    "step_id": completed_step.step_id,
                    "agent_id": completed_step.agent_id,
                    "action": review.action,
                    "reasoning": review.reasoning,
                },
            )

            return review

        except Exception as e:
            logger.warning(f"Supervisor review failed, defaulting to continue: {e}")
            return SupervisorReview(
                action=ReviewAction.CONTINUE,
                reasoning=f"Review failed ({e}), proceeding with plan",
            )

    def _format_remaining_steps(self, steps: list[SupervisorStep]) -> str:
        """Format remaining steps for the review prompt."""
        if not steps:
            return "No remaining steps."

        lines = []
        for step in steps:
            deps = f" (depends on: {', '.join(step.depends_on)})" if step.depends_on else ""
            lines.append(f"- {step.step_id}: {step.agent_name} - {step.task_description}{deps}")
        return "\n".join(lines)

    def _parse_review_response(self, response_json: dict) -> SupervisorReview:
        """Parse and validate the LLM response into a SupervisorReview."""
        revised_steps = None
        if response_json.get("revised_steps"):
            revised_steps = [
                SupervisorStep(
                    step_id=step.get("step_id", f"step_{i+1}"),
                    agent_id=step["agent_id"],
                    agent_name=step["agent_name"],
                    task_description=step["task_description"],
                    depends_on=step.get("depends_on", []),
                    context_from_steps=step.get("context_from_steps", []),
                    priority=step.get("priority", 0),
                    max_retries=step.get("max_retries", 1),
                )
                for i, step in enumerate(response_json["revised_steps"])
            ]

        return SupervisorReview(
            action=response_json["action"],
            reasoning=response_json.get("reasoning", ""),
            revised_steps=revised_steps,
            retry_with_refinement=response_json.get("retry_with_refinement"),
        )

    def should_review_step(
        self,
        plan: SupervisorPlan,
        step_index: int,
        total_steps: int,
        result_text: str,
    ) -> bool:
        """Decide whether to invoke the Supervisor review after a step.

        The review step adds latency (~300-800ms per LLM call). It should be
        skipped when unnecessary.

        Args:
            plan: The execution plan
            step_index: Index of the completed step (0-based)
            total_steps: Total number of steps in the plan
            result_text: The agent's response text

        Returns:
            True if review should be performed, False to skip
        """
        # Skip review for single-step plans (nothing to adjust)
        if total_steps <= 1:
            return False

        # Skip review for the last step (nothing remaining to adjust)
        if step_index >= total_steps - 1:
            return False

        # Skip review if result is clearly successful (non-empty, no error markers)
        if result_text and len(result_text) > 50 and "error" not in result_text.lower():
            # Heuristic: substantial result with no error = probably fine
            # Only review if the plan has complex dependencies
            has_dependencies = any(
                s.context_from_steps for s in plan.steps[step_index + 1 :]
            )
            if not has_dependencies:
                return False

        return True

    # =========================================================================
    # Synthesis Phase
    # =========================================================================

    async def synthesize_results(
        self,
        plan: SupervisorPlan,
        step_results: dict[str, StepResult],
        room_config: RoomConfig,
    ) -> str:
        """Synthesize multi-agent results into a unified response.

        Args:
            plan: The original execution plan
            step_results: Dict mapping step_id to StepResult
            room_config: Room configuration

        Returns:
            Synthesized response text
        """
        # Format agent results for the prompt
        agent_results_str = self._format_agent_results(step_results)

        # Get synthesis instruction from plan or use default
        synthesis_instruction = plan.synthesis_instruction or (
            "Combine the agent responses into a unified, coherent answer."
        )

        # Build the system prompt
        system_prompt = SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT.format(
            agent_results=agent_results_str,
            synthesis_instruction=synthesis_instruction,
        )

        user_prompt = "Synthesize the agent results into a unified response for the user."

        try:
            # For synthesis, we want text output, not JSON
            response = await self._call_supervisor_llm_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            logger.info(
                "Supervisor synthesis completed",
                extra={
                    "plan_id": plan.plan_id,
                    "num_results": len(step_results),
                    "synthesis_length": len(response),
                },
            )

            return response

        except Exception as e:
            logger.error(f"Supervisor synthesis failed: {e}")
            # Fall back to simple concatenation
            return self._fallback_synthesis(step_results)

    def _format_agent_results(self, step_results: dict[str, StepResult]) -> str:
        """Format step results for the synthesis prompt."""
        lines = []
        for step_id, result in step_results.items():
            status = "SUCCESS" if result.success else f"FAILED: {result.error_message}"
            lines.append(
                f"### {result.agent_name} ({step_id})\n"
                f"Task: {result.task_description}\n"
                f"Status: {status}\n"
                f"Response:\n{result.response_text}\n"
            )
        return "\n".join(lines)

    def _fallback_synthesis(self, step_results: dict[str, StepResult]) -> str:
        """Simple fallback synthesis when LLM fails."""
        lines = ["Here's a summary of the agent responses:\n"]
        for result in step_results.values():
            if result.success:
                lines.append(f"**{result.agent_name}**: {result.response_text[:500]}...")
            else:
                lines.append(f"**{result.agent_name}**: (Failed - {result.error_message})")
        return "\n\n".join(lines)

    # =========================================================================
    # Legacy Conversion
    # =========================================================================

    # TODO: Wire convert_parsed_result_to_plan into the fallback paths in
    # _parse_with_supervisor so that SupervisorPlanningError and empty-message
    # fallbacks also produce a SupervisorPlan. This enables Phase 3 review and
    # Phase 4 synthesis to work even when the Supervisor LLM fails.
    # Currently not called — both fallback paths use parse_user_message() directly.

    def convert_parsed_result_to_plan(self, parsed_result: dict) -> SupervisorPlan:
        """Convert a legacy parsed result to a SupervisorPlan.

        This method allows the system to use SupervisorPlan-based processing
        even when the legacy parser was used (e.g., as a fallback).

        Args:
            parsed_result: Output from openai_service.parse_user_message_by_llm()
                {
                    "message_type": str,
                    "original_text": str,
                    "needs_decomposition": bool,
                    "task_steps": [
                        {
                            "step_id": str,
                            "agent_id": str | None,
                            "agent_name": str | None,
                            "task_content": str,
                            "dependencies": [step_id, ...]
                        }
                    ]
                }

        Returns:
            SupervisorPlan equivalent to the parsed result
        """
        message_type = parsed_result.get("message_type", "")
        task_steps = parsed_result.get("task_steps", [])

        # Determine strategy based on message_type and task structure
        strategy = self._infer_strategy_from_parsed_result(parsed_result)

        # Convert task_steps to SupervisorSteps
        steps = []
        for i, step in enumerate(task_steps):
            steps.append(
                SupervisorStep(
                    step_id=step.get("step_id", f"step_{i+1}"),
                    agent_id=step.get("agent_id"),  # None if unresolved
                    agent_name=step.get("agent_name") or "Unknown",
                    task_description=step.get("task_content", ""),
                    depends_on=step.get("dependencies", []),
                    context_from_steps=[],  # Legacy parser doesn't track this
                    priority=0,
                    max_retries=1,
                )
            )

        # Determine synthesis instruction based on strategy
        synthesis_instruction = None
        if len(steps) > 1:
            if "DEBATE" in message_type:
                synthesis_instruction = (
                    "Compare and contrast the different agent perspectives. "
                    "Highlight areas of agreement and disagreement."
                )
            else:
                synthesis_instruction = (
                    "Combine the agent responses into a unified, coherent answer."
                )

        return SupervisorPlan(
            strategy=strategy,
            reasoning=f"Converted from legacy parser (message_type={message_type})",
            steps=steps,
            synthesis_instruction=synthesis_instruction,
        )

    def _infer_strategy_from_parsed_result(self, parsed_result: dict) -> SupervisorStrategy:
        """Infer the Supervisor strategy from a legacy parsed result."""
        message_type = parsed_result.get("message_type", "")
        task_steps = parsed_result.get("task_steps", [])

        # Check for debate mode
        if "DEBATE" in message_type:
            return SupervisorStrategy.DEBATE

        # Single step = direct
        if len(task_steps) <= 1:
            return SupervisorStrategy.DIRECT

        # Check for dependencies to determine sequential vs parallel
        has_dependencies = any(
            step.get("dependencies") for step in task_steps
        )

        if has_dependencies:
            return SupervisorStrategy.SEQUENTIAL
        else:
            return SupervisorStrategy.PARALLEL

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
            ``SupervisorPlanningError`` so the caller can fall back to V1.
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
