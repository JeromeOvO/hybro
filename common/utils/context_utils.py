"""
Context utilities for room conversation memory management.
"""

import re
from typing import TYPE_CHECKING

from common.utils.logger import get_logger
from common.utils.time import utcnow

if TYPE_CHECKING:
    from models.memory import ConversationTurn, MemoryContent

logger = get_logger(__name__)

# Configuration
MAX_HISTORY_TURNS = 20  # Keep last N turns in full detail
MAX_CONTEXT_CHARS = 12000  # Approximate character limit before summarization kicks in
SUMMARY_PREVIEW_LENGTH = 150  # Characters to show per turn when summarizing


def clean_mention_format(text: str, room_agent_set: dict[str, str] | None = None) -> str:
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
    memory_content: "MemoryContent",
    role: str,
    content: str,
    agent_id: str | None = None,
    agent_name: str | None = None,
    user_id: str | None = None,
) -> "MemoryContent":
    """
    Add a conversation turn to history and manage window size.

    This implements a sliding window approach:
    - Keeps the most recent MAX_HISTORY_TURNS turns in full
    - Older turns are summarized and moved to the summary field

    Args:
        memory_content: The MemoryContent to update
        role: "user" or "agent"
        content: The message content (should be pre-cleaned)
        agent_id: Agent ID (for agent messages)
        agent_name: Agent name (for agent messages)
        user_id: User ID (for user messages)

    Returns:
        Updated MemoryContent
    """
    # Import here to avoid circular imports
    from models.memory import ConversationTurn

    turn = ConversationTurn(
        role=role,
        content=content,
        agent_id=agent_id,
        agent_name=agent_name,
        user_id=user_id,
        timestamp=utcnow(),
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

        logger.debug(
            f"Moved {excess_count} turns to summary, history now has "
            f"{len(memory_content.conversation_history)} turns"
        )

    return memory_content


def _format_turns_for_summary(turns: list["ConversationTurn"]) -> str:
    """Format conversation turns for summary storage."""
    parts = []
    for turn in turns:
        if turn.role == "user":
            preview = (
                turn.content[:SUMMARY_PREVIEW_LENGTH] + "..."
                if len(turn.content) > SUMMARY_PREVIEW_LENGTH
                else turn.content
            )
            parts.append(f"User: {preview}")
        else:
            speaker = turn.agent_name or "Agent"
            preview = (
                turn.content[:SUMMARY_PREVIEW_LENGTH] + "..."
                if len(turn.content) > SUMMARY_PREVIEW_LENGTH
                else turn.content
            )
            parts.append(f"{speaker}: {preview}")
    return "\n".join(parts)


def build_context_for_agent(
    memory_content: "MemoryContent",
    current_task: str,
    agent_name: str | None = None,
    include_system_instruction: bool = True,
) -> str:
    """
    Build context string for an agent request (ChatGPT/Claude style).

    This creates a clean conversation context that:
    1. Shows summarized older context if available
    2. Lists recent conversation turns clearly
    3. Presents the current task/request
    4. Optionally adds agent-specific instructions

    Args:
        memory_content: The room's MemoryContent with conversation history
        current_task: The current user request/task
        agent_name: Name of the agent receiving context (for personalization)
        include_system_instruction: Whether to add agent instructions at the end

    Returns:
        Formatted context string ready to send to agent
    """
    parts = []

    # 1. Include summary of older context if exists
    if memory_content.summary and memory_content.summary.strip():
        parts.append("[Earlier conversation summary]")
        parts.append(memory_content.summary.strip())
        parts.append("")  # Empty line for separation

    # 2. Include recent conversation history
    if memory_content.conversation_history:
        parts.append("[Recent conversation]")
        for turn in memory_content.conversation_history:
            if turn.role == "user":
                parts.append(f"User: {turn.content}")
            else:
                speaker = turn.agent_name or "Agent"
                parts.append(f"{speaker}: {turn.content}")
        parts.append("")  # Empty line before current task

    # 3. Current task/request
    parts.append("[Current request]")
    parts.append(f"User: {current_task}")

    # 4. Agent instruction (optional)
    if include_system_instruction and agent_name:
        parts.append("")
        parts.append(
            f"You are {agent_name}. Please respond to the current request above, "
            "using the conversation context if relevant."
        )

    return "\n".join(parts)


def build_minimal_context(
    memory_content: "MemoryContent",
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
            if turn.role == "user":
                parts.append(f"User: {turn.content}")
            else:
                speaker = turn.agent_name or "Agent"
                parts.append(f"{speaker}: {turn.content}")
        parts.append("")

    parts.append(f"User: {current_task}")
    return "\n".join(parts)


def get_context_stats(memory_content: "MemoryContent") -> dict:
    """
    Get statistics about the current context state.
    Useful for debugging and monitoring.

    Returns:
        Dict with context statistics
    """
    total_chars = 0
    if memory_content.summary:
        total_chars += len(memory_content.summary)
    for turn in memory_content.conversation_history:
        total_chars += len(turn.content)

    return {
        "history_turns": len(memory_content.conversation_history),
        "has_summary": bool(memory_content.summary),
        "summary_length": len(memory_content.summary) if memory_content.summary else 0,
        "total_chars": total_chars,
        "has_legacy_text": bool(memory_content.memory_text),
    }


def migrate_legacy_memory(memory_content: "MemoryContent") -> "MemoryContent":
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
