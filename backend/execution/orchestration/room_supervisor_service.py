"""Room Supervisor Service.

Provides adaptive-loop orchestration for multi-agent chat rooms:
1. DECIDE_NEXT: Given the trajectory so far, decide the next action
2. SYNTHESIZE: Produce a unified response from collected agent results
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.prompts.markdown_response_format import HYBRO_MARKDOWN_RESPONSE_FORMAT
from common.utils.logger import get_logger
from llm_gateway.errors import LLMModelRoutingError, LLMServiceNotBoundError
from models.orchestration import PlannerAction, PlannerActionType
from models.supervisor import (
    ActionType,
    AgentProfile,
    ClarifyQuestion,
    DelegateTarget,
    RoomConfig,
    StepStatus,
    SupervisorAction,
    SupervisorTrajectory,
)

if TYPE_CHECKING:
    from llm_gateway.services import SupervisorLLMService

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
# Prompts (adaptive loop)
# =============================================================================

SUPERVISOR_SYSTEM_PROMPT = """You are a Supervisor coordinating specialist agents in a chat room.

## Available Agents
{agent_registry}

## Explicit Mentions
{explicit_mentions}

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
   - CLARIFY is a LAST RESORT. Always prefer DELEGATE first — agents can handle
     their own confirmations and input requests via their built-in flows.
   - Only use CLARIFY when you genuinely cannot determine WHICH agent to delegate
     to or WHAT task to give them (e.g., user message is unintelligible).
   - Do NOT use CLARIFY to ask the user about costs, payments, confirmations, or
     approvals. If an agent requires payment or confirmation, DELEGATE to the
     appropriate agent — the agent will ask the user directly if needed.
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
   - ONLY valid after at least one agent has been delegated to AND responded in this execution.

## Rules
- Prefer DELEGATE with a single target unless sub-tasks are truly independent.
  EXCEPTION: When the user explicitly addresses all agents (e.g., "everyone",
  "all of you", "each agent", "introduce yourselves"), delegate to ALL relevant
  agents — each with the same task addressed to them individually.
- Never ask an agent to impersonate, role-play as, or generate responses on behalf
  of other agents. Each agent can only speak for itself.
- You MUST DELEGATE at least once before choosing DONE or SYNTHESIZE. You cannot
  answer the user yourself — only agents produce visible responses. Even if the
  "Room Conversation Background" already contains relevant information from a prior
  exchange, the current user message is a NEW request that requires a fresh agent
  delegation. The conversation background is context only, not results for this task.
- After each agent result, evaluate quality per the QUALITY EVALUATION section
  below. If the agent returned a substantive response that fully addresses the
  user's question, choose DONE. Re-delegate if the response is empty, off-topic,
  says it couldn't find anything, or the agent explicitly failed.
  Do NOT re-delegate just to get a "better" or "more refined" answer when the
  existing response already contains actionable content.
- DELEGATE FIRST, CLARIFY LAST: When an agent's response indicates a next step
  (e.g., "payment required", "transfer needed", "authentication needed"), always
  DELEGATE to the appropriate agent with the specific details extracted from the
  prior response. Do NOT choose DONE (the user's goal is unmet) or CLARIFY (the
  agent can handle its own confirmations). Agents have built-in flows for user
  confirmation — trust them to ask the user directly when needed.
- If an agent's result changes what you planned to do next, simply adapt.
- Do NOT delegate to agents that are unhealthy (status: unhealthy).
- You have a maximum of {max_steps} actions. Use SYNTHESIZE or DONE before the limit.
- You may CLARIFY at most once. After you receive the user's answers, you MUST
  proceed with DELEGATE — do not issue another CLARIFY.
- When the user message includes quoted text (see Quoted text section), that quote is
  the primary subject. In DELEGATE tasks, include the quoted text verbatim — do not
  paraphrase, reformat, collapse line breaks, or wrap it in a new narrative frame.

## Quality Evaluation — before choosing SYNTHESIZE or DONE
- Review each DELEGATE result for substance. Does it directly address the
  user's request with actionable, specific content?
- A response that repeats the question, returns no data, says it couldn't
  find anything, or contains only generic/templated text should be treated
  as unsatisfactory.
- A response that asks the user to perform an action (e.g., make a payment,
  transfer funds, visit a URL) that another available agent could handle is
  NOT a final answer. DELEGATE to the appropriate agent with the specific
  details (amount, address, parameters) extracted from the prior response.
- If one or more agents returned unsatisfactory results while others
  succeeded, you may:
  (a) DELEGATE to the same agent with a more specific/refined task
  (b) DELEGATE to a different agent that might handle it better
  (c) SYNTHESIZE using only the good results, noting which areas had
      insufficient coverage
- Only choose SYNTHESIZE when you are confident the collected results
  adequately address the user's request.

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

SUPERVISOR_USER_PROMPT = """{debate_mode_note}

## User Message
{message_text}
{quoted_section}

## Execution So Far
{trajectory_summary}

## Budget
Actions completed so far: {steps_completed} of {max_steps} maximum.
Actions remaining: {steps_remaining}.
{budget_warning}

## What should happen next?"""

SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT = """You are synthesizing the results from multiple specialist agents into a single coherent response for the user.

## Execution Trajectory
{trajectory_summary}

## Synthesis Instructions
{synthesis_instruction}

## Rules
- You are HYBRO AI. Never adopt the identity, name, or persona of any agent. Never say "I'm [Agent Name]" or repeat an agent's self-introduction.
- Attribute insights to their source agent when helpful: "According to [Agent Name]..."
- Resolve contradictions by noting both perspectives.
- If one agent failed, note what was successfully completed and what was not.
- Be concise. The user has already seen each agent's individual response.
- Focus on the unified answer, not a recap of each agent's full response.

""" + HYBRO_MARKDOWN_RESPONSE_FORMAT + "\n"


class RoomSupervisorService:
    """Supervisor for multi-agent room orchestration (adaptive loop).

    Responsibilities:
    1. DECIDE_NEXT: Analyze trajectory + agent registry -> SupervisorAction
    2. SYNTHESIZE: Combine agent results into a unified response
    """

    def __init__(
        self,
        supervisor_service: SupervisorLLMService | None = None,
    ) -> None:
        self._supervisor_service = supervisor_service

    def bind_supervisor_service(
        self, supervisor_service: SupervisorLLMService
    ) -> None:
        self._supervisor_service = supervisor_service

    # =========================================================================
    # Agent registry formatting
    # =========================================================================

    @staticmethod
    def _format_agent_registry(agents: list[AgentProfile]) -> str:
        """Format agent profiles for the Supervisor prompt."""
        lines = []
        for agent in agents:
            capabilities = (
                ", ".join(agent.capabilities) if agent.capabilities else "General"
            )
            health = "healthy" if agent.is_healthy else "unhealthy"
            lines.append(
                f"- {agent.agent_name} (ID: {agent.agent_id})\n"
                f"  Description: {agent.description}\n"
                f"  Capabilities: {capabilities}\n"
                f"  Success Rate: {agent.success_rate:.0%}, Status: {health}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_explicit_mentions(explicit_mentions: list[dict]) -> str:
        if not explicit_mentions:
            return "No explicit agent mentions."
        lines = [
            "The user explicitly mentioned these agents:",
            *[
                f"- {mention.get('agent_name', 'Unknown')} "
                f"(ID: {mention.get('agent_id', 'unknown')}) via "
                f"{mention.get('mention_text', '<mention>')}"
                for mention in explicit_mentions
            ],
            "",
            "Treat explicit mentions as strong routing intent. Use the mentioned "
            "agents unless they are unavailable, unsafe, or clearly irrelevant. "
            "You may add other agents only if the task requires it. If you do "
            "not use a mentioned agent, explain why.",
        ]
        return "\n".join(lines)

    # =========================================================================
    # Adaptive loop — decide_next / synthesize
    # =========================================================================

    async def decide_next(
        self,
        message_text: str,
        agent_registry: list[AgentProfile],
        room_config: RoomConfig,
        trajectory: SupervisorTrajectory,
        conversation_context: str | None = None,
        quoted_text: str | None = None,
        max_steps: int = 8,
    ) -> SupervisorAction:
        """Ask the Supervisor LLM for the next action (adaptive loop).

        Called once per loop iteration by the ``SupervisorExecutor``.

        Returns:
            ``SupervisorAction`` — what to do next.
            On LLM failure with no prior entries, raises
            ``SupervisorPlanningError`` so the caller can surface the error.
            On LLM failure with prior entries, returns a DONE action (fail-open).
        """
        try:
            agent_registry_str = self._format_agent_registry(agent_registry)
            explicit_mentions = self._format_explicit_mentions(
                room_config.explicit_mentions
            )
            system_prompt = SUPERVISOR_SYSTEM_PROMPT.format(
                agent_registry=agent_registry_str,
                max_steps=max_steps,
                conversation_context=conversation_context or "No prior conversation.",
                explicit_mentions=explicit_mentions,
            )

            debate_note = ""
            if room_config.is_debate_mode:
                debate_note = (
                    "## Room Mode\n"
                    "DEBATE MODE is enabled. Agents are dispatched sequentially, each "
                    "building on prior responses. Do NOT synthesize — use DONE after "
                    "all agents have responded."
                )

            trajectory_summary = self._format_trajectory(trajectory)
            steps_completed = len(trajectory.entries)
            steps_remaining = max_steps - steps_completed
            if steps_remaining <= 1:
                budget_warning = (
                    "🛑 This is your LAST action. You MUST choose DONE or SYNTHESIZE."
                )
            elif steps_remaining <= 2:
                budget_warning = (
                    "⚠️ Budget almost exhausted. You MUST choose DONE or SYNTHESIZE "
                    "unless the user's question is genuinely unanswered."
                )
            else:
                budget_warning = ""

            quoted_section = ""
            if quoted_text and quoted_text.strip():
                quoted_section = (
                    "\n\n## Quoted text (user highlighted from a prior message)\n"
                    f'"{quoted_text.strip()}"\n'
                    "The user's message refers to this quoted content. When you DELEGATE, "
                    "include the quoted text verbatim in each agent's task — do not paraphrase "
                    "or flatten formatting."
                )

            user_prompt = SUPERVISOR_USER_PROMPT.format(
                debate_mode_note=debate_note,
                message_text=message_text,
                quoted_section=quoted_section,
                trajectory_summary=trajectory_summary,
                steps_completed=steps_completed,
                max_steps=max_steps,
                steps_remaining=steps_remaining,
                budget_warning=budget_warning,
            )

            response_json = await self._call_supervisor_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            action = self._parse_supervisor_action(response_json)

            # Hard guard: if the LLM keeps re-delegating to the same agent(s)
            # despite successful results, override to DONE.  Prompt hints are
            # not reliable enough with smaller models (gpt-4o-mini).
            # threshold >= 1: even one successful delegation is enough to block
            # an identical re-delegation on the next supervisor resume, which is
            # the root cause of duplicated agent responses.
            if action.action == ActionType.DELEGATE and len(trajectory.entries) >= 1:
                action = self._guard_consecutive_redelegation(
                    action, trajectory, max_consecutive=1,
                )

            logger.info(
                "Supervisor decide_next — action=%s targets=%s reasoning=%s",
                action.action.value
                if hasattr(action.action, "value")
                else action.action,
                [t.agent_name for t in action.targets],
                (action.reasoning or "")[:120],
            )

            return action

        except LLMServiceNotBoundError:
            raise
        except LLMModelRoutingError:
            raise
        except Exception as e:
            logger.warning("Supervisor decide_next failed: %s", e)
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

    def _synthesis_prompts(
        self,
        trajectory: SupervisorTrajectory,
        synthesis_instruction: str,
    ) -> tuple[str, str]:
        trajectory_summary = self._format_trajectory(trajectory)
        system_prompt = SUPERVISOR_SYNTHESIS_SYSTEM_PROMPT.format(
            trajectory_summary=trajectory_summary,
            synthesis_instruction=synthesis_instruction
            or "Combine the agent responses into a unified, coherent answer.",
        )
        user_prompt = (
            "Synthesize the agent results into a unified response for the user."
        )
        return system_prompt, user_prompt

    async def synthesize_stream(
        self,
        trajectory: SupervisorTrajectory,
        synthesis_instruction: str,
    ):
        """Stream synthesis tokens from the supervisor LLM (adaptive loop)."""
        system_prompt, user_prompt = self._synthesis_prompts(
            trajectory, synthesis_instruction
        )
        try:
            stream = self._supervisor_llm_text_stream(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            total = 0
            async for token in stream:
                total += len(token)
                yield token
            logger.info(
                "Supervisor synthesis stream completed",
                extra={
                    "trajectory_id": trajectory.trajectory_id,
                    "synthesis_length": total,
                },
            )
        except LLMServiceNotBoundError:
            raise
        except LLMModelRoutingError:
            raise
        except Exception as e:
            logger.error("Supervisor synthesis stream failed: %s", e)
            fallback = self._fallback_synthesis(trajectory)
            if fallback:
                yield fallback

    async def synthesize(
        self,
        trajectory: SupervisorTrajectory,
        synthesis_instruction: str,
    ) -> str:
        """Produce a synthesis from collected results (adaptive loop).

        Called when ``decide_next`` returns SYNTHESIZE, or when the step
        budget is exhausted and results need combining.
        """
        parts: list[str] = []
        async for token in self.synthesize_stream(trajectory, synthesis_instruction):
            parts.append(token)
        return "".join(parts)

    # -------------------------------------------------------------------------
    # Helpers
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
            lines.append(f"Steps 1–{older[-1].step_number}: [{summary_text}]")
            entries = entries[len(entries) - window :]

        for entry in entries:
            lines.append(f"### Step {entry.step_number}: {entry.action.action.upper()}")
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
                    total_len = len(result.response_text)
                    response_preview = result.response_text[:3000]
                    if total_len > 3000:
                        response_preview += (
                            f" ... [truncated — full response: {total_len} chars]"
                        )
                    lines.append(
                        f"  → {result.agent_name} [{status}]: {response_preview}"
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
                lines.append(f"  Instruction: {entry.action.synthesis_instruction}")
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

        # Warn about consecutive same-agent re-delegations
        all_entries = trajectory.entries
        if len(all_entries) >= 2:
            consecutive_agents: dict[str, int] = {}
            for entry in reversed(all_entries):
                if entry.action.action != ActionType.DELEGATE:
                    break
                entry_agent_ids = {r.agent_id for r in entry.results if r.success}
                if not consecutive_agents:
                    for aid in entry_agent_ids:
                        consecutive_agents[aid] = 1
                else:
                    for aid in entry_agent_ids:
                        if aid in consecutive_agents:
                            consecutive_agents[aid] += 1
                    if not any(aid in consecutive_agents for aid in entry_agent_ids):
                        break

            for agent_id, count in consecutive_agents.items():
                if count >= 2:
                    agent_name = next(
                        (
                            r.agent_name
                            for e in all_entries
                            for r in e.results
                            if r.agent_id == agent_id
                        ),
                        agent_id,
                    )
                    lines.append(
                        f"\n⚠️ {agent_name} has been delegated to {count} consecutive "
                        f"times with successful results. Unless its response was clearly "
                        f"wrong or incomplete, prefer DONE."
                    )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Hard guard against infinite same-agent re-delegation
    # ------------------------------------------------------------------

    @staticmethod
    def _count_consecutive_outcomes(
        target_ids: set[str],
        trajectory: SupervisorTrajectory,
        *,
        count_success: bool,
    ) -> dict[str, int]:
        """Walk trajectory backwards, counting consecutive matching outcomes."""
        counts: dict[str, int] = {aid: 0 for aid in target_ids}
        for entry in reversed(trajectory.entries):
            if entry.action.action != ActionType.DELEGATE:
                break
            matches = {
                r.agent_id for r in entry.results
                if r.success == count_success
            }
            matched = target_ids & matches
            if not matched:
                break
            for aid in matched:
                counts[aid] = counts.get(aid, 0) + 1
        return counts

    @staticmethod
    def _strip_offenders(
        action: SupervisorAction,
        offender_ids: set[str],
        label: str,
        threshold: int,
        reason_suffix: str,
    ) -> SupervisorAction:
        """Return a new action with offender targets removed, or DONE."""
        offender_names = [
            t.agent_name for t in action.targets if t.agent_id in offender_ids
        ]
        remaining = [
            t for t in action.targets if t.agent_id not in offender_ids
        ]
        if remaining:
            logger.warning(
                "%s guard: stripping %s (%d %s) — delegating to remaining %s only",
                label, offender_names, threshold, reason_suffix,
                [t.agent_name for t in remaining],
            )
            return SupervisorAction(
                action=ActionType.DELEGATE,
                reasoning=(
                    f"Auto-filtered: {', '.join(offender_names)} "
                    f"{reason_suffix} {threshold} times. "
                    f"Delegating to {', '.join(t.agent_name for t in remaining)} only."
                ),
                targets=remaining,
            )
        logger.warning(
            "%s guard: all targets %s hit %d %s — forcing DONE",
            label, offender_names, threshold, reason_suffix,
        )
        return SupervisorAction(
            action=ActionType.DONE,
            reasoning=(
                f"Auto-override: {', '.join(offender_names)} "
                f"{reason_suffix} {threshold} consecutive times. "
                f"No viable agents remaining."
            ),
        )

    def _guard_consecutive_redelegation(
        self,
        action: SupervisorAction,
        trajectory: SupervisorTrajectory,
        max_consecutive: int = 3,
        max_consecutive_failures: int = 2,
    ) -> SupervisorAction:
        """Filter out targets that have been delegated to repeatedly.

        Checks two conditions (failure guard runs first):

        1. **Consecutive failures** — if an agent has failed
           ``max_consecutive_failures`` times in a row, strip it to avoid
           wasting time on a broken/unreachable agent.
        2. **Consecutive successes** — if an agent has succeeded
           ``max_consecutive`` times in a row, strip it to prevent
           semantic loops.

        - If no targets are offenders, returns the original action unchanged.
        - If only some targets are offenders, returns DELEGATE with the
          remaining (non-offender) targets.
        - If *all* targets are offenders, returns DONE.
        """
        target_ids = {t.agent_id for t in action.targets}

        # --- Failure guard ---
        fail_counts = self._count_consecutive_outcomes(
            target_ids, trajectory, count_success=False,
        )
        fail_offenders = {
            aid for aid, c in fail_counts.items()
            if c >= max_consecutive_failures
        }
        if fail_offenders:
            action = self._strip_offenders(
                action, fail_offenders, "Failure",
                max_consecutive_failures, "consecutive failures",
            )
            if action.action == ActionType.DONE:
                return action
            target_ids = {t.agent_id for t in action.targets}

        # --- Success guard ---
        success_counts = self._count_consecutive_outcomes(
            target_ids, trajectory, count_success=True,
        )
        success_offenders = {
            aid for aid, c in success_counts.items() if c >= max_consecutive
        }
        if not success_offenders:
            return action
        return self._strip_offenders(
            action, success_offenders, "Success",
            max_consecutive, "consecutive successes",
        )

    def _parse_supervisor_action(self, response_json: dict) -> SupervisorAction:
        """Parse the LLM JSON response into a ``SupervisorAction``."""

        raw_action = response_json.get("action", "done")
        action_str = str(raw_action).lower() if raw_action is not None else "done"
        try:
            action_type = ActionType(action_str)
        except ValueError:
            logger.warning(
                "Supervisor: unknown action '%s' (raw: '%s'), defaulting to DONE",
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
                    "Supervisor: dropping malformed target (missing agent_id): %s",
                    t,
                )

        if action_type == ActionType.DELEGATE and raw_targets and not targets:
            logger.warning(
                "Supervisor: all %d targets were malformed, converting DELEGATE to DONE",
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
    def parse_planner_action(response_json: dict) -> PlannerAction:
        """Parse an existing supervisor JSON decision into a v2 planner action."""

        return RoomSupervisorService._parse_legacy_action_as_planner_action(
            response_json
        )

    @staticmethod
    def _parse_legacy_action_as_planner_action(
        response_json: dict,
    ) -> PlannerAction:
        """Adapt legacy supervisor JSON action names to a strict v2 action.

        Unlike ``_parse_supervisor_action``, this v2 path raises for unknown or
        malformed action values instead of coercing them to DONE.
        """

        if not isinstance(response_json, dict):
            raise ValueError("planner action response must be a JSON object")
        if "action" not in response_json:
            raise ValueError("planner action response missing action")

        raw_action = response_json["action"]
        if not isinstance(raw_action, str) or not raw_action.strip():
            raise ValueError("planner action must be a non-empty string")

        action_key = raw_action.strip().lower()
        legacy_action_mapping = {
            "clarify": PlannerActionType.ASK_USER,
            "done": PlannerActionType.COMPLETE,
            "delegate": PlannerActionType.DELEGATE,
            "synthesize": PlannerActionType.SYNTHESIZE,
        }
        try:
            planner_action_type = legacy_action_mapping.get(
                action_key
            ) or PlannerActionType(action_key)
        except ValueError as exc:
            raise ValueError(f"unknown planner action: {raw_action}") from exc

        raw_targets = response_json["targets"] if "targets" in response_json else []
        if not isinstance(raw_targets, list):
            raise ValueError("planner action targets must be a list")
        targets = []
        for target in raw_targets:
            if not isinstance(target, dict):
                raise ValueError("planner action target must be an object")
            agent_id = target.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("planner action target requires agent_id")
            task = target.get("task", "")
            if planner_action_type == PlannerActionType.DELEGATE and (
                not isinstance(task, str) or not task.strip()
            ):
                raise ValueError("delegate planner action target requires task")
            parsed_target = {
                "agent_id": agent_id,
                "agent_name": target.get("agent_name"),
                "task": task,
            }
            target_payload = dict(target)
            target_payload.setdefault("attachment_policy", "explicit_refs_only")
            for field_name in (
                "context_refs",
                "artifact_refs",
                "attachment_refs",
                "attachment_policy",
            ):
                if field_name in target_payload:
                    parsed_target[field_name] = target_payload[field_name]
            if "expected_outputs" in target_payload:
                parsed_target["expected_outputs"] = (
                    RoomSupervisorService._normalize_expected_outputs(
                        target_payload["expected_outputs"]
                    )
                )
            targets.append(parsed_target)

        raw_questions = response_json.get("questions")
        if raw_questions is None and isinstance(
            response_json.get("clarification_question"), str
        ):
            raw_questions = [
                {
                    "prompt": response_json["clarification_question"],
                    "prompt_type": response_json.get("prompt_type", "text"),
                    "choices": response_json.get("choices"),
                }
            ]
        elif raw_questions is None:
            raw_questions = []
        if not isinstance(raw_questions, list):
            raise ValueError("planner action questions must be a list")
        questions = []
        for question in raw_questions:
            if not isinstance(question, dict):
                raise ValueError("planner action question must be an object")
            questions.append(question)

        return PlannerAction(
            action=planner_action_type,
            reasoning=response_json.get("reasoning", ""),
            targets=targets,
            questions=questions,
            synthesis_instruction=response_json.get("synthesis_instruction"),
            failure_reason=response_json.get("failure_reason"),
        )

    @staticmethod
    def _normalize_expected_outputs(raw_expected_outputs):
        if not isinstance(raw_expected_outputs, list):
            return raw_expected_outputs

        normalized = []
        for output in raw_expected_outputs:
            if isinstance(output, str):
                description = output.strip()
                if not description:
                    continue
                normalized.append(
                    {
                        "kind": RoomSupervisorService._expected_output_kind(
                            description
                        ),
                        "required": True,
                        "description": description,
                    }
                )
                continue
            normalized.append(output)
        return normalized

    @staticmethod
    def _expected_output_kind(description: str) -> str:
        kind = description
        for separator in ("(", ":", "-", "—"):
            before_separator = description.split(separator, 1)[0].strip()
            if before_separator:
                kind = before_separator
                break
        kind = "_".join(kind.lower().split())
        return kind or description

    @staticmethod
    def _fallback_synthesis(trajectory: SupervisorTrajectory) -> str:
        """Simple fallback synthesis when the LLM call fails."""
        lines = ["Here's a summary of the agent responses:\n"]
        for entry in trajectory.entries:
            for result in entry.results:
                if result.status == StepStatus.PAUSED:
                    continue
                elif result.success:
                    lines.append(
                        f"**{result.agent_name}**: {result.response_text[:3000]}"
                    )
                else:
                    lines.append(
                        f"**{result.agent_name}**: (Failed - {result.error_message})"
                    )
        return "\n\n".join(lines)

    # =========================================================================
    # LLM Helpers
    # =========================================================================

    async def call_planner_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        """Call the supervisor JSON model through the public planner boundary."""

        return await self._call_supervisor_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    async def _call_supervisor_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        schema: dict | None = None,
    ) -> dict:
        """Call the Supervisor LLM and return JSON response.

        Routes through the focused supervisor LLM service. Model/provider routing
        is owned by llm_gateway.
        """
        if self._supervisor_service is None:
            raise LLMServiceNotBoundError("SupervisorLLMService is not bound")
        kwargs = {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }
        if schema is not None:
            kwargs["schema"] = schema
        return await self._supervisor_service.call_json(**kwargs)

    async def _call_supervisor_llm_text(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Call the Supervisor LLM and return text response (for synthesis).

        Routes through the focused supervisor LLM service.
        """
        parts: list[str] = []
        async for token in self._supervisor_llm_text_stream(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        ):
            parts.append(token)
        return "".join(parts)

    def _supervisor_llm_text_stream(
        self,
        system_prompt: str,
        user_prompt: str,
    ):
        """Return async iterator of supervisor LLM text."""
        if self._supervisor_service is None:
            raise LLMServiceNotBoundError("SupervisorLLMService is not bound")
        return self._supervisor_service.call_text_stream(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

# Singleton instance
room_supervisor_service = RoomSupervisorService()
