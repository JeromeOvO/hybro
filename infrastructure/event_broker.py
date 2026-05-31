from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

# Type alias for the async handler callback
MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


@runtime_checkable
class EventBroker(Protocol):
    """Abstract interface for cross-instance event fan-out.

    Current implementation: RedisBroker (Redis Pub/Sub).
    Future: NATS, RabbitMQ, Kafka — implement this Protocol and update the factory.

    Message envelope format:
        {
            "kind": str,        # message type for dispatch ("sse_event", "cancellation")
            "origin": str,      # source instance ID (for self-dedup)
            ...payload fields
        }
    """

    @property
    def is_connected(self) -> bool:
        """Whether the broker is currently connected and operational."""
        ...

    async def start(self) -> None:
        """Connect to the broker and start the subscriber background task.

        Must be called before publish/subscribe. Should be idempotent.
        """
        ...

    async def stop(self) -> None:
        """Graceful shutdown: stop subscriber, close connections.

        Should be idempotent. Safe to call even if start() was never called.
        """
        ...

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish a message to a channel.

        Best-effort delivery. Implementations SHOULD NOT raise exceptions — log
        failures internally. Callers additionally wrap publish calls in
        try/except as defense-in-depth. This ensures local SSE delivery always
        proceeds regardless of broker health.

        Args:
            channel: Channel name (e.g., "sse:room:abc123" or "cancel:global")
            payload: Message envelope dict (must include "kind" and "origin" fields)
        """
        ...

    async def subscribe(self, channel: str) -> None:
        """Subscribe to a channel. Incoming messages are routed to registered handlers.

        Safe to call multiple times for the same channel (idempotent).
        """
        ...

    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel.

        Safe to call for channels not currently subscribed (no-op).
        """
        ...

    def set_handler(self, kind: str, handler: MessageHandler) -> None:
        """Register an async handler for messages with the given 'kind' field.

        When a message arrives with payload["kind"] == kind, the handler is called
        with the full payload dict. Multiple kinds can be registered.

        Args:
            kind: The value of the "kind" field to match (e.g., "sse_event")
            handler: Async callable(payload: dict) -> None
        """
        ...
