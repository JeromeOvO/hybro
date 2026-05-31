from __future__ import annotations

from common.utils.context_utils import MAX_CONTEXT_CHARS, estimate_tokens
from common.utils.logger import get_logger
from context_memory.config import TokenBudgetConfig
from context_memory.models import AssemblyResult, ConversationTurnData, TruncationReason
from context_memory.translators import assemble_context_dto, normalize_room_memory

logger = get_logger(__name__)


def build_stable_prefix(
    *,
    room_summary=None,
    agent_registry: list[dict] | None = None,
    room_facts: list[str] | None = None,
    include_room_facts: bool = True,
    memory_search_snippets: list[str] | None = None,
) -> str:
    parts: list[str] = []

    if room_summary and room_summary.has_content():
        parts.append("[Room Context]")
        if room_summary.current_goal:
            parts.append(f"Current Goal: {room_summary.current_goal}")
        if room_summary.key_decisions:
            parts.append(f"Key Decisions: {'; '.join(room_summary.key_decisions[:3])}")
        if room_summary.open_questions:
            parts.append(f"Open Questions: {'; '.join(room_summary.open_questions[:3])}")
        if room_summary.recent_agent_contributions:
            parts.append(
                "Recent Agent Work: "
                f"{'; '.join(room_summary.recent_agent_contributions[:3])}"
            )
        if room_summary.important_constraints:
            parts.append(
                f"Constraints: {'; '.join(room_summary.important_constraints[:3])}"
            )
        parts.append("")

    if agent_registry:
        parts.append("[Available Agents]")
        for agent in sorted(agent_registry, key=lambda item: item.get("agent_id", ""))[:10]:
            name = agent.get("agent_name") or agent.get("name", "Unknown")
            desc = (agent.get("description") or "")[:100]
            parts.append(f"- {name}: {desc}")
        parts.append("")

    if include_room_facts and room_facts:
        parts.append("[Room Facts]")
        for fact in room_facts[:5]:
            parts.append(f"- {fact}")
        parts.append("")

    if memory_search_snippets:
        parts.append("[Relevant Memory]")
        for snippet in memory_search_snippets[:5]:
            parts.append(f"- {snippet}")
        parts.append("")

    return "\n".join(parts)


def build_dynamic_suffix(
    *,
    turns: list[ConversationTurnData],
    current_task: str,
    include_summary: bool = False,
    summary: str | None = None,
) -> str:
    parts: list[str] = []
    if include_summary and summary:
        parts.append("[Earlier conversation summary]")
        parts.append(summary.strip())
        parts.append("")
    if turns:
        parts.append("[Recent conversation]")
        for turn in turns:
            parts.append(turn.to_context_string())
        parts.append("")
    parts.append("[Current request]")
    parts.append(f"User: {current_task}")
    return "\n".join(parts)


def build_agent_dynamic_suffix(
    *,
    turns: list[ConversationTurnData],
    summary: str | None,
    current_task: str,
    agent_name: str | None = None,
    room_awareness: str | None = None,
    quoted_text: str | None = None,
    agent_task: str | None = None,
    include_system_instruction: bool = True,
    task_budget: int | None = None,
) -> str:
    parts: list[str] = []
    if summary:
        parts.append("[Earlier conversation summary]")
        parts.append(summary.strip())
        parts.append("")
    if turns:
        parts.append("[Recent conversation]")
        for turn in turns:
            parts.append(turn.to_context_string())
        parts.append("")

    task_parts: list[str] = []
    if quoted_text:
        task_parts.append("[Quoted context]")
        qt = quoted_text.strip()
        if "\n---\n" in qt:
            task_parts.append(qt)
        else:
            task_parts.append("The user is referencing the following specific content:")
            task_parts.append(f'"{qt}"')
        task_parts.append("")
    if room_awareness:
        task_parts.append(room_awareness)
        task_parts.append("")
    task_parts.append("[Current request]")
    task_parts.append(f"User: {current_task}")
    if agent_task and agent_task.strip():
        task_parts.append("")
        task_parts.append("[Task]")
        task_parts.append(agent_task.strip())
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

    task_content = "\n".join(task_parts)
    if task_budget:
        task_tokens = estimate_tokens(task_content)
        if task_tokens > task_budget:
            max_task_chars = (task_budget * 4) - 500
            if max_task_chars > 100:
                task_parts_truncated: list[str] = []
                if quoted_text:
                    task_parts_truncated.append("[Quoted context]")
                    qt = quoted_text.strip()
                    if "\n---\n" in qt:
                        task_parts_truncated.append(qt)
                    else:
                        task_parts_truncated.append(
                            "The user is referencing the following specific content:"
                        )
                        task_parts_truncated.append(f'"{qt}"')
                    task_parts_truncated.append("")
                if room_awareness:
                    task_parts_truncated.append(room_awareness[:200] + "...")
                    task_parts_truncated.append("")
                task_parts_truncated.append("[Current request]")
                task_parts_truncated.append(f"User: {current_task[:max_task_chars]}... [truncated]")
                if agent_task and agent_task.strip():
                    task_parts_truncated.append("")
                    task_parts_truncated.append("[Task]")
                    remain = max(0, max_task_chars - len(current_task))
                    task_parts_truncated.append(
                        (agent_task.strip()[:remain] + "... [truncated]")
                        if remain < len(agent_task)
                        else agent_task.strip()
                    )
                if include_system_instruction and agent_name:
                    task_parts_truncated.append("")
                    task_parts_truncated.append(
                        f"You are {agent_name}. Execute the current request above."
                    )
                task_content = "\n".join(task_parts_truncated)

    parts.append(task_content)
    return "\n".join(parts)


def select_turns_within_budget(
    turns: list[ConversationTurnData],
    budget_tokens: int,
    summary: str | None = None,
) -> tuple[list[ConversationTurnData], int]:
    if not turns:
        return [], 0

    remaining_budget = budget_tokens - (estimate_tokens(summary) if summary else 0)
    if remaining_budget <= 0:
        return turns[-1:], len(turns) - 1

    costs: list[int] = []
    for turn in turns:
        if turn.representation == "full":
            cost = turn.estimated_tokens_full
            if cost == 0 and turn.content:
                cost = estimate_tokens(turn.content)
            costs.append(cost)
        else:
            costs.append(turn.estimated_tokens_compact)

    n = len(turns)
    suffix_sum = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_sum[i] = suffix_sum[i + 1] + costs[i]

    lo, hi = 0, n - 1
    best_start = n - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if suffix_sum[mid] <= remaining_budget:
            best_start = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return turns[best_start:], best_start


def assemble_supervisor_context_from_memory(
    room_memory_doc: dict,
    current_task: str,
    *,
    token_budget: TokenBudgetConfig | None = None,
    agent_registry: list[dict] | None = None,
    max_turns: int = 5,
    memory_search_results: list | None = None,
):
    budget = token_budget or TokenBudgetConfig()
    state = normalize_room_memory(room_memory_doc)
    search_snippets = _search_snippets(memory_search_results)
    stable_prefix = build_stable_prefix(
        room_summary=state.room_summary,
        agent_registry=agent_registry,
        include_room_facts=False,
        memory_search_snippets=search_snippets,
    )
    recent_turns = state.conversation_history[-max_turns:]
    dynamic_suffix = build_dynamic_suffix(
        turns=recent_turns,
        current_task=current_task,
        include_summary=False,
        summary=state.summary,
    )
    result, stable_prefix, dynamic_suffix = _finalize(
        room_id=state.room_id,
        stable_prefix=stable_prefix,
        dynamic_suffix=dynamic_suffix,
        budget=budget,
        turns=recent_turns,
        rebuild=lambda turns: build_dynamic_suffix(
            turns=turns,
            current_task=current_task,
            include_summary=False,
            summary=state.summary,
        ),
    )
    final_turns = _included_turns(recent_turns, result.turns_included)
    return assemble_context_dto(
        room_id=state.room_id,
        stable_prefix=stable_prefix,
        dynamic_suffix=dynamic_suffix,
        result=result,
        mode="supervisor",
        extra_metadata=_turn_representation_counts(final_turns),
    )


def assemble_agent_execution_context_from_memory(
    room_memory_doc: dict,
    current_task: str,
    *,
    token_budget: TokenBudgetConfig | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    room_awareness: str | None = None,
    quoted_text: str | None = None,
    agent_task: str | None = None,
    include_system_instruction: bool = True,
):
    budget = token_budget or TokenBudgetConfig()
    state = normalize_room_memory(room_memory_doc)
    facts = [
        fact.get("content")
        for fact in state.room_facts
        if isinstance(fact, dict) and fact.get("content")
    ]
    stable_prefix = build_stable_prefix(
        room_summary=state.room_summary,
        room_facts=facts[:5],
        include_room_facts=True,
    )
    stable_tokens = estimate_tokens(stable_prefix)
    if stable_tokens > budget.room_context_tokens:
        stable_prefix = build_stable_prefix(
            room_summary=state.room_summary,
            room_facts=[],
            include_room_facts=False,
        )

    selected_turns, turns_truncated = select_turns_within_budget(
        state.conversation_history,
        budget.conversation_history_tokens,
        summary=state.summary,
    )
    dynamic_suffix = build_agent_dynamic_suffix(
        turns=selected_turns,
        summary=state.summary,
        current_task=current_task,
        agent_name=agent_name,
        room_awareness=room_awareness,
        quoted_text=quoted_text,
        agent_task=agent_task,
        include_system_instruction=include_system_instruction,
        task_budget=budget.current_task_tokens,
    )
    result, stable_prefix, dynamic_suffix = _finalize(
        room_id=state.room_id,
        stable_prefix=stable_prefix,
        dynamic_suffix=dynamic_suffix,
        budget=budget,
        turns=selected_turns,
        turns_truncated=turns_truncated,
        rebuild=lambda turns: build_agent_dynamic_suffix(
            turns=turns,
            summary=state.summary,
            current_task=current_task,
            agent_name=agent_name,
            room_awareness=room_awareness,
            quoted_text=quoted_text,
            agent_task=agent_task,
            include_system_instruction=include_system_instruction,
            task_budget=budget.current_task_tokens,
        ),
    )
    final_turns = _included_turns(selected_turns, result.turns_included)
    return assemble_context_dto(
        room_id=state.room_id,
        stable_prefix=stable_prefix,
        dynamic_suffix=dynamic_suffix,
        result=result,
        mode="agent",
        extra_metadata={
            "agent_id": agent_id,
            **_turn_representation_counts(final_turns),
        },
    )


def _finalize(
    *,
    room_id: str,
    stable_prefix: str,
    dynamic_suffix: str,
    budget: TokenBudgetConfig,
    turns: list[ConversationTurnData],
    rebuild,
    turns_truncated: int = 0,
) -> tuple[AssemblyResult, str, str]:
    stable_tokens = estimate_tokens(stable_prefix)
    dynamic_tokens = estimate_tokens(dynamic_suffix)
    total_tokens = stable_tokens + dynamic_tokens
    was_truncated = turns_truncated > 0
    truncation_reason = (
        TruncationReason.TOKEN_BUDGET_EXCEEDED if was_truncated else None
    )
    available = budget.available_for_content
    selected_turns = list(turns)

    if total_tokens > available:
        was_truncated = True
        truncation_reason = TruncationReason.TOKEN_BUDGET_EXCEEDED
        while total_tokens > available and selected_turns:
            turns_truncated += 1
            selected_turns = selected_turns[1:]
            dynamic_suffix = rebuild(selected_turns)
            dynamic_tokens = estimate_tokens(dynamic_suffix)
            total_tokens = stable_tokens + dynamic_tokens
    context = f"{stable_prefix}\n\n{dynamic_suffix}" if stable_prefix else dynamic_suffix
    if total_tokens > available:
        was_truncated = True
        truncation_reason = TruncationReason.TOKEN_BUDGET_EXCEEDED
        context = _truncate_context_to_token_budget(context, available)
        stable_prefix, dynamic_suffix = _split_context_blocks(
            context,
            stable_prefix,
        )
        if selected_turns and not _dynamic_suffix_contains_turns(dynamic_suffix):
            turns_truncated += len(selected_turns)
            selected_turns = []
        stable_tokens = estimate_tokens(stable_prefix)
        dynamic_tokens = estimate_tokens(dynamic_suffix)
        total_tokens = estimate_tokens(context)
    if len(context) > MAX_CONTEXT_CHARS:
        was_truncated = True
        if truncation_reason is None:
            truncation_reason = TruncationReason.CHAR_LIMIT_EXCEEDED
        context = _truncate_context_to_char_limit(context, MAX_CONTEXT_CHARS)
        stable_prefix, dynamic_suffix = _split_context_blocks(
            context,
            stable_prefix,
        )
        if selected_turns and not _dynamic_suffix_contains_turns(dynamic_suffix):
            turns_truncated += len(selected_turns)
            selected_turns = []
        stable_tokens = estimate_tokens(stable_prefix)
        total_tokens = estimate_tokens(context)
        dynamic_tokens = estimate_tokens(dynamic_suffix)

    occupancy_window = max(1, budget.model_context_window)
    occupancy = (total_tokens / occupancy_window) * 100
    return (
        AssemblyResult(
            context=context,
            total_tokens=total_tokens,
            occupancy_pct=occupancy,
            was_truncated=was_truncated,
            truncation_reason=truncation_reason,
            turns_included=len(selected_turns),
            turns_truncated=turns_truncated,
            stable_prefix_tokens=stable_tokens,
            dynamic_suffix_tokens=dynamic_tokens,
        ),
        stable_prefix,
        dynamic_suffix,
    )


def _truncate_context_to_token_budget(context: str, available_tokens: int) -> str:
    current_request = _current_request_section(context)
    quoted_context = _quoted_context_section(context)
    if available_tokens <= 0:
        return current_request or ""

    marker = "\n... [context truncated]"
    if estimate_tokens(marker) > available_tokens:
        marker = "..."
    if estimate_tokens(marker) > available_tokens:
        return ""

    # Policy A: preserve [Quoted context] + [Current request] together when possible
    if quoted_context and current_request:
        combined = f"{quoted_context}\n\n{current_request}"
        candidate = f"{marker.lstrip()}\n{combined}"
        if estimate_tokens(candidate) <= available_tokens:
            return candidate
        # Quote + request too large — keep quote and truncate request
        quote_tokens = estimate_tokens(f"{marker.lstrip()}\n{quoted_context}\n\n")
        remaining = available_tokens - quote_tokens
        if remaining > 50:
            truncated_req = _truncate_current_request_section(
                current_request, remaining, marker,
            )
            if truncated_req is not None:
                return f"{marker.lstrip()}\n{quoted_context}\n\n{truncated_req}"

    if current_request:
        candidate = _truncate_current_request_section(
            current_request,
            available_tokens,
            marker,
        )
        if candidate is not None:
            return candidate

    lo = 0
    hi = len(context)
    best = marker.lstrip()
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = f"{marker.lstrip()}\n{context[len(context) - mid:].lstrip()}"
        if estimate_tokens(candidate) <= available_tokens:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _truncate_context_to_char_limit(context: str, max_chars: int) -> str:
    marker = "\n... [context truncated]"
    if max_chars <= len(marker):
        return marker[-max_chars:]
    current_request = _current_request_section(context)
    quoted_context = _quoted_context_section(context)
    # Policy A: preserve [Quoted context] + [Current request] together
    if quoted_context and current_request:
        prefix_marker = marker.lstrip()
        combined = f"{quoted_context}\n\n{current_request}"
        candidate = f"{prefix_marker}\n{combined}"
        if len(candidate) <= max_chars:
            return candidate
    if current_request:
        prefix_marker = marker.lstrip()
        candidate = f"{prefix_marker}\n{current_request}"
        if len(candidate) <= max_chars:
            return candidate
        suffix_marker = marker
        if max_chars <= len(suffix_marker):
            return suffix_marker[-max_chars:]
        return current_request[: max_chars - len(suffix_marker)].rstrip() + suffix_marker
    prefix_marker = marker.lstrip()
    body_chars = max_chars - len(prefix_marker) - 1
    if body_chars <= 0:
        return prefix_marker[-max_chars:]
    return f"{prefix_marker}\n{context[-body_chars:].lstrip()}"


def _current_request_section(context: str) -> str | None:
    marker = "[Current request]"
    index = context.rfind(marker)
    if index == -1:
        return None
    return context[index:].lstrip()


def _quoted_context_section(context: str) -> str | None:
    """Extract ``[Quoted context]`` block up to (but not including) the next section."""
    start_marker = "[Quoted context]"
    idx = context.rfind(start_marker)
    if idx == -1:
        return None
    remainder = context[idx:]
    for next_section in ("[Current request]", "[Task]"):
        end = remainder.find(next_section)
        if end > 0:
            return remainder[:end].rstrip()
    return remainder.rstrip()


def _truncate_current_request_section(
    current_request: str,
    available_tokens: int,
    marker: str,
) -> str | None:
    prefix_marker = marker.lstrip()
    candidate = f"{prefix_marker}\n{current_request}"
    if estimate_tokens(candidate) <= available_tokens:
        return candidate

    suffix_marker = marker
    if estimate_tokens(suffix_marker) > available_tokens:
        return None
    lo = 0
    hi = len(current_request)
    best = suffix_marker.strip()
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = current_request[:mid].rstrip() + suffix_marker
        if estimate_tokens(candidate) <= available_tokens:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _search_snippets(memory_search_results: list | None) -> list[str] | None:
    if not memory_search_results:
        return None
    snippets = []
    for result in memory_search_results[:5]:
        preview = getattr(result, "content_preview", None) or getattr(
            result, "content", ""
        )
        role = getattr(result, "role", None) or "unknown"
        agent = getattr(result, "agent_name", None)
        label = f"[{agent}]" if agent else f"[{role}]"
        if preview:
            snippets.append(f"{label} {preview}")
    return snippets


def _split_context_blocks(
    context: str,
    stable_prefix: str,
) -> tuple[str, str]:
    if not stable_prefix:
        return "", context
    if context.startswith(stable_prefix):
        remainder = context[len(stable_prefix) :]
        if remainder.startswith("\n\n"):
            return stable_prefix, remainder[2:]
        return stable_prefix, remainder.lstrip("\n")
    return "", context


def _dynamic_suffix_contains_turns(dynamic_suffix: str) -> bool:
    return "[Recent conversation]" in dynamic_suffix


def _included_turns(
    turns: list[ConversationTurnData],
    turns_included: int,
) -> list[ConversationTurnData]:
    if turns_included <= 0:
        return []
    return turns[-turns_included:]


def _turn_representation_counts(turns: list[ConversationTurnData]) -> dict[str, int]:
    full_turns = sum(1 for turn in turns if turn.representation == "full")
    compact_turns = sum(1 for turn in turns if turn.representation == "compact")
    return {"full_turns": full_turns, "compact_turns": compact_turns}
