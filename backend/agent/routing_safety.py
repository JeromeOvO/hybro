from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from common.utils.a2a_file_modes import agent_accepts_required_input_modes
from models.agent import AgentStatus


def is_routing_agent_eligible(
    agent,
    *,
    user_id: str | None,
    excluded_agent_ids: set[str] | frozenset[str] = frozenset(),
    required_input_modes: list[str] | None = None,
) -> bool:
    """Return whether an agent is safe to expose to routing or selection."""
    if agent is None:
        return False
    agent_id = str(getattr(agent, "agent_id", "") or "")
    if not agent_id or agent_id in excluded_agent_ids:
        return False
    status = getattr(agent, "agent_status", None)
    status_value = getattr(status, "value", status)
    if status_value != AgentStatus.active.value:
        return False
    # Legacy records without ``is_public`` are treated as public by existing
    # visibility queries; preserve the same rule at this in-process boundary.
    if (
        getattr(agent, "is_public", True) is False
        and getattr(agent, "provider_id", None) != user_id
    ):
        return False
    return agent_accepts_required_input_modes(
        agent.agent_card,
        required_input_modes,
    )


async def sanitize_routing_agent_ids(
    agent_ids: Iterable[str],
    *,
    lookup: Callable[[str], Awaitable[object | None]],
    user_id: str | None,
    excluded_agent_ids: set[str] | frozenset[str] = frozenset(),
    required_input_modes: list[str] | None = None,
) -> tuple[list[object], list[str]]:
    """Resolve IDs once and partition them into eligible agents and rejected IDs."""
    agents: list[object] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for raw_id in agent_ids:
        agent_id = str(raw_id)
        if agent_id in seen:
            continue
        seen.add(agent_id)
        agent = await lookup(agent_id)
        if is_routing_agent_eligible(
            agent,
            user_id=user_id,
            excluded_agent_ids=excluded_agent_ids,
            required_input_modes=required_input_modes,
        ):
            agents.append(agent)
        else:
            rejected.append(agent_id)
    return agents, rejected
