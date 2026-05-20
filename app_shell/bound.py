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
    async def create(self, data: Any) -> Any: ...
    async def delete(self, item_id: Any) -> Any: ...
    async def get(self, item_id: Any) -> Any: ...
    async def get_all(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
        sort: list[tuple[str, int]] | None = None,
    ) -> Any: ...
    async def patch(self, item_id: Any, data: Any) -> Any: ...
    async def update(self, item_id: Any, data: Any) -> Any: ...


@runtime_checkable
class WebhookTransport(Protocol):
    async def handle_webhook(
        self, message_id: str, payload: dict[str, Any], token: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class WebhookTransportFactory(Protocol):
    def __call__(self) -> WebhookTransport: ...


__all__ = [
    "InspectionCenter",
    "ViewSetRepository",
    "WebhookTransport",
    "WebhookTransportFactory",
]
