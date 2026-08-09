"""
Context utilities for room conversation memory management.

See docs/System-Architecture.md for design details.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from common.dto import LLMStructuredResponse
from common.utils.logger import get_logger

logger = get_logger(__name__)


class MemoryContentLike(Protocol):
    summary: str | None
    conversation_history: list[Any]


class RoomMemoryLike(Protocol):
    memory_content: MemoryContentLike | None
    conversation_history: list[Any]


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


# Configuration
MAX_HISTORY_TURNS = 20  # Keep last N turns in full detail
MAX_CONTEXT_CHARS = (
    500_000  # Safety net for char-level truncation; token budget is the real limiter
)
MAX_SUMMARY_CHARS = 4000  # Cap for MemoryContent.summary to prevent unbounded growth

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
            model="context_memory_json_model",
        )
        if isinstance(response, dict):
            return response
        return response.data

    legacy_json = getattr(provider, "call_supervisor_llm_json", None)
    if legacy_json is not None:
        return await legacy_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model="context_memory_json_model",
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


def get_context_stats(room_memory: RoomMemoryLike) -> dict:
    """Get monitoring statistics from canonical room-memory fields."""
    total_chars = 0
    total_tokens = 0
    full_turns = 0
    compact_turns = 0
    memory_content = room_memory.memory_content
    summary = memory_content.summary if memory_content else None

    if summary:
        total_chars += len(summary)

    for turn in room_memory.conversation_history:
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
        "history_turns": len(room_memory.conversation_history),
        "full_turns": full_turns,
        "compact_turns": compact_turns,
        "has_summary": bool(summary),
        "summary_length": len(summary) if summary else 0,
        "total_chars": total_chars,
        "total_tokens": total_tokens,
    }
