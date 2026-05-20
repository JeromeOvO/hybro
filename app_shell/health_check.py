from typing import Any, Protocol, runtime_checkable

from fastapi import Request


@runtime_checkable
class HealthCheck(Protocol):
    async def check(self, request: Request) -> dict[str, Any]: ...


__all__ = ["HealthCheck"]
