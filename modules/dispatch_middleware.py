"""DispatchMiddleware — composable pre/post-dispatch hooks.

The ``DispatchChain`` runs middleware in order on pre-dispatch (0→N)
and in reverse on post-dispatch (N→0).  If any middleware sets
``ctx.denied = True`` during pre-dispatch, the chain short-circuits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from a2a.types import Message

from models.agent import Agent
from models.processing import ProcessingResult
from models.room import RoomAgentMessage


@dataclass
class DispatchContext:
    agent: Agent
    room_agent_message: RoomAgentMessage
    room_id: str
    user_message_id: str
    prepared_message: Message
    transport: str = "direct"  # "direct" | "relay"
    denied: bool = False
    deny_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DispatchMiddleware(Protocol):
    async def pre_dispatch(self, ctx: DispatchContext) -> DispatchContext: ...

    async def post_dispatch(
        self, ctx: DispatchContext, result: ProcessingResult
    ) -> ProcessingResult: ...


class DispatchChain:
    """Execute a list of ``DispatchMiddleware`` instances in order."""

    def __init__(self, middlewares: list[DispatchMiddleware] | None = None) -> None:
        self._middlewares: list[DispatchMiddleware] = list(middlewares or [])

    def add(self, mw: DispatchMiddleware) -> None:
        self._middlewares.append(mw)

    async def run_pre_dispatch(self, ctx: DispatchContext) -> DispatchContext:
        for mw in self._middlewares:
            ctx = await mw.pre_dispatch(ctx)
            if ctx.denied:
                break
        return ctx

    async def run_post_dispatch(
        self, ctx: DispatchContext, result: ProcessingResult
    ) -> ProcessingResult:
        for mw in reversed(self._middlewares):
            result = await mw.post_dispatch(ctx, result)
        return result
