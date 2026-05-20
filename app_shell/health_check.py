from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HealthCheck(Protocol):
    async def __call__(self) -> dict[str, Any]: ...


__all__ = ["HealthCheck"]
