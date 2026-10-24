from typing import Protocol, runtime_checkable

from common.dto import DeliveryEvent
from common.protocols.json_types import JsonValue


@runtime_checkable
class DeliveryEventPublisher(Protocol):
    async def emit(self, event: DeliveryEvent) -> bool | None: ...


# Short alias for the public delivery-only contract.
EventPublisher = DeliveryEventPublisher


@runtime_checkable
class SSEConnectionLike(Protocol):
    connection_id: str
    is_active: bool

    async def get_message(self, timeout: float | None = None) -> str: ...


@runtime_checkable
class SSERouteTransport(Protocol):
    async def add_connection(self, conversation_id: str) -> SSEConnectionLike: ...
    async def remove_connection(
        self, conversation_id: str, connection_id: str
    ) -> None: ...
    def get_room_status(self, conversation_id: str) -> dict[str, JsonValue]: ...


__all__ = [
    "DeliveryEventPublisher",
    "EventPublisher",
    "SSEConnectionLike",
    "SSERouteTransport",
]
