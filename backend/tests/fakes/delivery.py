from __future__ import annotations

from typing import Any

from common.utils.time import utcnow
from delivery.config import DeliveryConfig
from delivery.facade import DeliveryFacade
from delivery.sse.connection import SSEConnection
from delivery.translator import to_sse_frame

TERMINAL_STATUSES = {
    "completed",
    "failed",
    "canceled",
    "rejected",
    "rate_limited",
    "error",
}


class FakeDeliveryCompat:
    def __init__(self, redis_service: Any | None = None) -> None:
        self.room_connections: dict[str, dict[str, SSEConnection]] = {}
        self.frames: list[tuple[str, dict]] = []
        self.redis_service = redis_service
        self.terminal_status_sent: set[str] = set()
        self.draining = False
        self.lifecycle_calls: list[tuple[str, Any | None]] = []
        self.delivery_kv_connected = False
        self.delivery_pubsub_connected = False

    async def open_connection(self, conversation_id: str) -> SSEConnection:
        if self.draining:
            raise ConnectionRefusedError(
                "Server is draining - rejecting new SSE connections"
            )
        connection = SSEConnection(
            conversation_id=conversation_id,
            connection_id=f"conn-{len(self.room_connections.get(conversation_id, {})) + 1}",
            heartbeat_interval=0.01,
            now=utcnow,
        )
        self.room_connections.setdefault(conversation_id, {})[
            connection.connection_id
        ] = connection
        return connection

    async def remove_connection(self, conversation_id: str, connection_id: str) -> None:
        connection = self.room_connections.get(conversation_id, {}).pop(
            connection_id, None
        )
        if connection is not None:
            connection.close()
        if (
            conversation_id in self.room_connections
            and not self.room_connections[conversation_id]
        ):
            self.room_connections.pop(conversation_id)

    def get_room_status(self, conversation_id: str) -> dict:
        connections = self.room_connections.get(conversation_id, {})
        if not connections:
            return {
                "conversation_id": conversation_id,
                "active_connections": 0,
                "status": "no_connections",
            }
        return {
            "conversation_id": conversation_id,
            "active_connections": len(connections),
            "status": "active",
        }

    async def start(self) -> None:
        self.lifecycle_calls.append(("start", None))

    async def stop(self) -> None:
        self.lifecycle_calls.append(("stop", None))

    async def close_all_connections(self) -> None:
        self.lifecycle_calls.append(("close_all_connections", None))
        for connections in self.room_connections.values():
            for connection in connections.values():
                connection.close()
        self.room_connections.clear()

    async def publish_sse(self, conversation_id: str, frame: dict[str, Any]) -> bool:
        self.frames.append((frame["type"], frame["data"]))
        return True

    async def publish_dead_letter(self, envelope: dict[str, Any]) -> None:
        self.lifecycle_calls.append(("publish_dead_letter", envelope))

    async def refresh_health(self) -> None:
        self.lifecycle_calls.append(("refresh_health", None))

    def set_draining(self, draining: bool) -> None:
        self.draining = draining

    @property
    def redis_connected(self) -> bool:
        return self.delivery_kv_connected

    @property
    def broker_connected(self) -> bool:
        return self.delivery_pubsub_connected

    @property
    def is_connected(self) -> bool:
        return self.delivery_pubsub_connected

    async def _should_deliver(self, conversation_id: str, frame: dict) -> bool:
        if frame.get("type") != "processing_status":
            return True
        data = frame.get("data")
        if not isinstance(data, dict):
            return True
        status = data.get("status")
        message_id = data.get("message_id")
        if status not in TERMINAL_STATUSES or not message_id:
            return True

        local_key = f"{conversation_id}:{message_id}"
        redis_key = f"terminal:{conversation_id}:{message_id}"
        if local_key in self.terminal_status_sent:
            return False
        if self.redis_service is not None:
            inserted = await self.redis_service.set_nx(redis_key, status, ex=300)
            if not inserted:
                return False
        self.terminal_status_sent.add(local_key)
        return True


class FakeEventPublisher:
    def __init__(self, compat: FakeDeliveryCompat | None = None) -> None:
        self.compat = compat
        self.events = []
        self.lifecycle_calls: list[tuple[str, Any | None]] = []

    async def emit(self, event) -> None:
        self.events.append(event)
        if self.compat is None:
            return
        frame = to_sse_frame(event, timestamp=utcnow())
        conversation_id = frame["conversation_id"]
        if not await self.compat._should_deliver(conversation_id, frame):
            return
        self.compat.frames.append((frame["type"], frame["data"]))
        connections = list(
            self.compat.room_connections.get(conversation_id, {}).values()
        )
        for connection in connections:
            await connection.queue.put(frame)


def make_delivery_facade(
    *,
    compat: FakeDeliveryCompat | None = None,
    redis_service: Any | None = None,
    instance_id: str = "test-worker",
    event_publisher: FakeEventPublisher | None = None,
    config: DeliveryConfig | None = None,
) -> DeliveryFacade:
    if compat is None:
        compat = FakeDeliveryCompat(redis_service=redis_service)
    publisher = event_publisher or FakeEventPublisher(compat)
    if getattr(publisher, "compat", None) is None:
        publisher.compat = compat
    facade = DeliveryFacade(
        event_publisher=publisher,
        sse_transport=compat,
        event_bus=compat,
        redis_kv=None,
        config=config or DeliveryConfig(),
        instance_id=instance_id,
    )
    return facade
