from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from common.dto import DeliveryEvent
from common.protocols.json_types import JsonValue


@runtime_checkable
class DeliveryEventPublisher(Protocol):
    async def emit(self, event: DeliveryEvent) -> bool | None: ...


# Compatibility name for the public delivery-only contract.
EventPublisher = DeliveryEventPublisher


@runtime_checkable
class SSETransport(Protocol):
    def connect(self, room_id: str, connection_id: str) -> AsyncIterator[dict]: ...
    async def disconnect(self, connection_id: str) -> None: ...
    async def broadcast_frame_to_room(self, room_id: str, frame: dict) -> int: ...
    def set_draining(self, draining: bool) -> None: ...


@runtime_checkable
class SSEConnectionLike(Protocol):
    connection_id: str
    is_active: bool

    async def get_message(self, timeout: float | None = None) -> str: ...


@runtime_checkable
class SSERouteTransport(Protocol):
    async def add_connection(self, room_id: str) -> SSEConnectionLike: ...
    async def remove_connection(self, room_id: str, connection_id: str) -> None: ...
    def get_room_status(self, room_id: str) -> dict[str, JsonValue]: ...


__all__ = [
    "DeliveryEventPublisher",
    "EventPublisher",
    "SSEConnectionLike",
    "SSERouteTransport",
    "SSETransport",
]
