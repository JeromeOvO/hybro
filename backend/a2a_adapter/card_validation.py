from __future__ import annotations

from a2a.types import AgentCard


def validate_agent_card(card: dict) -> None:
    AgentCard(**card)


def is_valid_agent_card(card: dict) -> bool:
    try:
        validate_agent_card(card)
    except Exception:
        return False
    return True


__all__ = ["is_valid_agent_card", "validate_agent_card"]
