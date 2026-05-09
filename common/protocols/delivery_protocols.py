from typing import Protocol, runtime_checkable

from common.dto import DeliveryEvent, InternalEvent, SSEEvent


@runtime_checkable
class SSETransport(Protocol):
    async def add_connection(self, room_id: str, connection_id: str) -> None: ...
    async def remove_connection(self, room_id: str, connection_id: str) -> None: ...
    async def broadcast_to_room(self, room_id: str, event: SSEEvent) -> None: ...
    async def send_processing_status(self, room_id: str, status: str) -> None: ...
    async def cancel_room_streams(self, room_id: str, reason: str | None = None) -> None: ...


@runtime_checkable
class EventPublisher(Protocol):
    async def emit(self, event: DeliveryEvent) -> None: ...
    async def emit_internal(self, event: InternalEvent) -> None: ...


__all__ = [
    "EventPublisher",
    "SSETransport",
]
