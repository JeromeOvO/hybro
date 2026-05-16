"""
Context Assembly Service for budget-aware context window construction.

This service implements the Context Assembly Engine described in
CONTEXT_MEMORY_SYSTEM_DESIGN.md §5. It provides:

1. Token budget allocation and enforcement
2. Stable prefix / dynamic suffix builders for KV-cache optimization
3. Context occupancy monitoring and truncation handling
4. Integration with existing memory services

The assembly pipeline:
    Load Memory Layers → Budget Allocation → Context Selection → Serialization

See CONTEXT_MEMORY_SYSTEM_DESIGN.md §5 for design details.
"""

from dataclasses import dataclass
from enum import Enum

from common.utils.context_utils import MAX_CONTEXT_CHARS, estimate_tokens
from common.utils.logger import get_logger
from models.context_config import TokenBudget, token_budget
from models.memory import (
    ConversationTurn,
    MemoryContent,
    RoomMemory,
    RoomSummary,
    TurnRepresentation,
)

logger = get_logger(__name__)


class TruncationReason(str, Enum):
    """Reason for context truncation."""

    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    TURN_COUNT_EXCEEDED = "turn_count_exceeded"
    CHAR_LIMIT_EXCEEDED = "char_limit_exceeded"


@dataclass
class ContextAssemblyResult:
    """Result of context assembly operation."""

    context: str
    total_tokens: int
    occupancy_pct: float
    was_truncated: bool
    truncation_reason: TruncationReason | None
    turns_included: int
    turns_truncated: int
    stable_prefix_tokens: int
    dynamic_suffix_tokens: int


@dataclass
class ContextMetrics:
    """Metrics for context assembly monitoring."""

    room_id: str
    total_tokens: int
    budget_tokens: int
    occupancy_pct: float
    was_truncated: bool
    truncation_reason: str | None
    turns_included: int
    turns_truncated: int
    full_turns: int
    compact_turns: int


class ContextAssemblyService:
    """
    Service for budget-aware context window construction.

    Implements the 4-stage assembly pipeline:
    1. Load Memory Layers (session, room, user, agent)
    2. Budget Allocation (fixed + dynamic percentages)
    3. Context Selection (recent turns, summaries, facts)
    4. Serialization (stable prefix + dynamic suffix)

    See CONTEXT_MEMORY_SYSTEM_DESIGN.md §5 for specification.
    """

    def __init__(self):
        self._budget: TokenBudget = token_budget
        self._facade = None
        self._bound = False

        # Truncation tracking for metrics
        self._truncation_count = 0

    def bind_facade(self, facade) -> None:
        self._facade = facade
        self._bound = True

    def _require_facade(self):
        if not self._bound or self._facade is None:
            raise RuntimeError(
                "ContextAssemblyService.bind_facade() not called - startup incomplete"
            )
        return self._facade

    @property
    def budget(self) -> TokenBudget:
        """Get the current token budget configuration."""
        return self._budget

    @property
    def truncation_count(self) -> int:
        """Get the total number of truncation events."""
        return self._truncation_count

    def build_supervisor_context(
        self,
        room_memory: RoomMemory,
        current_task: str,
        agent_registry: list[dict] | None = None,
        max_turns: int = 5,
        memory_search_results: list | None = None,
    ) -> ContextAssemblyResult:
        """
        Build context for the Supervisor LLM (decide_next calls).

        This context is FROZEN for the entire V2 loop duration.
        Agent results during the loop appear in trajectory_summary, not here.

        Uses minimal turns (default 5) to keep supervisor context lean.
        The stable prefix contains room summary and agent roster.

        Args:
            room_memory: The room's durable memory
            current_task: The current user request
            agent_registry: List of available agents with their capabilities
            max_turns: Maximum recent turns to include (default 5)
            memory_search_results: Optional pre-fetched MemorySearchResult list

        Returns:
            ContextAssemblyResult with assembled context and metrics
        """
        self._require_facade()
        if self._bound and self._facade is not None:
            from context_memory.translators import primitive

            assembled = self._facade.assemble_supervisor_context_from_memory(
                primitive(room_memory),
                current_task,
                agent_registry=agent_registry,
                max_turns=max_turns,
                memory_search_results=memory_search_results,
            )
            result = _legacy_context_result(assembled)
            self._record_bound_context_metrics(
                room_id=room_memory.room_id,
                result=result,
                context_type="supervisor",
                metadata=assembled.metadata,
            )
            return result

        memory_content = self._get_memory_content(room_memory)

        # Format memory search results into snippets
        search_snippets = None
        if memory_search_results:
            search_snippets = []
            for r in memory_search_results[:5]:
                preview = getattr(r, "content_preview", None) or getattr(r, "content", "")
                role = getattr(r, "role", None) or "unknown"
                agent = getattr(r, "agent_name", None)
                label = f"[{agent}]" if agent else f"[{role}]"
                if preview:
                    search_snippets.append(f"{label} {preview}")

        # Build stable prefix (room summary + agent roster + memory search)
        stable_prefix = self._build_stable_prefix(
            room_summary=room_memory.room_summary,
            agent_registry=agent_registry,
            include_room_facts=False,  # Supervisor doesn't need detailed facts
            memory_search_snippets=search_snippets,
        )

        # Build dynamic suffix (recent turns + current task)
        recent_turns = memory_content.conversation_history[-max_turns:]
        dynamic_suffix = self._build_dynamic_suffix(
            turns=recent_turns,
            current_task=current_task,
            include_summary=False,  # Supervisor uses room_summary instead
        )

        # Calculate tokens
        stable_tokens = estimate_tokens(stable_prefix)
        dynamic_tokens = estimate_tokens(dynamic_suffix)
        total_tokens = stable_tokens + dynamic_tokens

        # Check budget and truncate if needed
        available_tokens = self._budget.available_for_content
        was_truncated = False
        truncation_reason = None
        turns_truncated = 0

        if total_tokens > available_tokens:
            was_truncated = True
            truncation_reason = TruncationReason.TOKEN_BUDGET_EXCEEDED
            self._truncation_count += 1

            # Truncate by reducing turns
            while total_tokens > available_tokens and len(recent_turns) > 1:
                turns_truncated += 1
                recent_turns = recent_turns[1:]  # Remove oldest turn
                dynamic_suffix = self._build_dynamic_suffix(
                    turns=recent_turns,
                    current_task=current_task,
                    include_summary=False,
                )
                dynamic_tokens = estimate_tokens(dynamic_suffix)
                total_tokens = stable_tokens + dynamic_tokens

            # Edge case: if still over budget after removing all but 1 turn,
            # the stable prefix is too large. Log error but continue.
            if total_tokens > available_tokens:
                logger.error(
                    f"CRITICAL: Context still over budget after max truncation. "
                    f"stable_prefix={stable_tokens} tokens exceeds available={available_tokens}. "
                    f"Consider reducing room_summary or agent_registry size."
                )
            # Note: _log_context_metrics handles warning logging for truncation

        # Assemble final context
        context = f"{stable_prefix}\n\n{dynamic_suffix}" if stable_prefix else dynamic_suffix

        # Hard cap: enforce MAX_CONTEXT_CHARS as safety net (§17.2)
        if len(context) > MAX_CONTEXT_CHARS:
            if not was_truncated:
                was_truncated = True
                truncation_reason = TruncationReason.CHAR_LIMIT_EXCEEDED
                self._truncation_count += 1
            context = context[:MAX_CONTEXT_CHARS] + "\n... [context truncated]"
            total_tokens = estimate_tokens(context)
            logger.warning(
                f"Context char-limit truncation [supervisor] room={room_memory.room_id}: "
                f"exceeded MAX_CONTEXT_CHARS={MAX_CONTEXT_CHARS}"
            )

        occupancy_pct = (total_tokens / self._budget.model_context_window) * 100

        self._log_context_metrics(
            room_id=room_memory.room_id,
            total_tokens=total_tokens,
            occupancy_pct=occupancy_pct,
            was_truncated=was_truncated,
            truncation_reason=truncation_reason,
            turns_included=len(recent_turns),
            turns_truncated=turns_truncated,
            context_type="supervisor",
            stable_prefix_tokens=stable_tokens,
        )

        return ContextAssemblyResult(
            context=context,
            total_tokens=total_tokens,
            occupancy_pct=occupancy_pct,
            was_truncated=was_truncated,
            truncation_reason=truncation_reason,
            turns_included=len(recent_turns),
            turns_truncated=turns_truncated,
            stable_prefix_tokens=stable_tokens,
            dynamic_suffix_tokens=dynamic_tokens,
        )

    def build_agent_execution_context(
        self,
        room_memory: RoomMemory,
        current_task: str,
        agent_name: str | None = None,
        room_awareness: str | None = None,
        quoted_text: str | None = None,
        include_system_instruction: bool = True,
    ) -> ContextAssemblyResult:
        """
        Build context for an individual agent execution.

        Agents get more context than the supervisor:
        - Full conversation history (within budget)
        - Room facts relevant to the task
        - Peer agent awareness

        Args:
            room_memory: The room's durable memory
            current_task: The current task for this agent
            agent_name: Name of the agent (for personalization)
            room_awareness: Description of other agents in the room
            quoted_text: Text the user quoted from a previous message
            include_system_instruction: Whether to add agent instructions

        Returns:
            ContextAssemblyResult with assembled context and metrics
        """
        self._require_facade()
        if self._bound and self._facade is not None:
            from context_memory.translators import primitive

            assembled = self._facade.assemble_agent_execution_context_from_memory(
                primitive(room_memory),
                current_task,
                agent_name=agent_name,
                room_awareness=room_awareness,
                quoted_text=quoted_text,
                include_system_instruction=include_system_instruction,
            )
            result = _legacy_context_result(assembled)
            self._record_bound_context_metrics(
                room_id=room_memory.room_id,
                result=result,
                context_type="agent",
                metadata=assembled.metadata,
            )
            return result

        memory_content = self._get_memory_content(room_memory)

        # Calculate available budget for each component
        available_tokens = self._budget.available_for_content
        history_budget = self._budget.conversation_history_tokens
        task_budget = self._budget.current_task_tokens
        room_budget = self._budget.room_context_tokens

        # Build stable prefix (room facts + agent roster)
        # Defensive: handle case where room_facts might be None (though model has default)
        facts = room_memory.room_facts or []
        stable_prefix = self._build_stable_prefix(
            room_summary=room_memory.room_summary,
            room_facts=[f.content for f in facts[:5]],  # Top 5 facts
            include_room_facts=True,
        )
        stable_tokens = estimate_tokens(stable_prefix)

        # Enforce room context budget
        if stable_tokens > room_budget:
            # Truncate room facts
            stable_prefix = self._build_stable_prefix(
                room_summary=room_memory.room_summary,
                room_facts=[],
                include_room_facts=False,
            )
            stable_tokens = estimate_tokens(stable_prefix)
            logger.debug(f"Truncated room facts to fit budget: {stable_tokens} tokens")

        # Select turns within history budget
        selected_turns, turns_truncated = self._select_turns_within_budget(
            turns=memory_content.conversation_history,
            budget_tokens=history_budget,
            summary=memory_content.summary,
        )

        # Build dynamic suffix with task budget enforcement
        dynamic_suffix = self._build_agent_dynamic_suffix(
            turns=selected_turns,
            summary=memory_content.summary,
            current_task=current_task,
            agent_name=agent_name,
            room_awareness=room_awareness,
            quoted_text=quoted_text,
            include_system_instruction=include_system_instruction,
            task_budget=task_budget,
        )
        dynamic_tokens = estimate_tokens(dynamic_suffix)

        # Final budget check and hard cap enforcement (§17.2, §15.1)
        total_tokens = stable_tokens + dynamic_tokens
        was_truncated = turns_truncated > 0
        truncation_reason = TruncationReason.TOKEN_BUDGET_EXCEEDED if was_truncated else None

        # Hard cap: if total exceeds available, truncate more turns
        if total_tokens > available_tokens:
            if not was_truncated:
                was_truncated = True
                truncation_reason = TruncationReason.TOKEN_BUDGET_EXCEEDED
                self._truncation_count += 1

            # Try to reduce by removing more turns
            while total_tokens > available_tokens and len(selected_turns) > 1:
                turns_truncated += 1
                selected_turns = selected_turns[1:]  # Remove oldest turn

                # Rebuild dynamic suffix with fewer turns
                dynamic_suffix = self._build_agent_dynamic_suffix(
                    turns=selected_turns,
                    summary=memory_content.summary,  # Always include summary when truncating
                    current_task=current_task,
                    agent_name=agent_name,
                    room_awareness=room_awareness,
                    quoted_text=quoted_text,
                    include_system_instruction=include_system_instruction,
                    task_budget=task_budget,
                )
                dynamic_tokens = estimate_tokens(dynamic_suffix)
                total_tokens = stable_tokens + dynamic_tokens

            # Edge case: if still over budget after removing all but 1 turn
            if total_tokens > available_tokens:
                logger.error(
                    f"CRITICAL: Agent context still over budget after max truncation. "
                    f"total={total_tokens} tokens exceeds available={available_tokens}. "
                    f"stable_prefix={stable_tokens}, dynamic_suffix={dynamic_tokens}. "
                    f"Consider reducing room_summary size or task content."
                )

        # Assemble final context
        context = f"{stable_prefix}\n\n{dynamic_suffix}" if stable_prefix else dynamic_suffix

        # Hard cap: enforce MAX_CONTEXT_CHARS as safety net (§17.2)
        if len(context) > MAX_CONTEXT_CHARS:
            if not was_truncated:
                was_truncated = True
                truncation_reason = TruncationReason.CHAR_LIMIT_EXCEEDED
                self._truncation_count += 1
            context = context[:MAX_CONTEXT_CHARS] + "\n... [context truncated]"
            total_tokens = estimate_tokens(context)
            logger.warning(
                f"Context char-limit truncation [agent] room={room_memory.room_id}: "
                f"exceeded MAX_CONTEXT_CHARS={MAX_CONTEXT_CHARS}"
            )

        occupancy_pct = (total_tokens / self._budget.model_context_window) * 100

        # Count full vs compact turns
        full_turns = sum(
            1 for t in selected_turns
            if t.representation == TurnRepresentation.FULL
        )
        compact_turns = len(selected_turns) - full_turns

        self._log_context_metrics(
            room_id=room_memory.room_id,
            total_tokens=total_tokens,
            occupancy_pct=occupancy_pct,
            was_truncated=was_truncated,
            truncation_reason=truncation_reason,
            turns_included=len(selected_turns),
            turns_truncated=turns_truncated,
            context_type="agent",
            full_turns=full_turns,
            compact_turns=compact_turns,
            stable_prefix_tokens=stable_tokens,
        )

        return ContextAssemblyResult(
            context=context,
            total_tokens=total_tokens,
            occupancy_pct=occupancy_pct,
            was_truncated=was_truncated,
            truncation_reason=truncation_reason,
            turns_included=len(selected_turns),
            turns_truncated=turns_truncated,
            stable_prefix_tokens=stable_tokens,
            dynamic_suffix_tokens=dynamic_tokens,
        )

    def _get_memory_content(self, room_memory: RoomMemory) -> MemoryContent:
        """Get MemoryContent from RoomMemory, handling legacy structure."""
        if (
            room_memory.memory_content
            and room_memory.memory_content.conversation_history
        ):
            return room_memory.memory_content
        # Fallback: create from direct conversation_history
        content = MemoryContent()
        content.conversation_history = room_memory.get_conversation_history()
        if room_memory.memory_content and room_memory.memory_content.summary:
            content.summary = room_memory.memory_content.summary
        return content

    def _build_stable_prefix(
        self,
        room_summary: RoomSummary | None = None,
        agent_registry: list[dict] | None = None,
        room_facts: list[str] | None = None,
        include_room_facts: bool = True,
        memory_search_snippets: list[str] | None = None,
    ) -> str:
        """
        Build the stable prefix portion of context.

        This portion changes rarely and enables KV-cache optimization.
        Structure:
        - Room summary (current goal, key decisions, open questions)
        - Agent roster (if multi-agent)
        - Room facts (if enabled)
        - Memory search snippets (if provided)

        Args:
            room_summary: The room's rolling summary
            agent_registry: List of available agents
            room_facts: List of room fact strings
            include_room_facts: Whether to include room facts
            memory_search_snippets: Pre-formatted search result snippets

        Returns:
            Stable prefix string
        """
        parts = []

        # Room summary (Knowledge Block)
        if room_summary and self._has_room_summary_content(room_summary):
            parts.append("[Room Context]")
            if room_summary.current_goal:
                parts.append(f"Current Goal: {room_summary.current_goal}")
            if room_summary.key_decisions:
                decisions = "; ".join(room_summary.key_decisions[:3])
                parts.append(f"Key Decisions: {decisions}")
            if room_summary.open_questions:
                questions = "; ".join(room_summary.open_questions[:3])
                parts.append(f"Open Questions: {questions}")
            if room_summary.recent_agent_contributions:
                contributions = "; ".join(room_summary.recent_agent_contributions[:3])
                parts.append(f"Recent Agent Work: {contributions}")
            if room_summary.important_constraints:
                constraints = "; ".join(room_summary.important_constraints[:3])
                parts.append(f"Constraints: {constraints}")
            parts.append("")

        # Agent roster (sorted for KV-cache stability per §12.1)
        if agent_registry:
            parts.append("[Available Agents]")
            # Sort by agent_id for deterministic ordering (§12.1)
            sorted_agents = sorted(agent_registry, key=lambda a: a.get("agent_id", ""))
            for agent in sorted_agents[:10]:  # Limit to 10 agents
                name = agent.get("agent_name") or agent.get("name", "Unknown")
                desc = agent.get("description", "")[:100]
                parts.append(f"- {name}: {desc}")
            parts.append("")

        # Room facts
        if include_room_facts and room_facts:
            parts.append("[Room Facts]")
            for fact in room_facts[:5]:  # Limit to 5 facts
                parts.append(f"- {fact}")
            parts.append("")

        # Memory search results (§8 hybrid retrieval)
        if memory_search_snippets:
            parts.append("[Relevant Memory]")
            for snippet in memory_search_snippets[:5]:
                parts.append(f"- {snippet}")
            parts.append("")

        return "\n".join(parts)

    def _has_room_summary_content(self, room_summary: RoomSummary) -> bool:
        """Check if room summary has any meaningful content."""
        return bool(
            room_summary.current_goal
            or room_summary.key_decisions
            or room_summary.open_questions
            or room_summary.recent_agent_contributions
            or room_summary.important_constraints
        )

    def _build_dynamic_suffix(
        self,
        turns: list[ConversationTurn],
        current_task: str,
        include_summary: bool = False,
        summary: str | None = None,
    ) -> str:
        """
        Build the dynamic suffix portion of context.

        This portion changes with each request.
        Structure:
        - Earlier conversation summary (if truncated)
        - Recent conversation turns
        - Current task/request

        Args:
            turns: Recent conversation turns to include
            current_task: The current user request
            include_summary: Whether to include summary of older turns
            summary: Summary text of older turns

        Returns:
            Dynamic suffix string
        """
        parts = []

        # Summary of older context
        if include_summary and summary:
            parts.append("[Earlier conversation summary]")
            parts.append(summary.strip())
            parts.append("")

        # Recent conversation
        if turns:
            parts.append("[Recent conversation]")
            for turn in turns:
                parts.append(turn.to_context_string())
            parts.append("")

        # Current task
        parts.append("[Current request]")
        parts.append(f"User: {current_task}")

        return "\n".join(parts)

    def _build_agent_dynamic_suffix(
        self,
        turns: list[ConversationTurn],
        summary: str | None,
        current_task: str,
        agent_name: str | None = None,
        room_awareness: str | None = None,
        quoted_text: str | None = None,
        include_system_instruction: bool = True,
        task_budget: int | None = None,
    ) -> str:
        """
        Build dynamic suffix for agent execution context.

        Extended version with agent-specific elements.
        Enforces task_budget if provided (§5.2).

        Args:
            turns: Recent conversation turns
            summary: Summary of older context
            current_task: The current user request
            agent_name: Name of the agent
            room_awareness: Description of other agents
            quoted_text: Text the user quoted
            include_system_instruction: Whether to add agent instructions
            task_budget: Optional token budget for task-related content

        Returns:
            Dynamic suffix string
        """
        parts = []

        # Summary of older context
        if summary:
            parts.append("[Earlier conversation summary]")
            parts.append(summary.strip())
            parts.append("")

        # Recent conversation
        if turns:
            parts.append("[Recent conversation]")
            for turn in turns:
                parts.append(turn.to_context_string())
            parts.append("")

        # Track task-related tokens for budget enforcement
        task_parts = []

        # Quoted context (part of task budget)
        if quoted_text:
            task_parts.append("[Quoted context]")
            task_parts.append("The user is referencing the following specific content:")
            task_parts.append(f'"{quoted_text}"')
            task_parts.append("")

        # Room awareness (part of task budget)
        if room_awareness:
            task_parts.append(room_awareness)
            task_parts.append("")

        # Current task (always included)
        task_parts.append("[Current request]")
        task_parts.append(f"User: {current_task}")

        # Agent instruction
        if include_system_instruction and agent_name:
            task_parts.append("")
            instruction = (
                f"You are {agent_name}. Execute the current request above and provide concrete results. "
                "Do NOT just describe or plan what should be done - actually complete the task and deliver the output. "
                "Use the conversation context if relevant."
            )
            if quoted_text:
                instruction += (
                    " Pay special attention to the quoted context — "
                    "the user is asking about or responding to that specific content."
                )
            task_parts.append(instruction)

        # Enforce task budget if provided
        task_content = "\n".join(task_parts)
        if task_budget:
            task_tokens = estimate_tokens(task_content)
            if task_tokens > task_budget:
                # Truncate current_task if it's too long
                logger.warning(
                    f"Task content ({task_tokens} tokens) exceeds budget ({task_budget}). "
                    f"Truncating current_task."
                )
                # Rebuild with truncated task
                max_task_chars = (task_budget * 4) - 500  # Reserve for headers/instructions
                if max_task_chars > 100:
                    truncated_task = current_task[:max_task_chars] + "... [truncated]"
                    task_parts_truncated = []
                    if quoted_text:
                        task_parts_truncated.append("[Quoted context]")
                        task_parts_truncated.append("The user is referencing the following specific content:")
                        task_parts_truncated.append(f'"{quoted_text[:500]}..."')  # Also truncate quote
                        task_parts_truncated.append("")
                    if room_awareness:
                        task_parts_truncated.append(room_awareness[:200] + "...")
                        task_parts_truncated.append("")
                    task_parts_truncated.append("[Current request]")
                    task_parts_truncated.append(f"User: {truncated_task}")
                    if include_system_instruction and agent_name:
                        task_parts_truncated.append("")
                        task_parts_truncated.append(
                            f"You are {agent_name}. Execute the current request above."
                        )
                    task_content = "\n".join(task_parts_truncated)

        parts.append(task_content)

        return "\n".join(parts)

    def _select_turns_within_budget(
        self,
        turns: list[ConversationTurn],
        budget_tokens: int,
        summary: str | None = None,
    ) -> tuple[list[ConversationTurn], int]:
        """
        Select turns that fit within the token budget.

        Uses a suffix-sum approach with binary search to find the optimal
        cutoff in O(n) instead of the O(n^2) peel-from-front loop.

        Strategy:
        1. Build per-turn token costs from newest to oldest
        2. Compute cumulative sum from the tail
        3. Binary search for the first index whose cumulative total fits budget

        Args:
            turns: All conversation turns
            budget_tokens: Maximum tokens for conversation history
            summary: Existing summary (adds to token count)

        Returns:
            Tuple of (selected_turns, turns_truncated)
        """
        if not turns:
            return [], 0

        summary_tokens = estimate_tokens(summary) if summary else 0
        remaining_budget = budget_tokens - summary_tokens
        if remaining_budget <= 0:
            return turns[-1:], len(turns) - 1

        n = len(turns)
        costs = []
        for turn in turns:
            if turn.representation == TurnRepresentation.FULL:
                tok = turn.estimated_tokens_full
                if tok == 0 and turn.content:
                    tok = estimate_tokens(turn.content)
                costs.append(tok)
            else:
                costs.append(turn.estimated_tokens_compact)

        # Suffix sums: suffix_sum[i] = sum of costs[i..n-1]
        suffix_sum = [0] * (n + 1)
        for i in range(n - 1, -1, -1):
            suffix_sum[i] = suffix_sum[i + 1] + costs[i]

        # Binary search for the earliest index where suffix_sum[idx] <= remaining_budget
        lo, hi = 0, n - 1
        best_start = n - 1  # At minimum, include the last turn
        while lo <= hi:
            mid = (lo + hi) // 2
            if suffix_sum[mid] <= remaining_budget:
                best_start = mid
                hi = mid - 1
            else:
                lo = mid + 1

        selected = turns[best_start:]
        turns_truncated = best_start

        if turns_truncated > 0:
            logger.debug(
                f"Truncated {turns_truncated} turns to fit budget: "
                f"{suffix_sum[best_start]} tokens (budget: {budget_tokens})"
            )

        return selected, turns_truncated

    def _log_context_metrics(
        self,
        room_id: str,
        total_tokens: int,
        occupancy_pct: float,
        was_truncated: bool,
        truncation_reason: TruncationReason | None,
        turns_included: int,
        turns_truncated: int,
        context_type: str,
        full_turns: int = 0,
        compact_turns: int = 0,
        stable_prefix_tokens: int = 0,
    ) -> None:
        """
        Log context assembly metrics for monitoring.

        Occupancy thresholds (§15.1):
        - < 70%: Healthy
        - 70-85%: Soft warning (approaching limit)
        - 85-90%: Hard cap zone (truncate + warning)
        - > 90%: Emergency (error + alert)

        See CONTEXT_MEMORY_SYSTEM_DESIGN.md §15 for metrics specification.
        """
        metrics = ContextMetrics(
            room_id=room_id,
            total_tokens=total_tokens,
            budget_tokens=self._budget.available_for_content,
            occupancy_pct=occupancy_pct,
            was_truncated=was_truncated,
            truncation_reason=truncation_reason.value if truncation_reason else None,
            turns_included=turns_included,
            turns_truncated=turns_truncated,
            full_turns=full_turns,
            compact_turns=compact_turns,
        )

        # Log at appropriate level based on occupancy thresholds (§15.1)
        # Include cache_prefix_tokens for KV-cache analysis (§15.2)
        if occupancy_pct > 90:
            # EMERGENCY: > 90% occupancy
            logger.error(
                f"EMERGENCY context occupancy [{context_type}] room={room_id}: "
                f"occupancy={occupancy_pct:.1f}% EXCEEDS 90%! "
                f"tokens={total_tokens}/{self._budget.available_for_content}, "
                f"cache_prefix_tokens={stable_prefix_tokens}, "
                f"truncated={turns_truncated} turns. Investigate verbose agent."
            )
        elif was_truncated or occupancy_pct > 85:
            # Hard cap zone: 85-90% or truncation occurred
            logger.warning(
                f"Context TRUNCATED [{context_type}] room={room_id}: "
                f"occupancy={occupancy_pct:.1f}% (hard cap zone), "
                f"truncated={turns_truncated} turns, "
                f"tokens={total_tokens}/{self._budget.available_for_content}, "
                f"cache_prefix_tokens={stable_prefix_tokens}"
            )
        elif occupancy_pct > 70:
            # Soft warning: 70-85%
            logger.info(
                f"Context approaching limit [{context_type}] room={room_id}: "
                f"occupancy={occupancy_pct:.1f}% (>70%), "
                f"tokens={total_tokens}, cache_prefix_tokens={stable_prefix_tokens}, "
                f"turns={turns_included} (full={full_turns}, compact={compact_turns})"
            )
        else:
            # Healthy: < 70%
            logger.debug(
                f"Context assembled [{context_type}] room={room_id}: "
                f"occupancy={occupancy_pct:.1f}%, "
                f"tokens={total_tokens}, cache_prefix_tokens={stable_prefix_tokens}, "
                f"turns={turns_included} (full={full_turns}, compact={compact_turns})"
            )

    def _record_bound_context_metrics(
        self,
        *,
        room_id: str,
        result: ContextAssemblyResult,
        context_type: str,
        metadata: dict | None = None,
    ) -> None:
        if result.was_truncated:
            self._truncation_count += 1
        metadata = metadata or {}
        self._log_context_metrics(
            room_id=room_id,
            total_tokens=result.total_tokens,
            occupancy_pct=result.occupancy_pct,
            was_truncated=result.was_truncated,
            truncation_reason=result.truncation_reason,
            turns_included=result.turns_included,
            turns_truncated=result.turns_truncated,
            context_type=context_type,
            full_turns=metadata.get("full_turns", 0),
            compact_turns=metadata.get("compact_turns", 0),
            stable_prefix_tokens=result.stable_prefix_tokens,
        )

    def get_budget_summary(self) -> dict:
        """Get a summary of the current token budget configuration."""
        return self._budget.get_budget_summary()


# Singleton export
context_assembly_service = ContextAssemblyService()


def _legacy_context_result(assembled) -> ContextAssemblyResult:
    metadata = assembled.metadata
    reason = metadata.get("truncation_reason")
    return ContextAssemblyResult(
        context=metadata.get("context", ""),
        total_tokens=assembled.total_tokens,
        occupancy_pct=metadata.get("occupancy_pct", 0.0),
        was_truncated=metadata.get("was_truncated", False),
        truncation_reason=TruncationReason(reason) if reason else None,
        turns_included=metadata.get("turns_included", 0),
        turns_truncated=metadata.get("turns_truncated", 0),
        stable_prefix_tokens=metadata.get("stable_prefix_tokens", 0),
        dynamic_suffix_tokens=metadata.get("dynamic_suffix_tokens", 0),
    )
