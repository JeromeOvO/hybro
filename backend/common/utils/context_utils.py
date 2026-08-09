"""
Context utilities for room conversation memory management.

See docs/System-Architecture.md for design details.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, TypeVar

from common.dto import LLMStructuredResponse
from common.utils.logger import get_logger
from common.utils.time import utcnow

logger = get_logger(__name__)


class MemoryContentLike(Protocol):
    summary: str | None
    conversation_history: list[Any]
    memory_text: str | None


TMemoryContent = TypeVar("TMemoryContent", bound=MemoryContentLike)


class TurnNotesLLMProvider(Protocol):
    async def generate_structured(
        self,
        messages: list[dict],
        *,
        schema: dict | None = None,
        json_mode: bool = False,
        model: str | None = None,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> LLMStructuredResponse | dict[str, Any]: ...


class ContextTurn(Protocol):
    def to_context_string(self) -> str: ...


class ContextTurnFactory(Protocol):
    def __call__(
        self,
        *,
        role: str,
        content: str,
        agent_id: str | None = None,
        agent_name: str | None = None,
        user_id: str | None = None,
        timestamp: Any,
        content_type: str,
        turn_type: str,
        estimated_tokens_full: int,
        turn_notes: dict | None,
        was_successful: bool | None = None,
    ) -> ContextTurn: ...


context_turn_factory: ContextTurnFactory | None = None


def bind_context_turn_factory(factory: ContextTurnFactory) -> None:
    global context_turn_factory

    context_turn_factory = factory


# Configuration
MAX_HISTORY_TURNS = 20  # Keep last N turns in full detail
MAX_CONTEXT_CHARS = (
    500_000  # Safety net for char-level truncation; token budget is the real limiter
)
MAX_SUMMARY_CHARS = 4000  # Cap for MemoryContent.summary to prevent unbounded growth
SUMMARY_PREVIEW_LENGTH = 150  # Characters to show per turn when summarizing

# Token estimation constants
CHARS_PER_TOKEN_ESTIMATE = 4  # Approximate chars per token for English text


def estimate_tokens(text: str | None, model: str = "gpt-4") -> int:
    """
    Estimate token count for text.

    Uses tiktoken for accuracy if available. Falls back to char/4 heuristic.

    See docs/System-Architecture.md for the current architecture.

    Args:
        text: The text to estimate tokens for
        model: The model to use for tokenization (default: gpt-4)

    Returns:
        Estimated token count
    """
    if not text:
        return 0

    try:
        import tiktoken

        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(text))
    except ImportError:
        logger.debug("tiktoken not available, using char/4 heuristic")
    except Exception as exc:
        logger.debug(
            "token_estimation_fallback_selected",
            extra={"error_type": type(exc).__name__},
        )

    # Fallback: ~4 chars per token for English
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


def extract_turn_notes(content: str | None) -> dict | None:
    """
    Extract structured notes from turn content (Zettelkasten / A-MEM pattern).

    This is a heuristic implementation that extracts:
    - keywords: Important words/phrases
    - entities: Named entities (people, places, things)
    - one_liner: Brief summary of the turn

    For short turns (<100 tokens), uses simple heuristics.
    For long turns, a fast LLM could be used (not implemented in Phase 1).

    See docs/System-Architecture.md for the current architecture.

    Args:
        content: The turn content to extract notes from

    Returns:
        Dict with keywords, entities, and one_liner, or None if content is empty
    """
    if not content or len(content.strip()) < 10:
        return None

    # Simple heuristic extraction for Phase 1
    words = content.split()

    # Extract potential keywords (longer words, excluding common stop words)
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "and",
        "but",
        "if",
        "or",
        "because",
        "until",
        "while",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "please",
        "thanks",
        "thank",
        "yes",
        "okay",
        "ok",
    }

    # Extract keywords (words > 4 chars, not stop words, alphanumeric)
    keywords = []
    seen = set()
    for word in words:
        clean_word = re.sub(r"[^\w]", "", word.lower())
        if (
            len(clean_word) > 4
            and clean_word not in stop_words
            and clean_word not in seen
            and clean_word.isalpha()
        ):
            keywords.append(clean_word)
            seen.add(clean_word)
            if len(keywords) >= 10:  # Limit to 10 keywords
                break

    # Extract potential entities (capitalized words that aren't at sentence start)
    entities = []
    entity_seen = set()
    for i, word in enumerate(words):
        # Skip first word of content and words after sentence-ending punctuation
        if i == 0:
            continue
        prev_word = words[i - 1] if i > 0 else ""
        if prev_word.endswith((".", "!", "?")):
            continue

        # Check if word is capitalized (potential entity)
        clean_word = re.sub(r"[^\w]", "", word)
        if (
            clean_word
            and clean_word[0].isupper()
            and clean_word.lower() not in stop_words
            and clean_word not in entity_seen
        ):
            entities.append(clean_word)
            entity_seen.add(clean_word)
            if len(entities) >= 5:  # Limit to 5 entities
                break

    # Generate one-liner (first sentence or truncated content)
    one_liner = content.strip()
    # Find first sentence
    for end_char in [".", "!", "?"]:
        idx = one_liner.find(end_char)
        if idx > 0 and idx < 150:
            one_liner = one_liner[: idx + 1]
            break
    else:
        # No sentence end found, truncate
        if len(one_liner) > 100:
            one_liner = one_liner[:100] + "..."

    return {
        "keywords": keywords,
        "entities": entities,
        "tags": [],  # Placeholder for future LLM-based tag extraction
        "one_liner": one_liner,
    }


LLM_TURN_NOTES_THRESHOLD = 100


async def extract_turn_notes_llm(
    content: str, *, provider: TurnNotesLLMProvider | None = None
) -> dict | None:
    """Extract structured turn notes using a fast LLM for long content.

    Callers should check content length before calling. This function always
    attempts the LLM path and falls back to the heuristic on failure.

    See docs/System-Architecture.md for the current architecture.
    """
    if not content or len(content.strip()) < 10:
        return None

    try:
        if provider is None:
            return extract_turn_notes(content)
        prompt = (
            "Extract structured notes from the following conversation turn. "
            "Return ONLY valid JSON with these keys:\n"
            '- "keywords": list of 5-10 important keywords\n'
            '- "entities": list of named entities (people, projects, tools)\n'
            '- "tags": list of topic tags (e.g. "debugging", "deployment")\n'
            '- "one_liner": a single sentence summary (max 100 chars)\n\n'
            f"Turn content:\n{content[:3000]}"
        )
        result = await _generate_turn_notes_json(
            provider,
            system_prompt="Extract structured notes. Respond with valid JSON only.",
            user_prompt=prompt,
        )
        if isinstance(result, dict):
            return {
                "keywords": result.get("keywords", [])[:10],
                "entities": result.get("entities", [])[:5],
                "tags": result.get("tags", [])[:5],
                "one_liner": (result.get("one_liner", "") or "")[:150],
            }
    except Exception as e:
        logger.debug("extract_turn_notes_llm failed, using heuristic: %s", e)

    return extract_turn_notes(content)


async def _generate_turn_notes_json(
    provider: TurnNotesLLMProvider,
    *,
    system_prompt: str,
    user_prompt: str,
) -> dict[str, Any] | None:
    generate_structured = getattr(provider, "generate_structured", None)
    if generate_structured is not None:
        response = await generate_structured(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            schema=None,
            json_mode=True,
            model="context_memory_legacy_json_model",
        )
        if isinstance(response, dict):
            return response
        return response.data

    legacy_json = getattr(provider, "call_supervisor_llm_json", None)
    if legacy_json is not None:
        return await legacy_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model="context_memory_legacy_json_model",
        )
    return None


def clean_mention_format(
    text: str, room_agent_set: dict[str, str] | None = None
) -> str:
    """
    Convert <@uuid|name> mentions to clean @AgentName format for storage.

    Args:
        text: Raw message text with mentions like "<@d7d6dbb1-...|N8n Agent>"
        room_agent_set: Dict of {agent_id: agent_name} from room

    Returns:
        Clean text like "@N8n Agent check my email"

    Example:
        Input:  "<@d7d6dbb1-e1ba-48f3-946b-48c6adb98f4d|N8n Personal AI Assistant Agent> can you check me email today?"
        Output: "@N8n Personal AI Assistant Agent can you check me email today?"
    """
    room_agent_set = room_agent_set or {}

    def replace_mention(match: re.Match) -> str:
        agent_id = match.group(1)
        agent_name = match.group(2)
        # Prefer name from room_agent_set if available for consistency
        display_name = room_agent_set.get(agent_id, agent_name)
        return f"@{display_name}"

    cleaned = re.sub(r"<@([^|]+)\|([^>]+)>", replace_mention, text)
    # Clean up any extra whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_mentioned_agent_ids(text: str) -> list[str]:
    """
    Extract agent IDs from mention format in text.

    Args:
        text: Message text with mentions like "<@uuid|name>"

    Returns:
        List of agent IDs found in mentions
    """
    pattern = r"<@([^|]+)\|[^>]+>"
    return re.findall(pattern, text)


def add_turn_to_history(
    memory_content: TMemoryContent,
    role: str | Any,
    content: str,
    agent_id: str | None = None,
    agent_name: str | None = None,
    user_id: str | None = None,
    content_type: str | Any = "text",
    turn_type: str | Any = "message",
    was_successful: bool | None = None,
) -> TMemoryContent:
    """
    Add a conversation turn to history and manage window size.

    This implements a sliding window approach:
    - Keeps the most recent MAX_HISTORY_TURNS turns in full
    - Older turns are summarized and moved to the summary field

    IMPORTANT: This function now populates estimated_tokens_full and turn_notes
    at turn creation time, as documented in docs/System-Architecture.md.

    Args:
        memory_content: The MemoryContent to update
        role: TurnRole enum or string ("user", "agent", "supervisor")
        content: The message content (should be pre-cleaned)
        agent_id: Agent ID (for agent/supervisor messages)
        agent_name: Agent name (for agent/supervisor messages)
        user_id: User ID (for user messages)
        content_type: ContentType enum or string ("text", "tool_result", "agent_response")
        turn_type: TurnType enum or string ("message", "hitl_question", "hitl_reply")
        was_successful: Success flag for learning from failures (§2.1 Principle 4)

    Returns:
        Updated MemoryContent
    """
    # Estimate tokens for the content (REQUIRED - never leave at 0)
    tokens_full = estimate_tokens(content)

    # Extract turn notes for richer retrieval (heuristic for now)
    notes = extract_turn_notes(content)

    turn_cls = _require_context_turn_factory()
    turn = turn_cls(
        role=_value(role),
        content=content,
        agent_id=agent_id,
        agent_name=agent_name,
        user_id=user_id,
        timestamp=utcnow(),
        content_type=_value(content_type),
        turn_type=_value(turn_type),
        estimated_tokens_full=tokens_full,
        turn_notes=notes,
        was_successful=was_successful,
    )

    memory_content.conversation_history.append(turn)

    # Check if we need to trim the window
    if len(memory_content.conversation_history) > MAX_HISTORY_TURNS:
        # Calculate how many turns to move to summary
        excess_count = len(memory_content.conversation_history) - MAX_HISTORY_TURNS
        excess_turns = memory_content.conversation_history[:excess_count]

        # Keep only the recent turns
        memory_content.conversation_history = memory_content.conversation_history[
            excess_count:
        ]

        # Add excess turns to summary
        summary_addition = _format_turns_for_summary(excess_turns)
        if memory_content.summary:
            memory_content.summary = f"{memory_content.summary}\n{summary_addition}"
        else:
            memory_content.summary = summary_addition

        # Cap summary to prevent unbounded growth; oldest content is trimmed
        # first because compacted turns remain searchable in MongoDB.
        if len(memory_content.summary) > MAX_SUMMARY_CHARS:
            memory_content.summary = (
                "..."
                + memory_content.summary[
                    len(memory_content.summary) - MAX_SUMMARY_CHARS + 3 :
                ]
            )

        logger.debug(
            f"Moved {excess_count} turns to summary, history now has "
            f"{len(memory_content.conversation_history)} turns"
        )

    return memory_content


def _require_context_turn_factory() -> ContextTurnFactory:
    if context_turn_factory is None:
        raise RuntimeError("Context turn factory has not been bound")
    return context_turn_factory


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _role_value(value: Any) -> str:
    return str(_value(value))


def _representation_value(value: Any) -> str:
    return str(_value(value))


def _render_turn_for_context(turn: Any) -> str:
    if hasattr(turn, "to_context_string"):
        return turn.to_context_string()
    if _role_value(getattr(turn, "role", "")) == "user":
        content = getattr(turn, "content", None) or "[content unavailable]"
        return f"User: {content}"
    speaker = getattr(turn, "agent_name", None) or "Agent"
    content = getattr(turn, "content", None) or "[content unavailable]"
    return f"{speaker}: {content}"


def _format_turns_for_summary(turns: list[Any]) -> str:
    """Format conversation turns for summary storage.

    Uses to_context_string() so that compact turns render their
    brief_summary + pointer instead of "[content unavailable]".
    """
    parts = []
    for turn in turns:
        rendered = _render_turn_for_context(turn)
        preview = (
            rendered[:SUMMARY_PREVIEW_LENGTH] + "..."
            if len(rendered) > SUMMARY_PREVIEW_LENGTH
            else rendered
        )
        parts.append(preview)
    return "\n".join(parts)


def build_context_for_agent(
    memory_content: MemoryContentLike,
    current_task: str,
    agent_name: str | None = None,
    include_system_instruction: bool = True,
    quoted_text: str | None = None,
    room_awareness: str | None = None,
    agent_task: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """
    DEPRECATED: Use context_memory.assembly for budget-aware assembly instead.
    This function is kept only as a fallback and will be removed in a future release.

    Build context string for an agent request (ChatGPT/Claude style).

    This creates a clean conversation context that:
    1. Shows summarized older context if available
    2. Lists recent conversation turns clearly (using to_context_string for compact support)
    3. Presents quoted context (if the user quoted a specific message)
    4. Presents the current task/request
    5. Optionally adds room awareness (other agents in the team)
    6. Optionally adds agent-specific instructions

    IMPORTANT: This function enforces MAX_CONTEXT_CHARS.

    For budget-aware context assembly with KV-cache optimization, use the
    core context-memory assembly implementation instead.

    Args:
        memory_content: The room's MemoryContent with conversation history
        current_task: The current user request/task
        agent_name: Name of the agent receiving context (for personalization)
        include_system_instruction: Whether to add agent instructions at the end
        quoted_text: Text the user highlighted and quoted from a previous message
        room_awareness: Optional room context describing other agents and this agent's role
        max_tokens: Optional token limit (defaults to MAX_CONTEXT_CHARS / 4)

    Returns:
        Formatted context string ready to send to agent
    """
    parts = []
    total_tokens = 0

    # Calculate effective token limit
    effective_max_tokens = max_tokens or (MAX_CONTEXT_CHARS // CHARS_PER_TOKEN_ESTIMATE)

    # 1. Include summary of older context if exists
    if memory_content.summary and memory_content.summary.strip():
        summary_tokens = estimate_tokens(memory_content.summary)
        if total_tokens + summary_tokens < effective_max_tokens:
            parts.append("[Earlier conversation summary]")
            parts.append(memory_content.summary.strip())
            parts.append("")  # Empty line for separation
            total_tokens += summary_tokens

    # 2. Include recent conversation history (with truncation if needed)
    if memory_content.conversation_history:
        history_parts = ["[Recent conversation]"]
        history_tokens = estimate_tokens("[Recent conversation]\n")

        # Process turns from oldest to newest, but we'll reverse selection
        turns_to_include = []
        for turn in reversed(memory_content.conversation_history):
            turn_str = _render_turn_for_context(turn)

            turn_tokens = estimate_tokens(turn_str)

            # Check if adding this turn would exceed history budget (60% per §5.2)
            # Use 0.6 to match conversation_history_pct in TokenBudget
            if total_tokens + history_tokens + turn_tokens < effective_max_tokens * 0.6:
                turns_to_include.insert(0, turn_str)
                history_tokens += turn_tokens
            else:
                # Budget exceeded, stop adding older turns
                break

        if turns_to_include:
            history_parts.extend(turns_to_include)
            history_parts.append("")  # Empty line before current task
            parts.extend(history_parts)
            total_tokens += history_tokens

    # 3. Quoted context (user highlighted specific text from a previous message)
    if quoted_text:
        qt = quoted_text.strip()
        if "\n---\n" in qt:
            quoted_section = f"[Quoted context]\n{qt}"
        else:
            quoted_section = (
                "[Quoted context]\n"
                "The user is referencing the following specific content:\n"
                f'"{qt}"'
            )
        quoted_tokens = estimate_tokens(quoted_section)
        if total_tokens + quoted_tokens < effective_max_tokens:
            parts.append("[Quoted context]")
            if "\n---\n" in qt:
                parts.append(qt)
            else:
                parts.append("The user is referencing the following specific content:")
                parts.append(f'"{qt}"')
            parts.append("")
            total_tokens += quoted_tokens

    # 4. Room awareness (other agents in the team and this agent's role)
    if room_awareness:
        awareness_tokens = estimate_tokens(room_awareness)
        if total_tokens + awareness_tokens < effective_max_tokens:
            parts.append(room_awareness)
            parts.append("")
            total_tokens += awareness_tokens

    # 5. Current task/request (always included)
    task_section = f"[Current request]\nUser: {current_task}"
    task_tokens = estimate_tokens(task_section)
    parts.append("[Current request]")
    parts.append(f"User: {current_task}")
    total_tokens += task_tokens

    if agent_task and agent_task.strip():
        ts = f"\n[Task]\n{agent_task.strip()}"
        tt = estimate_tokens(ts)
        if total_tokens + tt < effective_max_tokens:
            parts.append("")
            parts.append("[Task]")
            parts.append(agent_task.strip())
            total_tokens += tt

    # 6. Agent instruction (optional)
    if include_system_instruction and agent_name:
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
        instruction_tokens = estimate_tokens(instruction)
        if total_tokens + instruction_tokens < effective_max_tokens:
            parts.append("")
            parts.append(instruction)
            total_tokens += instruction_tokens

    # Log context occupancy (§15 requirement)
    occupancy_pct = (total_tokens / effective_max_tokens) * 100
    if occupancy_pct > 85:
        logger.warning(
            f"Context occupancy HIGH: {occupancy_pct:.1f}% "
            f"({total_tokens}/{effective_max_tokens} tokens)"
        )
    else:
        logger.debug(
            f"Context occupancy: {occupancy_pct:.1f}% "
            f"({total_tokens}/{effective_max_tokens} tokens)"
        )

    result = "\n".join(parts)

    # Hard cap: enforce MAX_CONTEXT_CHARS as safety net (§17.2)
    if len(result) > MAX_CONTEXT_CHARS:
        result = result[:MAX_CONTEXT_CHARS] + "\n... [context truncated]"
        logger.warning(
            f"Context char-limit truncation [legacy build_context_for_agent]: "
            f"exceeded MAX_CONTEXT_CHARS={MAX_CONTEXT_CHARS}"
        )

    return result


def build_minimal_context(
    memory_content: MemoryContentLike,
    current_task: str,
    max_turns: int = 5,
) -> str:
    """
    Build a minimal context with only the most recent turns.
    Useful when you want to reduce token usage.

    Args:
        memory_content: The room's MemoryContent
        current_task: The current user request
        max_turns: Maximum number of recent turns to include

    Returns:
        Minimal context string
    """
    parts = []

    # Only include recent turns
    recent_turns = memory_content.conversation_history[-max_turns:]
    if recent_turns:
        for turn in recent_turns:
            parts.append(_render_turn_for_context(turn))
        parts.append("")

    parts.append(f"User: {current_task}")
    return "\n".join(parts)


def get_context_stats(memory_content: MemoryContentLike) -> dict:
    """
    Get statistics about the current context state.
    Useful for debugging and monitoring.

    Returns:
        Dict with context statistics
    """
    total_chars = 0
    total_tokens = 0
    full_turns = 0
    compact_turns = 0

    if memory_content.summary:
        total_chars += len(memory_content.summary)

    for turn in memory_content.conversation_history:
        # Check representation if available (new ConversationTurn)
        if hasattr(turn, "representation"):
            if _representation_value(turn.representation) == "full":
                full_turns += 1
                if turn.content:
                    total_chars += len(turn.content)
            else:
                compact_turns += 1
        else:
            # Legacy turn
            full_turns += 1
            if turn.content:
                total_chars += len(turn.content)

        # Sum token estimates if available
        if hasattr(turn, "estimated_tokens_full"):
            total_tokens += turn.estimated_tokens_full

    return {
        "history_turns": len(memory_content.conversation_history),
        "full_turns": full_turns,
        "compact_turns": compact_turns,
        "has_summary": bool(memory_content.summary),
        "summary_length": len(memory_content.summary) if memory_content.summary else 0,
        "total_chars": total_chars,
        "total_tokens": total_tokens,
        "has_legacy_text": bool(memory_content.memory_text),
    }


def migrate_legacy_memory(memory_content: TMemoryContent) -> TMemoryContent:
    """
    Migrate old memory_text format to new conversation history structure.
    Call this when loading rooms with legacy data.

    Args:
        memory_content: MemoryContent that might have legacy memory_text

    Returns:
        Updated MemoryContent with legacy text moved to summary
    """
    if memory_content.memory_text and not memory_content.conversation_history:
        # Move old text to summary
        memory_content.summary = memory_content.memory_text
        memory_content.memory_text = None
        logger.info("Migrated legacy memory_text to summary field")
    return memory_content
