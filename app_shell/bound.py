from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from models.request import InspectionCenterRequest
from models.response import (
    InsepectionCenterConnectionValidationResponse,
    InspectionCenterResponse,
)


@runtime_checkable
class InspectionCenter(Protocol):
    async def inspect_a2a_connection(
        self, request: InspectionCenterRequest
    ) -> InsepectionCenterConnectionValidationResponse: ...
    async def inspect_agent_card(
        self, request: InspectionCenterRequest
    ) -> InspectionCenterResponse: ...


@runtime_checkable
class ViewSetRepository(Protocol):
    async def create(self, data: Mapping[str, object] | object) -> object: ...
    async def delete(self, item_id: object) -> object: ...
    async def get(self, item_id: object) -> object: ...
    async def get_all(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        filters: Mapping[str, object] | None = None,
        sort: list[tuple[str, int]] | None = None,
    ) -> list[object]: ...
    async def patch(self, item_id: object, data: Mapping[str, object] | object) -> object: ...
    async def update(self, item_id: object, data: Mapping[str, object] | object) -> object: ...


@runtime_checkable
class WebhookTransport(Protocol):
    async def handle_webhook(
        self, message_id: str, payload: dict[str, Any], token: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class WebhookTransportFactory(Protocol):
    def __call__(self) -> WebhookTransport: ...


@runtime_checkable
class LegacyMemoryCenter(Protocol):
    async def add_chat_context(self, request: object) -> object: ...
    async def get_chat_context_by_session_id(self, request: object) -> object: ...
    async def update_chat_context_by_session_id(self, request: object) -> object: ...
    async def delete_chat_context_by_session_id(self, request: object) -> object: ...


@runtime_checkable
class RoomCenterRouteOwner(Protocol):
    async def create_new_room(self, request: object) -> object: ...
    async def inquiry_rooms_by_room_owner_id(self, request: object) -> object: ...
    async def inquiry_room_messages_by_room_id(self, request: object) -> object: ...
    async def inquiry_room_setting(self, request: object) -> object: ...
    async def update_room_setting(self, request: object) -> object: ...
    async def create_and_parse_user_message(self, request: object) -> object: ...
    async def send_message(self, request: object) -> object: ...


@runtime_checkable
class SSEManagerRouteOwner(Protocol):
    async def add_connection(self, room_id: str) -> object: ...
    async def remove_connection(self, room_id: str, connection_id: str) -> None: ...
    def get_room_status(self, room_id: str) -> dict[str, object]: ...


__all__ = [
    "InspectionCenter",
    "LegacyMemoryCenter",
    "RoomCenterRouteOwner",
    "SSEManagerRouteOwner",
    "ViewSetRepository",
    "WebhookTransport",
    "WebhookTransportFactory",
]
