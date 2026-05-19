"""HubTransportMiddleware — selects relay transport for hub-associated agents.

During pre-dispatch, checks ``agent.hub_id``. If the agent is associated
with a hub (whether originally hub-sourced or enriched from a cloud
registration) the transport is switched to ``"relay"``.

An authoritative Redis-backed liveness check determines whether the hub
is actually connected.  If not, the DB flag is corrected immediately so
the frontend shows accurate status, and the dispatch is denied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from execution.dispatch.dispatch_middleware import DispatchContext

if TYPE_CHECKING:
    from models.processing import ProcessingResult
    from services.relay_service import RelayService


class HubTransportMiddleware:
    def __init__(self, relay_service: RelayService) -> None:
        self._relay = relay_service

    async def pre_dispatch(self, ctx: DispatchContext) -> DispatchContext:
        if ctx.agent.hub_id:
            ctx.transport = "relay"
            if not await self._relay.is_hub_alive(ctx.agent.hub_id):
                await self._relay.mark_hub_agents_offline(ctx.agent.hub_id)
                ctx.denied = True
                ctx.deny_reason = "Agent is offline — hub is disconnected"
        return ctx

    async def post_dispatch(
        self, ctx: DispatchContext, result: ProcessingResult
    ) -> ProcessingResult:
        return result
