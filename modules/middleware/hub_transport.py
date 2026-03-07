"""HubTransportMiddleware — selects relay transport for hub-sourced agents.

During pre-dispatch, checks ``agent.source``. If the agent is a hub agent
the transport is switched to ``"relay"``.  If the hub is offline the
``queued_for_offline`` flag is set so the relay service can queue the event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.dispatch_middleware import DispatchContext

if TYPE_CHECKING:
    from models.processing import ProcessingResult
    from services.relay_service import RelayService


class HubTransportMiddleware:
    def __init__(self, relay_service: RelayService) -> None:
        self._relay = relay_service

    async def pre_dispatch(self, ctx: DispatchContext) -> DispatchContext:
        if ctx.agent.source == "hub":
            if not ctx.agent.hub_id:
                ctx.denied = True
                ctx.deny_reason = "Hub agent is missing hub_id"
                return ctx
            ctx.transport = "relay"
            if not ctx.agent.is_hub_online:
                ctx.metadata["queued_for_offline"] = True
        return ctx

    async def post_dispatch(
        self, ctx: DispatchContext, result: ProcessingResult
    ) -> ProcessingResult:
        return result
