from __future__ import annotations

from typing import Any

from a2a.types import AgentCard


def validate_agent_card(card: dict[str, Any]) -> None:
    """Validate protocol card data against the SDK model.

    The 1.0 card is a protobuf message whose fields are set per-field, so this
    rejects malformed data only; normalizing a legacy card is the resolver's
    job, not this validator's.
    """
    if not card:
        raise ValueError("agent card data is empty")
    AgentCard(**card)


def is_valid_agent_card(card: dict[str, Any]) -> bool:
    try:
        validate_agent_card(card)
    except Exception:
        return False
    return True


__all__ = ["is_valid_agent_card", "validate_agent_card"]
