from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from common.dto import ProcessingStatusEvent, RunEventNotification
from common.protocols import EventPublisher
from execution.ports import (
    ClientRequestIdResolver,
    ProcessingStatusLike,
    RunLifecyclePort,
)

logger = logging.getLogger(__name__)

SUPPORTED_TYPED_PROCESSING_STATUSES = {
    "queued",
    "processing",
    "awaiting_input",
    "completed",
    "failed",
    "canceled",
    "rejected",
    "rate_limited",
    "error",
}


def _processing_status_value(status: ProcessingStatusLike) -> str:
    return str(getattr(status, "value", status))


def _normalize_processing_status(status: ProcessingStatusLike) -> str:
    value = _processing_status_value(status)
    if value not in SUPPORTED_TYPED_PROCESSING_STATUSES:
        raise ValueError(f"Unsupported ProcessingStatusEvent status: {value}")
    return value


def _typed_processing_status_details(
    details: dict[str, Any] | None,
    error_message: str | None,
) -> dict[str, Any] | None:
    if details is not None:
        return details
    if error_message:
        return {"message": error_message}
    return None


def _require_payload_field(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or value == "":
        raise ValueError(f"Run event payload missing required field: {key}")
    return value


def _require_frontend_message_id(message_id: str | None) -> str:
    if not message_id:
        raise ValueError("ProcessingStatusEvent requires frontend message_id")
    return message_id


async def _resolve_processing_status_client_request_id(
    resolver: ClientRequestIdResolver,
    message_id: str | None,
    client_request_id: str | None,
) -> str | None:
    try:
        return await resolver.resolve_client_request_id(message_id, client_request_id)
    except Exception:
        logger.warning(
            "processing status client_request_id resolution failed for message_id=%s",
            message_id,
            exc_info=True,
        )
        return client_request_id


def run_event_notification_from_payload(
    *,
    room_id: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> RunEventNotification:
    return RunEventNotification(
        room_id=room_id,
        event_id=str(_require_payload_field(payload, "event_id")),
        run_id=str(_require_payload_field(payload, "run_id")),
        seq=int(_require_payload_field(payload, "seq")),
        run_event_type=str(_require_payload_field(payload, "type")),
        payload=payload.get("payload") or {},
        correlation_id=payload.get("correlation_id") or correlation_id,
    )


async def emit_processing_status(
    *,
    room_id: str,
    status: ProcessingStatusLike,
    message_id: str | None,
    run_lifecycle: RunLifecyclePort,
    event_publisher: EventPublisher,
    run_event_enabled: Callable[[], bool],
    client_request_id_resolver: ClientRequestIdResolver,
    lifecycle_message_id: str | None = None,
    record_lifecycle: bool = True,
    client_request_id: str | None = None,
    details: dict[str, Any] | None = None,
    error_message: str | None = None,
    agents: list[dict] | None = None,
) -> dict[str, Any] | None:
    status_value = _normalize_processing_status(status)
    frontend_message_id = _require_frontend_message_id(message_id)
    typed_details = _typed_processing_status_details(details, error_message)
    payload = None
    if record_lifecycle:
        payload = await run_lifecycle.record_processing_status(
            room_id,
            status_value,
            lifecycle_message_id or message_id,
            client_request_id=client_request_id,
            details=typed_details,
            error_message=error_message,
        )
    resolved_client_request_id = await _resolve_processing_status_client_request_id(
        client_request_id_resolver,
        message_id,
        client_request_id,
    )
    if payload and run_event_enabled():
        await event_publisher.emit(
            run_event_notification_from_payload(
                room_id=room_id,
                payload=payload,
                correlation_id=client_request_id,
            )
        )
    await event_publisher.emit(
        ProcessingStatusEvent(
            room_id=room_id,
            message_id=frontend_message_id,
            status=status_value,
            related_message_id=lifecycle_message_id,
            details=typed_details,
            client_request_id=resolved_client_request_id,
            agents=agents,
        )
    )
    return payload


def _room_processing_status_details(
    details: dict[str, Any] | str | None,
    error_message: str | None,
) -> dict[str, Any] | None:
    if isinstance(details, dict):
        return details
    if isinstance(details, str):
        return {"message": details}
    if error_message:
        return {"message": error_message}
    return None


def _room_processing_status_error_message(
    status: ProcessingStatusLike,
    details: dict[str, Any] | str | None,
    error_message: str | None,
) -> str | None:
    if error_message:
        return error_message
    status_value = _processing_status_value(status)
    if isinstance(details, str) and status_value in {"failed", "canceled"}:
        return details
    return None


async def emit_room_processing_status(
    *,
    room_id: str,
    status: ProcessingStatusLike,
    message_id: str | None,
    run_lifecycle: RunLifecyclePort,
    event_publisher: EventPublisher,
    run_event_enabled: Callable[[], bool],
    client_request_id_resolver: ClientRequestIdResolver,
    lifecycle_message_id: str | None = None,
    record_lifecycle: bool = True,
    client_request_id: str | None = None,
    details: dict[str, Any] | str | None = None,
    error_message: str | None = None,
    agents: list[dict] | None = None,
) -> dict[str, Any] | None:
    normalized_error = _room_processing_status_error_message(
        status, details, error_message
    )
    return await emit_processing_status(
        room_id=room_id,
        status=status,
        message_id=message_id,
        run_lifecycle=run_lifecycle,
        event_publisher=event_publisher,
        run_event_enabled=run_event_enabled,
        client_request_id_resolver=client_request_id_resolver,
        lifecycle_message_id=lifecycle_message_id,
        record_lifecycle=record_lifecycle,
        client_request_id=client_request_id,
        details=_room_processing_status_details(details, normalized_error),
        error_message=normalized_error,
        agents=agents,
    )
