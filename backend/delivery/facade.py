import time
from enum import Enum
from typing import Any

from cachetools import TTLCache

from common.dto import (
    AgentMessageFinal,
    ArtifactUpdateEvent,
    DeliveryEmitStatus,
    DeliveryEvent,
    ErrorEvent,
    ProcessingStatusEvent,
    TaskSubmittedEvent,
    TaskUpdateEvent,
)
from common.observability import get_logger
from common.protocols import EventPublisher, RedisKV, SSETransport
from delivery.config import DeliveryConfig

logger = get_logger(__name__)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


class DeliveryCompatibility:
    def __init__(self, facade: "DeliveryFacade") -> None:
        self._facade = facade

    async def open_connection(self, room_id: str) -> Any:
        return await self._facade.open_connection(room_id)

    async def remove_connection(self, room_id: str, connection_id: str) -> None:
        await self._facade.remove_connection(room_id, connection_id)

    def get_room_status(self, room_id: str) -> dict:
        return self._facade.get_room_status(room_id)

    @property
    def room_connections(self) -> dict:
        return self._facade.room_connections

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
        redis_kv: RedisKV | None,
        config: DeliveryConfig,
        instance_id: str,
    ) -> None:
        self.event_publisher = event_publisher
        self.sse_transport = sse_transport
        self._event_publisher = event_publisher
        self._sse_transport = sse_transport
        self._event_bus = event_bus
        self._redis_kv = redis_kv
        self.config = config
        self.instance_id = instance_id
        self.compat = DeliveryCompatibility(self)
        self._delivery_kv_connected = False
        self._delivery_pubsub_connected = False
        self._started = False
        self._kv_closed = False
        self._delivery_started_at: TTLCache[tuple[str, str], float] = TTLCache(
            maxsize=config.delivery_started_cache_maxsize,
            ttl=config.delivery_started_ttl_seconds,
        )
        self._terminal_delivery_logged: TTLCache[tuple[str, str], bool] = TTLCache(
            maxsize=config.terminal_dedup_cache_maxsize,
            ttl=config.terminal_dedup_ttl_seconds,
        )

    @property
    def delivery_kv_connected(self) -> bool:
        return self._delivery_kv_connected

    @property
    def delivery_pubsub_connected(self) -> bool:
        return self._delivery_pubsub_connected

    @property
    def redis_connected(self) -> bool:
        return self.delivery_kv_connected

    @property
    def broker_connected(self) -> bool:
        return self.delivery_pubsub_connected

    async def emit(self, event: DeliveryEvent) -> bool:
        result = await self._event_publisher.emit(event)
        return result is not False

    async def open_connection(self, room_id: str) -> Any:
        return await self._sse_transport.open_connection(room_id)

    async def add_connection(self, room_id: str) -> Any:
        return await self.open_connection(room_id)

    async def remove_connection(self, room_id: str, connection_id: str) -> None:
        await self._sse_transport.remove_connection(room_id, connection_id)

    def get_room_status(self, room_id: str) -> dict:
        return self._sse_transport.get_room_status(room_id)

    @property
    def room_connections(self) -> dict:
        return self._sse_transport.room_connections

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
            await self._event_bus.start()
            started.append("bus")
            await self.refresh_health()
            self._started = True
        except Exception:
            await self._rollback_start(started)
            raise

    async def stop(self) -> None:
        if not self._started:
            await self._close_kv_once()
            return
        await self._sse_transport.close_all_connections()
        await self._event_bus.stop()
        await self._close_kv_once()
        self._started = False

    async def send_agent_response(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        content: str,
        related_message_id: str | None = None,
        parts: list[dict] | None = None,
        client_request_id: str | None = None,
    ) -> None:
        content_payload: dict[str, Any] = {
            "content": content,
            "related_message_id": related_message_id,
        }
        if client_request_id:
            content_payload["client_request_id"] = client_request_id
        if parts:
            content_payload["parts"] = parts
        delivered = await self.emit(
            AgentMessageFinal(
                room_id=room_id,
                message_id=message_id,
                agent_id=agent_id,
                content=content_payload,
            )
        )
        self._record_terminal_delivery(
            room_id=room_id,
            message_id=message_id,
            outcome="completed" if delivered else "delivery_failed",
            terminal_kind="agent_message_final",
            agent_id=agent_id,
        )

    async def send_error(
        self,
        room_id: str,
        error: str,
        message_id: str | None = None,
    ) -> None:
        delivered = await self.emit(
            ErrorEvent(room_id=room_id, error=error, message_id=message_id)
        )
        if message_id:
            self._record_terminal_delivery(
                room_id=room_id,
                message_id=message_id,
                outcome="error" if delivered else "delivery_failed",
                terminal_kind="error",
            )

    async def send_rate_limit_error(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        reason: str,
        retry_after_seconds: int | None = None,
        user_requests_used: int = 0,
        user_requests_limit: int | None = None,
        system_requests_used: int = 0,
        system_requests_limit: int | None = None,
    ) -> None:
        if message_id:
            self._delivery_started_at.setdefault(
                (room_id, message_id),
                time.perf_counter(),
            )
        delivered = await self.emit(
            ErrorEvent(
                room_id=room_id,
                error=reason,
                error_type="rate_limit_exceeded",
                message_id=message_id,
                agent_id=agent_id,
                retry_after_seconds=retry_after_seconds,
                user_requests_used=user_requests_used,
                user_requests_limit=user_requests_limit,
                system_requests_used=system_requests_used,
                system_requests_limit=system_requests_limit,
            )
        )
        self._record_terminal_delivery(
            room_id=room_id,
            message_id=message_id,
            outcome="rate_limited" if delivered else "delivery_failed",
            terminal_kind="rate_limit_error",
            agent_id=agent_id,
        )

    async def send_artifact_update(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        artifact: Any,
        append: bool = False,
        last_chunk: bool = False,
        client_request_id: str | None = None,
    ) -> None:
        await self.emit(
            ArtifactUpdateEvent(
                room_id=room_id,
                message_id=message_id,
                agent_id=agent_id,
                artifact=artifact,
                append=append,
                last_chunk=last_chunk,
                client_request_id=client_request_id,
            )
        )

    async def send_processing_status(
        self,
        room_id: str,
        status: Any,
        message_id: str | None = None,
        details: Any = None,
        related_message_id: str | None = None,
        client_request_id: str | None = None,
        agents: list[dict] | None = None,
    ) -> None:
        delivered = await self.emit(
            ProcessingStatusEvent(
                room_id=room_id,
                message_id=message_id,
                status=_enum_value(status),
                details=(
                    details
                    if isinstance(details, dict)
                    else {"message": details}
                    if isinstance(details, str)
                    else None
                ),
                related_message_id=related_message_id,
                client_request_id=client_request_id,
                agents=agents,
            )
        )
        normalized_status = str(_enum_value(status)).lower()
        if message_id and normalized_status in self.config.terminal_processing_statuses:
            self._record_terminal_delivery(
                room_id=room_id,
                message_id=message_id,
                outcome=normalized_status if delivered else "delivery_failed",
                terminal_kind="processing_status",
            )

    async def send_task_submitted(
        self,
        room_id: str,
        message_id: str,
        task_id: str,
        agent_name: str,
        agent_id: str | None = None,
        status: Any = "working",
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        client_request_id: str | None = None,
    ) -> None:
        self._delivery_started_at.setdefault(
            (room_id, message_id),
            time.perf_counter(),
        )
        await self.emit(
            TaskSubmittedEvent(
                room_id=room_id,
                message_id=message_id,
                task_id=task_id,
                agent_name=agent_name,
                agent_id=agent_id,
                status=_enum_value(status),
                related_message_id=related_message_id,
                created_at=created_at,
                step_number=step_number,
                total_steps=total_steps,
                task_content=task_content,
                client_request_id=client_request_id,
            )
        )

    async def send_task_update(
        self,
        room_id: str,
        message_id: str,
        status: Any,
        content: str | None = None,
        error: str | None = None,
        requires_input: bool = False,
        requires_auth: bool = False,
        status_message: str | None = None,
        agent_name: str | None = None,
        agent_id: str | None = None,
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        client_request_id: str | None = None,
        delivery_id: str | None = None,
    ) -> bool:
        event = TaskUpdateEvent(
            room_id=room_id,
            message_id=message_id,
            status=_enum_value(status),
            content=content,
            error=error,
            requires_input=requires_input,
            requires_auth=requires_auth,
            status_message=status_message,
            agent_name=agent_name,
            agent_id=agent_id,
            related_message_id=related_message_id,
            created_at=created_at,
            step_number=step_number,
            total_steps=total_steps,
            task_content=task_content,
            parts=parts,
            client_request_id=client_request_id,
            delivery_id=delivery_id,
        )
        checked = getattr(self._event_publisher, "emit_checked", None)
        if delivery_id and callable(checked):
            outcome = await checked(event)
            delivered = outcome in {
                DeliveryEmitStatus.DELIVERED,
                DeliveryEmitStatus.ALREADY_DELIVERED,
                DeliveryEmitStatus.DELIVERED.value,
                DeliveryEmitStatus.ALREADY_DELIVERED.value,
            }
        else:
            delivered = await self.emit(event)
        normalized_status = str(_enum_value(status)).lower()
        if normalized_status in self.config.terminal_processing_statuses:
            self._record_terminal_delivery(
                room_id=room_id,
                message_id=message_id,
                outcome=normalized_status if delivered else "delivery_failed",
                terminal_kind="task_update",
                agent_id=agent_id,
            )
        return delivered

    def set_draining(self, draining: bool) -> None:
        self._sse_transport.set_draining(draining)

    async def _rollback_start(self, started: list[str]) -> None:
        if "bus" in started:
            await self._event_bus.stop()
        self._started = False

    async def _close_kv_once(self) -> None:
        if self._redis_kv is not None and not self._kv_closed:
            await self._redis_kv.close()
            self._kv_closed = True

    def _record_terminal_delivery(
        self,
        *,
        room_id: str,
        message_id: str,
        outcome: str,
        terminal_kind: str,
        agent_id: str | None = None,
    ) -> None:
        delivery_key = (room_id, message_id)
        if outcome != "delivery_failed":
            if delivery_key in self._terminal_delivery_logged:
                self._delivery_started_at.pop(delivery_key, None)
                return
            self._terminal_delivery_logged[delivery_key] = True
        started_at = self._delivery_started_at.pop(
            delivery_key,
            time.perf_counter(),
        )
        logger.info(
            "delivery_completed",
            extra={
                "room_id": room_id,
                "message_id": message_id,
                "agent_id": agent_id,
                "outcome": outcome,
                "terminal_kind": terminal_kind,
                "duration_ms": round(
                    (time.perf_counter() - started_at) * 1000,
                    3,
                ),
            },
        )


__all__ = ["DeliveryCompatibility", "DeliveryFacade"]
