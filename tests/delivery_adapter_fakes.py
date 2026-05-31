from __future__ import annotations

import json
from typing import Any

from common.utils.cancellation import CancellationToken
from services.sse_services import SSEConnection, SSEManager

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
        self.cancelled_messages: set[str] = set()
        self.tokens: dict[str, CancellationToken] = {}
        self.frames: list[tuple[str, dict]] = []
        self.redis_service = redis_service
        self.terminal_status_sent: set[str] = set()
        self.draining = False
        self.lifecycle_calls: list[tuple[str, Any | None]] = []
        self.change_stream_connected = True
        self.delivery_kv_connected = False
        self.delivery_pubsub_connected = False

    async def emit_legacy_frame(self, room_id: str, frame: dict) -> None:
        if not await self._should_deliver(room_id, frame):
            return
        self.frames.append((room_id, frame))
        for connection in self.room_connections.get(room_id, {}).values():
            await connection.queue.put(json.dumps(frame))

    async def open_connection(self, room_id: str) -> SSEConnection:
        if self.draining:
            raise ConnectionRefusedError("Server is draining - rejecting new SSE connections")
        connection = SSEConnection(room_id=room_id)
        self.room_connections.setdefault(room_id, {})[connection.connection_id] = connection
        return connection

    async def remove_connection(self, room_id: str, connection_id: str) -> None:
        connection = self.room_connections.get(room_id, {}).pop(connection_id, None)
        if connection is not None:
            connection.close()
        if room_id in self.room_connections and not self.room_connections[room_id]:
            self.room_connections.pop(room_id)

    def get_room_status(self, room_id: str) -> dict:
        connections = self.room_connections.get(room_id, {})
        if not connections:
            return {
                "room_id": room_id,
                "active_connections": 0,
                "status": "no_connections",
            }
        return {
            "room_id": room_id,
            "active_connections": len(connections),
            "status": "active",
        }

    def is_cancelled(self, message_id: str) -> bool:
        return message_id in self.cancelled_messages

    def cancel_message(self, message_id: str) -> None:
        self._set_cancelled_local(message_id)

    async def cancel_message_and_broadcast(self, message_id: str) -> None:
        self._set_cancelled_local(message_id)
        if self.redis_service is not None:
            await self.redis_service.set_nx(f"cancelled:{message_id}", "1", ex=3600)

    async def check_cancelled(self, message_id: str) -> bool:
        if message_id in self.cancelled_messages:
            return True
        if self.redis_service is not None and await self.redis_service.exists(
            f"cancelled:{message_id}"
        ):
            self._set_cancelled_local(message_id)
            return True
        return False

    def clear_cancellation(self, message_id: str) -> None:
        self.cancelled_messages.discard(message_id)
        self.tokens.pop(message_id, None)

    def create_token(self, message_id: str) -> CancellationToken:
        token = CancellationToken(message_id=message_id)
        if message_id in self.cancelled_messages:
            token.cancel()
        self.tokens[message_id] = token
        return token

    def get_token(self, message_id: str) -> CancellationToken | None:
        return self.tokens.get(message_id)

    def remove_token(self, message_id: str) -> None:
        self.tokens.pop(message_id, None)

    async def start_change_stream_watcher(self) -> None:
        self.lifecycle_calls.append(("start_change_stream_watcher", None))

    async def stop_change_stream_watcher(self) -> None:
        self.lifecycle_calls.append(("stop_change_stream_watcher", None))

    async def start_redis_service(self, redis_service: Any | None = None) -> None:
        self.lifecycle_calls.append(("start_redis_service", redis_service))
        self.redis_service = redis_service
        self.delivery_kv_connected = redis_service is not None

    async def stop_redis_service(self) -> None:
        self.lifecycle_calls.append(("stop_redis_service", None))
        self.redis_service = None
        self.delivery_kv_connected = False

    async def start_event_broker(self, broker: Any | None = None) -> None:
        self.lifecycle_calls.append(("start_event_broker", broker))
        self.delivery_pubsub_connected = broker is not None

    async def stop_event_broker(self) -> None:
        self.lifecycle_calls.append(("stop_event_broker", None))
        self.delivery_pubsub_connected = False

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

    def _set_cancelled_local(self, message_id: str) -> None:
        self.cancelled_messages.add(message_id)
        token = self.tokens.get(message_id)
        if token is not None:
            token.cancel()

    async def _should_deliver(self, room_id: str, frame: dict) -> bool:
        if frame.get("type") != "processing_status":
            return True
        data = frame.get("data")
        if not isinstance(data, dict):
            return True
        status = data.get("status")
        message_id = data.get("message_id")
        if status not in TERMINAL_STATUSES or not message_id:
            return True

        local_key = f"{room_id}:{message_id}"
        redis_key = f"terminal:{room_id}:{message_id}"
        if local_key in self.terminal_status_sent:
            return False
        if self.redis_service is not None:
            inserted = await self.redis_service.set_nx(redis_key, status, ex=300)
            if not inserted:
                return False
        self.terminal_status_sent.add(local_key)
        return True


class FakeDeliveryFacade:
    def __init__(
        self,
        *,
        compat: FakeDeliveryCompat | None = None,
        instance_id: str = "test-worker",
    ) -> None:
        self.compat = compat or FakeDeliveryCompat()
        self.instance_id = instance_id


def make_bound_manager(
    *,
    compat: FakeDeliveryCompat | None = None,
    redis_service: Any | None = None,
    instance_id: str = "test-worker",
) -> SSEManager:
    if compat is None:
        compat = FakeDeliveryCompat(redis_service=redis_service)
    manager = SSEManager()
    manager.bind_facade(FakeDeliveryFacade(compat=compat, instance_id=instance_id))
    return manager

