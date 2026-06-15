from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from common.dto import (
    AgentMessageFinal,
    ArtifactUpdateEvent,
    DeliveryEvent,
    ErrorEvent,
    ProcessingStatusEvent,
    TaskSubmittedEvent,
    TaskUpdateEvent,
)
from common.utils.cancellation import CancellationToken
from common.utils.time import utcnow


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


class _DeliveryCompat(Protocol):
    async def open_connection(self, room_id: str) -> Any: ...
    async def remove_connection(self, room_id: str, connection_id: str) -> None: ...
    def get_room_status(self, room_id: str) -> dict: ...
    def is_cancelled(self, message_id: str) -> bool: ...
    def cancel_message(self, message_id: str) -> None: ...
    async def cancel_message_and_broadcast(self, message_id: str) -> None: ...
    async def check_cancelled(self, message_id: str) -> bool: ...
    def clear_cancellation(self, message_id: str) -> None: ...
    def create_token(self, message_id: str) -> CancellationToken: ...
    def get_token(self, message_id: str) -> CancellationToken | None: ...
    def remove_token(self, message_id: str) -> None: ...
    async def start_change_stream_watcher(self) -> None: ...
    async def stop_change_stream_watcher(self) -> None: ...
    async def start_redis_service(self, redis_service: Any | None = None) -> None: ...
    async def stop_redis_service(self) -> None: ...
    async def start_event_broker(self, broker: Any | None = None) -> None: ...
    async def stop_event_broker(self) -> None: ...
    async def refresh_health(self) -> None: ...
    def set_draining(self, draining: bool) -> None: ...
    @property
    def change_stream_connected(self) -> bool: ...
    @property
    def delivery_kv_connected(self) -> bool: ...
    @property
    def delivery_pubsub_connected(self) -> bool: ...
    @property
    def redis_connected(self) -> bool: ...
    @property
    def broker_connected(self) -> bool: ...


class _DeliveryFacadeLike(Protocol):
    compat: _DeliveryCompat
    event_publisher: Any
    instance_id: str
    async def emit(self, event: DeliveryEvent) -> None: ...


class SSEConnection:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.connection_id = str(uuid4())
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.connected_at = utcnow()
        self.is_active = True

    async def send_message(self, message_type: str, data: Any) -> bool:
        if not self.is_active:
            return False
        await self.queue.put(
            json.dumps(
                {
                    "type": message_type,
                    "timestamp": utcnow().isoformat(),
                    "room_id": self.room_id,
                    "data": data,
                }
            )
        )
        return True

    async def get_message(self, timeout: float = 30.0) -> str:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except TimeoutError:
            return json.dumps(
                {
                    "type": "heartbeat",
                    "timestamp": utcnow().isoformat(),
                    "room_id": self.room_id,
                    "data": {},
                }
            )

    def close(self) -> None:
        self.is_active = False


class AppShellSSEManager:
    def __init__(self) -> None:
        self._facade: _DeliveryFacadeLike | None = None
        self._fallback_instance_id = str(uuid4())

    def bind_facade(self, delivery_facade: _DeliveryFacadeLike) -> None:
        self._facade = delivery_facade

    def unbind_facade(self) -> None:
        self._facade = None

    @property
    def _instance_id(self) -> str:
        return self._facade.instance_id if self._facade is not None else self._fallback_instance_id

    def _compat(self) -> _DeliveryCompat:
        if self._facade is None:
            raise RuntimeError("SSEManager.bind_facade() not called - startup incomplete")
        return self._facade.compat

    @property
    def room_connections(self) -> dict:
        if self._facade is None:
            return {}
        return getattr(self._facade.compat, "room_connections", {})

    @property
    def _change_stream_task(self) -> None:
        return None

    @property
    def _shutdown_flag(self) -> bool:
        return False

    async def start_event_broker(self, broker: Any | None = None) -> None:
        await self._compat().start_event_broker(broker)

    async def stop_event_broker(self) -> None:
        await self._compat().stop_event_broker()

    @property
    def broker_connected(self) -> bool:
        return self._facade is not None and self._facade.compat.broker_connected

    async def start_redis_service(self, redis_service: Any | None = None) -> None:
        await self._compat().start_redis_service(redis_service)

    async def stop_redis_service(self) -> None:
        await self._compat().stop_redis_service()

    @property
    def redis_connected(self) -> bool:
        return self._facade is not None and self._facade.compat.redis_connected

    def set_draining(self, flag: bool) -> None:
        self._compat().set_draining(flag)

    async def add_connection(self, room_id: str) -> Any:
        return await self._compat().open_connection(room_id)

    async def remove_connection(self, room_id: str, connection_id: str) -> None:
        await self._compat().remove_connection(room_id, connection_id)

    async def _emit_event(self, event: DeliveryEvent) -> None:
        if self._facade is None:
            raise RuntimeError("SSEManager.bind_facade() not called - startup incomplete")
        await self._facade.emit(event)

    async def send_agent_response(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        content: str,
        related_message_id: str = None,
        parts: list[dict] | None = None,
        client_request_id: str | None = None,
    ) -> None:
        content_payload = {
            "content": content,
            "related_message_id": related_message_id,
        }
        if client_request_id:
            content_payload["client_request_id"] = client_request_id
        if parts:
            content_payload["parts"] = parts
        await self._emit_event(
            AgentMessageFinal(
                room_id=room_id,
                message_id=message_id,
                agent_id=agent_id,
                content=content_payload,
            )
        )

    async def send_error(self, room_id: str, error: str, message_id: str = None) -> None:
        await self._emit_event(
            ErrorEvent(room_id=room_id, error=error, message_id=message_id)
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
        await self._emit_event(
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
        await self._emit_event(
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
        status: str,
        message_id: str = None,
        details: Any = None,
        related_message_id: str | None = None,
        client_request_id: str | None = None,
        agents: list[dict] | None = None,
    ) -> None:
        await self._emit_event(
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
        await self._emit_event(
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
        parts: list[dict] | None = None,
        client_request_id: str | None = None,
    ) -> None:
        await self._emit_event(
            TaskUpdateEvent(
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
            )
        )

    def get_room_status(self, room_id: str) -> dict:
        return self._compat().get_room_status(room_id)

    async def start_change_stream_watcher(self, db_collection: Any = None) -> None:
        await self._compat().start_change_stream_watcher()

    async def stop_change_stream_watcher(self) -> None:
        await self._compat().stop_change_stream_watcher()

    @property
    def change_stream_connected(self) -> bool:
        return self._facade is not None and self._facade.compat.change_stream_connected

    def cancel_message(self, message_id: str) -> None:
        self._compat().cancel_message(message_id)

    async def cancel_message_and_broadcast(self, message_id: str) -> None:
        await self._compat().cancel_message_and_broadcast(message_id)

    def is_cancelled(self, message_id: str) -> bool:
        return self._compat().is_cancelled(message_id)

    async def check_cancelled(self, message_id: str) -> bool:
        return await self._compat().check_cancelled(message_id)

    def clear_cancellation(self, message_id: str) -> None:
        self._compat().clear_cancellation(message_id)

    def create_token(self, message_id: str) -> CancellationToken:
        return self._compat().create_token(message_id)

    def get_token(self, message_id: str) -> CancellationToken | None:
        return self._compat().get_token(message_id)

    def remove_token(self, message_id: str) -> None:
        self._compat().remove_token(message_id)


SSEManager = AppShellSSEManager
sse_manager = AppShellSSEManager()
