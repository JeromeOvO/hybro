from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class InspectionCenter(Protocol):
    async def inspect_a2a_connection(self, *args: Any, **kwargs: Any) -> Any: ...
    async def inspect_agent_card(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class ViewSetRepository(Protocol):
    async def get(self, *args: Any, **kwargs: Any) -> Any: ...
    async def list(self, *args: Any, **kwargs: Any) -> Any: ...
    async def save(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class WebhookTransportFactory(Protocol):
    def __call__(self) -> Any: ...


__all__ = ["InspectionCenter", "ViewSetRepository", "WebhookTransportFactory"]
