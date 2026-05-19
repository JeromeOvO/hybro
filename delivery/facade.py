from typing import Any

from common.protocols import EventPublisher, RedisKV, SSETransport
from delivery.config import DeliveryConfig, DeliveryStartupPolicy


class DeliveryCompatibility:
    def __init__(self, facade: "DeliveryFacade") -> None:
        self._facade = facade

    async def emit_legacy_frame(self, room_id: str, frame: dict) -> None:
        await self._facade.emit_legacy_frame(room_id, frame)

    async def open_connection(self, room_id: str) -> Any:
        return await self._facade._sse_transport.open_connection(room_id)

    async def remove_connection(self, room_id: str, connection_id: str) -> None:
        await self._facade._sse_transport.remove_connection(room_id, connection_id)

    def get_room_status(self, room_id: str) -> dict:
        return self._facade._sse_transport.get_room_status(room_id)

    @property
    def room_connections(self) -> dict:
        return self._facade._sse_transport.room_connections

    def is_cancelled(self, message_id: str) -> bool:
        return self._facade._sse_transport.is_cancelled(message_id)

    def cancel_message(self, message_id: str) -> None:
        self._facade._sse_transport.cancel_message(message_id)

    async def cancel_message_and_broadcast(self, message_id: str) -> None:
        await self._facade._sse_transport.cancel_message_and_broadcast(message_id)

    async def check_cancelled(self, message_id: str) -> bool:
        return await self._facade._sse_transport.check_cancelled(message_id)

    def clear_cancellation(self, message_id: str) -> None:
        self._facade._sse_transport.clear_cancellation(message_id)

    def create_token(self, message_id: str) -> Any:
        return self._facade._sse_transport.create_token(message_id)

    def get_token(self, message_id: str) -> Any:
        return self._facade._sse_transport.get_token(message_id)

    def remove_token(self, message_id: str) -> None:
        self._facade._sse_transport.remove_token(message_id)

    async def start_change_stream_watcher(self) -> None:
        await self._facade._sse_transport.start_cancellation_watcher()

    async def stop_change_stream_watcher(self) -> None:
        await self._facade._sse_transport.stop_cancellation_watcher()

    async def start_redis_service(self, redis_service: Any | None = None) -> None:
        return None

    async def stop_redis_service(self) -> None:
        return None

    async def start_event_broker(self, broker: Any | None = None) -> None:
        return None

    async def stop_event_broker(self) -> None:
        return None

    def set_draining(self, draining: bool) -> None:
        self._facade.set_draining(draining)

    @property
    def change_stream_connected(self) -> bool:
        watcher = self._facade._cancellation_watcher
        return bool(getattr(watcher, "change_stream_connected", False))

    @property
    def delivery_kv_connected(self) -> bool:
        return self._facade.delivery_kv_connected

    @property
    def delivery_pubsub_connected(self) -> bool:
        return self._facade.delivery_pubsub_connected

    async def refresh_health(self) -> None:
        await self._facade.refresh_health()

    @property
    def redis_connected(self) -> bool:
        return self._facade.redis_connected

    @property
    def broker_connected(self) -> bool:
        return self._facade.broker_connected


class DeliveryFacade:
    def __init__(
        self,
        *,
        event_publisher: EventPublisher,
        sse_transport: SSETransport,
        event_bus: Any,
        cancellation_watcher: Any,
        redis_kv: RedisKV | None,
        config: DeliveryConfig,
        startup_policy: DeliveryStartupPolicy,
        instance_id: str,
    ) -> None:
        self.event_publisher = event_publisher
        self.sse_transport = sse_transport
        self._event_publisher = event_publisher
        self._sse_transport = sse_transport
        self._event_bus = event_bus
        self._cancellation_watcher = cancellation_watcher
        self._redis_kv = redis_kv
        self.config = config
        self.startup_policy = startup_policy
        self.instance_id = instance_id
        self.compat = DeliveryCompatibility(self)
        self._delivery_kv_connected = False
        self._delivery_pubsub_connected = False
        self._started = False
        self._kv_closed = False

    @property
    def delivery_kv_connected(self) -> bool:
        return self._delivery_kv_connected

    @property
    def delivery_pubsub_connected(self) -> bool:
        return self._delivery_pubsub_connected

    @property
    def change_stream_connected(self) -> bool:
        return bool(getattr(self._cancellation_watcher, "change_stream_connected", False))

    @property
    def redis_connected(self) -> bool:
        return self.delivery_kv_connected

    @property
    def broker_connected(self) -> bool:
        return self.delivery_pubsub_connected

    async def emit_legacy_frame(self, room_id: str, frame: dict) -> None:
        await self._event_publisher._emit_legacy_frame(room_id, frame)

    async def refresh_health(self) -> None:
        if self._redis_kv is None:
            self._delivery_kv_connected = False
        else:
            try:
                self._delivery_kv_connected = await self._redis_kv.ping()
            except Exception:
                self._delivery_kv_connected = False

        try:
            await self._event_bus.refresh_health()
            self._delivery_pubsub_connected = bool(self._event_bus.is_connected)
        except Exception:
            self._delivery_pubsub_connected = False

    async def start(self) -> None:
        started: list[str] = []
        try:
            try:
                await self._sse_transport.start_cancellation_watcher()
                started.append("watcher")
            except Exception:
                if not self.startup_policy.allow_degraded_change_stream:
                    raise
            await self._event_bus.start()
            started.append("bus")
            await self.refresh_health()
            await self._event_publisher.start()
            started.append("publisher")
            self._started = True
        except Exception:
            await self._rollback_start(started)
            raise

    async def stop(self) -> None:
        if not self._started:
            await self._close_kv_once()
            return
        await self._event_publisher.stop()
        await self._sse_transport.close_all_connections()
        await self._event_bus.stop()
        await self._sse_transport.stop_cancellation_watcher()
        await self._close_kv_once()
        self._started = False

    def set_draining(self, draining: bool) -> None:
        self._sse_transport.set_draining(draining)

    async def _rollback_start(self, started: list[str]) -> None:
        if "publisher" in started:
            await self._event_publisher.stop()
        if "bus" in started:
            await self._event_bus.stop()
        if "watcher" in started:
            await self._sse_transport.stop_cancellation_watcher()
        self._started = False

    async def _close_kv_once(self) -> None:
        if self._redis_kv is not None and not self._kv_closed:
            await self._redis_kv.close()
            self._kv_closed = True


__all__ = ["DeliveryCompatibility", "DeliveryFacade"]
