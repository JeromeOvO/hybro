import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

from common.observability import MetricsCollector, NoopMetricsCollector
from common.utils.cancellation import CancellationToken
from delivery.config import DeliveryConfig
from delivery.sse.connection import SSEConnection
from delivery.types import TaskRunner


class SSETransportImpl:
    def __init__(
        self,
        *,
        cancellation_watcher: Any,
        event_bus: Any,
        config: DeliveryConfig,
        now: Callable[[], datetime],
        id_factory: Callable[[], str],
        instance_id: str,
        task_runner: TaskRunner,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.cancellation_watcher = cancellation_watcher
        self.event_bus = event_bus
        self.config = config
        self._now = now
        self._id_factory = id_factory
        self.instance_id = instance_id
        self._task_runner = task_runner
        self._metrics = metrics or NoopMetricsCollector()
        self.room_connections: dict[str, dict[str, SSEConnection]] = {}
        self.connection_rooms: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._admission_locks: dict[str, asyncio.Lock] = {}
        self._admission_locks_lock = asyncio.Lock()
        self._draining = False
        self._record_connection_gauge()

    def connect(self, room_id: str, connection_id: str) -> AsyncIterator[dict]:
        async def iterator() -> AsyncIterator[dict]:
            connection = await self._admit_connection(room_id, connection_id)
            try:
                while connection.is_active:
                    yield await connection.next_frame(
                        timeout=self.config.heartbeat_interval_seconds
                    )
            finally:
                await self.disconnect(connection_id)

        return iterator()

    async def disconnect(self, connection_id: str) -> None:
        room_id = self.connection_rooms.get(connection_id)
        if room_id is None:
            return
        await self.remove_connection(room_id, connection_id)

    async def open_connection(self, room_id: str) -> SSEConnection:
        return await self._admit_connection(room_id, self._id_factory())

    async def remove_connection(self, room_id: str, connection_id: str) -> None:
        unsubscribed_room = await self._remove_connections(room_id, [connection_id])
        if unsubscribed_room is not None:
            await self.event_bus.unsubscribe_room(unsubscribed_room)

    async def broadcast_frame_to_room(self, room_id: str, frame: dict[str, Any]) -> None:
        async with self._lock:
            snapshot = list(self.room_connections.get(room_id, {}).items())

        disconnected: list[str] = []
        for connection_id, connection in snapshot:
            if not await connection.send_frame(frame):
                disconnected.append(connection_id)

        if disconnected:
            unsubscribed_room = await self._remove_connections(room_id, disconnected)
            if unsubscribed_room is not None:
                await self.event_bus.unsubscribe_room(unsubscribed_room)

    async def close_all_connections(self) -> None:
        async with self._lock:
            rooms = list(self.room_connections)
            connections = [
                connection
                for room_connections in self.room_connections.values()
                for connection in room_connections.values()
            ]
            self.room_connections.clear()
            self.connection_rooms.clear()

        for connection in connections:
            connection.close()
        for room_id in rooms:
            await self.event_bus.unsubscribe_room(room_id)
        self._record_connection_gauge()

    def get_room_status(self, room_id: str) -> dict[str, Any]:
        room_connections = self.room_connections.get(room_id, {})
        return {
            "room_id": room_id,
            "connection_count": len(room_connections),
            "connections": list(room_connections),
        }

    def is_cancelled(self, message_id: str) -> bool:
        return self.cancellation_watcher.is_cancelled(message_id)

    async def mark_cancelled(self, message_id: str) -> None:
        await self.cancellation_watcher.mark_cancelled(message_id)

    def cancel_message(self, message_id: str) -> None:
        self.cancellation_watcher.cancel_message(message_id)

    async def cancel_message_and_broadcast(self, message_id: str) -> None:
        await self.cancellation_watcher.mark_cancelled(message_id)

    async def check_cancelled(self, message_id: str) -> bool:
        return await self.cancellation_watcher.check_cancelled(message_id)

    def clear_cancellation(self, message_id: str) -> None:
        self.cancellation_watcher.clear_cancellation(message_id)

    def create_token(self, message_id: str) -> CancellationToken:
        return self.cancellation_watcher.create_token(message_id)

    def get_token(self, message_id: str) -> CancellationToken | None:
        return self.cancellation_watcher.get_token(message_id)

    def remove_token(self, message_id: str) -> None:
        self.cancellation_watcher.remove_token(message_id)

    def set_draining(self, draining: bool) -> None:
        self._draining = draining

    async def start_cancellation_watcher(self) -> None:
        await self.cancellation_watcher.start()

    async def stop_cancellation_watcher(self) -> None:
        await self.cancellation_watcher.stop()

    async def _admit_connection(
        self,
        room_id: str,
        connection_id: str,
    ) -> SSEConnection:
        if self._draining:
            raise ConnectionRefusedError("Server is draining - rejecting new SSE connections")

        admission_lock = await self._get_admission_lock(room_id)
        async with admission_lock:
            if self._draining:
                raise ConnectionRefusedError(
                    "Server is draining - rejecting new SSE connections"
                )

            async with self._lock:
                first_for_room = not self.room_connections.get(room_id)

            if first_for_room:
                try:
                    await self.event_bus.subscribe_room(room_id)
                except Exception as exc:
                    raise ConnectionRefusedError(
                        f"Unable to subscribe room {room_id}"
                    ) from exc

            connection = SSEConnection(
                room_id=room_id,
                connection_id=connection_id,
                heartbeat_interval=self.config.heartbeat_interval_seconds,
                now=self._now,
            )
            async with self._lock:
                room_connections = self.room_connections.setdefault(room_id, {})
                room_connections[connection_id] = connection
                self.connection_rooms[connection_id] = room_id
                self._record_connection_gauge()
            return connection

    async def _get_admission_lock(self, room_id: str) -> asyncio.Lock:
        async with self._admission_locks_lock:
            lock = self._admission_locks.get(room_id)
            if lock is None:
                lock = asyncio.Lock()
                self._admission_locks[room_id] = lock
            return lock

    async def _remove_connections(
        self,
        room_id: str,
        connection_ids: list[str],
    ) -> str | None:
        room_empty = False
        async with self._lock:
            room_connections = self.room_connections.get(room_id)
            if not room_connections:
                return None

            for connection_id in connection_ids:
                connection = room_connections.pop(connection_id, None)
                self.connection_rooms.pop(connection_id, None)
                if connection is not None:
                    connection.close()

            if not room_connections:
                self.room_connections.pop(room_id, None)
                room_empty = True

            self._record_connection_gauge()

        return room_id if room_empty else None

    def _active_connection_count(self) -> int:
        return sum(len(connections) for connections in self.room_connections.values())

    def _record_connection_gauge(self) -> None:
        try:
            self._metrics.gauge(
                "hybro_delivery_sse_connections",
                self._active_connection_count(),
                {"worker_id": self.instance_id},
            )
        except Exception:
            return


__all__ = ["SSETransportImpl"]
