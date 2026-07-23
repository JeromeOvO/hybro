"""Shared sequential debate dispatch logic for Fast and Ultimate modes."""

from __future__ import annotations


class SequentialDebateDispatcher:
    """Shared sequential debate dispatch logic for Fast and Ultimate modes."""

    MAX_PRIOR_RESPONSE_CHARS: int = 3000

    @staticmethod
    def build_debate_prompt(
        original_task: str,
        prior_agent_name: str | None,
        prior_response: str | None,
        max_chars: int = 3000,
    ) -> str:
        """Build debate-enriched task prompt.

        First agent: returns original_task unchanged.
        Subsequent agents: injects last agent's response (truncated).
        """
        if not prior_response:
            return original_task

        # Fallback name for stale/deleted agents whose name lookup returns None
        if not prior_agent_name:
            prior_agent_name = "Previous Agent"

        truncated = prior_response[:max_chars]
        if len(prior_response) > max_chars:
            truncated += (
                f" ... [truncated — full response: {len(prior_response)} chars]"
            )

        return (
            f"YOUR TASK: {original_task}\n\n"
            f"=== RESPONSE FROM PREVIOUS AGENT ({prior_agent_name}) ===\n"
            f"{truncated}\n"
            f"=== END PREVIOUS RESPONSE ===\n\n"
            "DEBATE MODE INSTRUCTIONS:\n"
            "- Review the previous agent's response above\n"
            "- Provide your own perspective — you may agree, disagree, "
            "or build upon their points\n"
            "- Focus on adding value: new insights, alternative viewpoints, "
            "or deeper analysis\n"
            "- Execute your task and deliver concrete results, not just "
            "commentary on the previous response"
        )
