from __future__ import annotations

from a2a.types import AgentCard


def is_valid_agent_card(card: dict) -> bool:
    try:
        AgentCard(**card)
    except Exception:
        return False
    return True


__all__ = ["is_valid_agent_card"]
