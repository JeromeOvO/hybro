from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

EventHandler = Callable[[Any], Awaitable[None] | None]
RemoteEventCallback = Callable[[str], Awaitable[None]]


@runtime_checkable
class InternalEventPublisher(Protocol):
    async def publish(
        self,
        event: BaseModel,
        *,
        wait_for_handlers: bool = False,
        fanout: bool = True,
    ) -> None: ...


@runtime_checkable
class InternalEventBus(InternalEventPublisher, Protocol):
    @property
    def is_connected(self) -> bool: ...
    def register_handler(self, event_type: str, handler: EventHandler) -> None: ...
    async def refresh_health(self) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class InternalEventTransport(Protocol):
    @property
    def is_connected(self) -> bool: ...
    async def start(self, callback: RemoteEventCallback) -> None: ...
    async def publish(self, message: str) -> None: ...
    async def publish_dead_letter(self, message: str) -> None: ...
    async def refresh_health(self) -> None: ...
    async def stop_ingress(self) -> None: ...
    async def stop(self) -> None: ...


__all__ = [
    "EventHandler",
    "InternalEventBus",
    "InternalEventPublisher",
    "InternalEventTransport",
    "RemoteEventCallback",
]
