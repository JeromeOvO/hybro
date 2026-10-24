from __future__ import annotations

from dataclasses import dataclass

from models.agent import Agent


@dataclass(frozen=True, slots=True)
class ResolvedRoutingScope:
    selected_agent_set: dict[str, str]
    auto_assign_agents: bool
    agents: list[Agent]


@dataclass
class ParseResult:
    """Result of the orchestration preflight `MessagePreflight.prepare_orchestration_envelope`.

    ``canceled`` lets the caller distinguish cancellation from failure.
    """

    success: bool
    canceled: bool = False
