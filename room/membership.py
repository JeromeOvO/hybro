from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from common.dto import AgentInfo, MembershipSeed
from common.protocols import AgentRegistry, RoomMembershipSeedSource


@dataclass(frozen=True)
class ResolvedMembership:
    agent_set: dict[str, str]
    membership_origin: str
    membership_origin_status: str
    source_group_id: str | None = None
    source_group_name: str | None = None


async def resolve_membership_seed(
    *,
    seed: MembershipSeed,
    owner_id: str,
    agent_registry: AgentRegistry,
    membership_source: RoomMembershipSeedSource,
) -> ResolvedMembership:
    requesting_user_id = seed.requesting_user_id or owner_id
    if seed.mode == "manual":
        agent_set = await _resolve_explicit_agents(
            agent_ids=list(seed.agent_ids or []),
            requesting_user_id=requesting_user_id,
            agent_registry=agent_registry,
            require_active=False,
        )
        return ResolvedMembership(
            agent_set=agent_set,
            membership_origin="manual",
            membership_origin_status="manual",
        )

    if seed.mode == "saved_group":
        if not seed.group_id:
            raise ValueError("group_id is required for saved_group seed input")
        group = await membership_source.get_saved_group(seed.group_id)
        if group is None:
            raise ValueError(f"Saved group {seed.group_id} not found")
        if group.type != "builtin" and group.owner_id != requesting_user_id:
            raise ValueError("You do not have permission to use this saved group")
        agent_set = await _resolve_explicit_agents(
            agent_ids=list(group.agent_ids),
            requesting_user_id=requesting_user_id,
            agent_registry=agent_registry,
            require_active=True,
            skip_missing=True,
        )
        return ResolvedMembership(
            agent_set=agent_set,
            membership_origin="saved_group",
            membership_origin_status="seeded_never_edited",
            source_group_id=group.group_id,
            source_group_name=group.name,
        )

    if seed.mode == "all_current_agents":
        agents = await membership_source.list_current_agents(requesting_user_id)
        return ResolvedMembership(
            agent_set=_agent_set_from_agents(
                [
                    agent
                    for agent in agents
                    if _is_active(agent) and _is_visible(agent, requesting_user_id)
                ]
            ),
            membership_origin="all_current_agents",
            membership_origin_status="seeded_never_edited",
        )

    raise ValueError(f"Invalid membership seed mode: {seed.mode}")


def normalize_room_agent_set(room_agent_set: dict | None) -> dict[str, str]:
    if not room_agent_set:
        return {}

    keys_look_like_ids = sum(1 for key in room_agent_set if _looks_like_agent_id(key))
    values_look_like_ids = sum(
        1 for value in room_agent_set.values() if _looks_like_agent_id(value)
    )
    if keys_look_like_ids >= values_look_like_ids:
        return {str(key): str(value) for key, value in room_agent_set.items()}
    return {
        str(agent_id): str(agent_name)
        for agent_name, agent_id in room_agent_set.items()
        if isinstance(agent_id, str)
    }


async def _resolve_explicit_agents(
    *,
    agent_ids: list[str],
    requesting_user_id: str | None,
    agent_registry: AgentRegistry,
    require_active: bool,
    skip_missing: bool = False,
) -> dict[str, str]:
    if not agent_ids:
        return {}

    agents = await agent_registry.get_agents_by_ids(agent_ids)
    agents_by_id = {agent.agent_id: agent for agent in agents}
    missing = [agent_id for agent_id in agent_ids if agent_id not in agents_by_id]
    if missing and not skip_missing:
        raise ValueError(f"Unknown or deleted agent IDs: {', '.join(missing)}")

    selected: list[AgentInfo] = []
    inaccessible: list[str] = []
    inactive: list[str] = []
    for agent_id in agent_ids:
        agent = agents_by_id.get(agent_id)
        if agent is None:
            continue
        if require_active and not _is_active(agent):
            inactive.append(agent_id)
            continue
        if not _is_visible(agent, requesting_user_id):
            inaccessible.append(agent_id)
            continue
        selected.append(agent)

    if inaccessible:
        raise ValueError(f"Access denied to private agents: {', '.join(inaccessible)}")
    if inactive and not skip_missing:
        raise ValueError(f"Inactive agent IDs: {', '.join(inactive)}")
    return _agent_set_from_agents(selected)


def _agent_set_from_agents(agents: list[AgentInfo]) -> dict[str, str]:
    return {agent.agent_id: agent.name or agent.agent_id for agent in agents}


def _is_active(agent: AgentInfo) -> bool:
    return agent.status == "active"


def _is_visible(agent: AgentInfo, user_id: str | None) -> bool:
    return agent.is_public or (user_id is not None and agent.provider_id == user_id)


def _looks_like_agent_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
        return True
    except ValueError:
        return False
