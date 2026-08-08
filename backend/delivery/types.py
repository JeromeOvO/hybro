import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol


class TaskRunner(Protocol):
    def __call__(
        self,
        coro: Awaitable[Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task: ...


class SSEFrameCallback(Protocol):
    def __call__(self, room_id: str, frame: dict[str, Any]) -> Awaitable[None]: ...


InternalEnvelopeCallback = Callable[[dict[str, Any]], Awaitable[None]]


class RoomSubscriptionLimitExceeded(RuntimeError):
    pass


__all__ = [
    "InternalEnvelopeCallback",
    "RoomSubscriptionLimitExceeded",
    "SSEFrameCallback",
    "TaskRunner",
]
