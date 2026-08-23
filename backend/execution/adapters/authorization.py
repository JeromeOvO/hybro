"""Production AuthorizationRefreshPort over room membership and the agent registry.

``resource_refs`` is accepted for protocol compatibility but intentionally
ignored: bindings are frozen with a pre-filtered ``compatible_resource_refs``
set, so the resource refs on a command are already authorized at binding time
and re-checking them here would duplicate the binding's scope.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from common.protocols import AgentRegistry, RoomOwnershipReader
from execution.orchestrator.a2a_runtime.models import AgentToolBindingRecord

AuthorizationOutcome = Literal["authorized", "denied", "transient_failure"]


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class MembershipAuthorizationRefresh:
    """Re-check a frozen binding against the live room/agent state.

    Fail-closed: transient storage outages return ``transient_failure``, and any
    unknown/inactive/removed agent returns ``denied``.
    """

    def __init__(
        self,
        *,
        agents: AgentRegistry,
        room_ownership: RoomOwnershipReader,
    ) -> None:
        self._agents = agents
        self._room_ownership = room_ownership

    async def authorize(
        self,
        *,
        binding: AgentToolBindingRecord,
        requesting_subject_id: str,
        room_id: str,
        room_epoch: int,
        resource_refs: list[str],
    ) -> AuthorizationOutcome:
        del resource_refs
        if binding.room_id != room_id or binding.room_epoch != room_epoch:
            return "denied"
        if _digest(requesting_subject_id) != binding.requesting_subject_digest:
            return "denied"
        if getattr(binding, "authorization_kind", None) != "all_active_agents":
            # The user explicitly asked to coordinate every active agent; the
            # visibility-filtered candidate listing is the authorization for
            # that scope. Every other scope is gated by room membership.
            try:
                member = await self._room_ownership.verify_room_agent_membership(
                    room_id, binding.agent_id
                )
            except (ConnectionError, TimeoutError):
                return "transient_failure"
            if not member:
                return "denied"
        try:
            info = await self._agents.get_agent(binding.agent_id)
        except (ConnectionError, TimeoutError):
            return "transient_failure"
        if info is None or str(getattr(info, "status", "") or "") != "active":
            return "denied"
        if not (
            getattr(info, "is_public", True)
            or getattr(info, "provider_id", None) == requesting_subject_id
        ):
            return "denied"
        return "authorized"


__all__ = ["MembershipAuthorizationRefresh"]
