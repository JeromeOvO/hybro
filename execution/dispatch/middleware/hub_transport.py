"""HubTransportMiddleware — selects relay transport for hub-associated agents.

During pre-dispatch, checks ``agent.hub_id``. If the agent is associated
with a hub (whether originally hub-sourced or enriched from a cloud
registration) the transport is switched to ``"relay"``.

An authoritative Redis-backed liveness check determines whether the hub
is actually connected.  If not, the DB flag is corrected immediately so
the frontend shows accurate status, and the dispatch is denied.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from execution.dispatch.dispatch_middleware import DispatchContext

if TYPE_CHECKING:
    from models.processing import ProcessingResult


class HubTransportMiddleware:
    def __init__(self, dispatch_policy: Any) -> None:
        self._policy = dispatch_policy

    async def pre_dispatch(self, ctx: DispatchContext) -> DispatchContext:
        if ctx.agent.hub_id:
            ctx.transport = "relay"
            if hasattr(self._policy, "is_hub_alive"):
                decision = self._policy.is_hub_alive(ctx.agent.hub_id)
            elif hasattr(self._policy, "can_dispatch_to_hub"):
                decision = self._policy.can_dispatch_to_hub(
                    ctx.agent.hub_id, ctx.agent.agent_id
                )
            elif hasattr(self._policy, "is_hub_online"):
                decision = self._policy.is_hub_online(ctx.agent.hub_id)
            else:
                decision = False
            can_dispatch = await decision if inspect.isawaitable(decision) else bool(decision)
            if not can_dispatch:
                marker = getattr(self._policy, "mark_hub_agents_offline", None)
                if marker is not None:
                    await marker(ctx.agent.hub_id)
                ctx.denied = True
                ctx.deny_reason = "Agent is offline — hub is disconnected"
        return ctx

    async def post_dispatch(
        self, ctx: DispatchContext, result: ProcessingResult
    ) -> ProcessingResult:
        return result
