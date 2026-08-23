from __future__ import annotations

import inspect
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
from execution.run_lifecycle_outcome import (
    RunLifecycleWriteError,
    RunLifecycleWriteOutcome,
    RunLifecycleWriteStatus,
)

logger = logging.getLogger(__name__)

TERMINAL_PROCESSING_STATUSES = {
    "completed",
    "failed",
    "canceled",
    "rejected",
    "rate_limited",
    "error",
}

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
    event_id = str(_require_payload_field(payload, "event_id"))
    return RunEventNotification(
        room_id=room_id,
        event_id=event_id,
        delivery_id=f"terminal:{event_id}:run-event",
        run_id=str(_require_payload_field(payload, "run_id")),
        seq=int(_require_payload_field(payload, "seq")),
        run_event_type=str(_require_payload_field(payload, "type")),
        payload=payload.get("payload") or {},
        correlation_id=payload.get("correlation_id") or correlation_id,
    )


async def _write_terminal_lifecycle(
    *,
    run_lifecycle: RunLifecyclePort,
    room_id: str,
    status_value: str,
    lifecycle_message_id: str | None,
    client_request_id: str | None,
    details: dict[str, Any] | None,
    error_message: str | None,
    terminal_projection: dict[str, Any],
) -> RunLifecycleWriteOutcome:
    checked = inspect.getattr_static(run_lifecycle, "write_processing_status", None)
    if checked is not None:
        try:
            outcome = await run_lifecycle.write_processing_status(
                room_id,
                status_value,
                lifecycle_message_id,
                client_request_id=client_request_id,
                details=details,
                error_message=error_message,
                terminal_projection=terminal_projection,
            )
        except TypeError as exc:
            if "terminal_projection" not in str(exc):
                raise
            outcome = await run_lifecycle.write_processing_status(
                room_id,
                status_value,
                lifecycle_message_id,
                client_request_id=client_request_id,
                details=details,
                error_message=error_message,
            )
        if not isinstance(outcome, RunLifecycleWriteOutcome):
            return RunLifecycleWriteOutcome.error(
                TypeError("invalid checked lifecycle write outcome")
            )
        return outcome

    payload = await run_lifecycle.record_processing_status(
        room_id,
        status_value,
        lifecycle_message_id,
        client_request_id=client_request_id,
        details=details,
        error_message=error_message,
    )
    return (
        RunLifecycleWriteOutcome.accepted(payload)
        if payload is not None
        else RunLifecycleWriteOutcome.conflict()
    )


def _build_terminal_projection(
    *,
    status_value: str,
    frontend_message_id: str,
    lifecycle_message_id: str,
    client_request_id: str | None,
    details: dict[str, Any] | None,
    error_message: str | None,
    agents: list[dict] | None,
    system_message_id: str | None,
    turn_event_enabled: bool,
) -> dict[str, Any]:
    projection_status = (
        "completed"
        if status_value == "completed"
        else "canceled"
        if status_value == "canceled"
        else "failed"
    )
    completion_kind = (
        details.get("turn_completion_kind")
        if status_value == "completed" and details
        else None
    )
    turn_status = (
        status_value if status_value in {"completed", "canceled"} else "failed"
    )
    turn_event_type = f"turn_{turn_status}" if turn_event_enabled else None
    if turn_status == "completed":
        turn_payload: dict[str, Any] = {"duration_ms": 0}
    elif turn_status == "canceled":
        turn_payload = {}
    else:
        turn_payload = {
            "reason": (details or {}).get("message") or error_message or "failed",
            "code": (details or {}).get("code") or "error",
        }
        if details and "terminal_summary" in details:
            turn_payload["terminal_summary"] = details["terminal_summary"]
    steps = {
        "run_event_sse": {"state": "pending"},
        "processing_sse": {"state": "pending"},
    }
    descendant_cleanup_root_id = (
        lifecycle_message_id if status_value != "completed" else None
    )
    if descendant_cleanup_root_id:
        steps["descendant_cleanup"] = {"state": "pending"}
    if system_message_id:
        steps["system_task"] = {"state": "pending"}
        steps["system_task_delivery"] = {"state": "pending"}
    if completion_kind:
        steps["completion_metadata"] = {"state": "pending"}
    if turn_event_type:
        steps["turn_event"] = {"state": "pending"}
    return {
        "version": 1,
        "canonical_status": status_value,
        "frontend_message_id": frontend_message_id,
        "lifecycle_message_id": lifecycle_message_id,
        "descendant_cleanup_root_id": descendant_cleanup_root_id,
        "client_request_id": client_request_id,
        "details": details,
        "agents": agents,
        "system_message_id": system_message_id,
        "system_task_status": projection_status if system_message_id else None,
        "completion_kind": completion_kind,
        "turn_event_type": turn_event_type,
        "turn_event_payload": turn_payload if turn_event_type else None,
        "pending": True,
        "steps": steps,
    }


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
    system_message_id: str | None = None,
    turn_event_enabled: bool = False,
) -> dict[str, Any] | None:
    status_value = _normalize_processing_status(status)
    frontend_message_id = _require_frontend_message_id(message_id)
    typed_details = _typed_processing_status_details(details, error_message)
    resolved_client_request_id = await _resolve_processing_status_client_request_id(
        client_request_id_resolver, message_id, client_request_id
    )
    payload = None
    if record_lifecycle and status_value in TERMINAL_PROCESSING_STATUSES:
        terminal_projection = _build_terminal_projection(
            status_value=status_value,
            frontend_message_id=frontend_message_id,
            lifecycle_message_id=lifecycle_message_id or frontend_message_id,
            client_request_id=resolved_client_request_id,
            details=typed_details,
            error_message=error_message,
            agents=agents,
            system_message_id=system_message_id,
            turn_event_enabled=turn_event_enabled,
        )
        outcome = await _write_terminal_lifecycle(
            run_lifecycle=run_lifecycle,
            room_id=room_id,
            status_value=status_value,
            lifecycle_message_id=lifecycle_message_id or message_id,
            client_request_id=client_request_id,
            details=typed_details,
            error_message=error_message,
            terminal_projection=terminal_projection,
        )
        if outcome.status == RunLifecycleWriteStatus.CONFLICT:
            return None
        if outcome.status == RunLifecycleWriteStatus.ERROR:
            logger.error(
                "terminal lifecycle write failed room_id=%s message_id=%s "
                "status=%s error_class=%s error_fingerprint=%s",
                room_id,
                lifecycle_message_id or message_id,
                status_value,
                outcome.error_class,
                outcome.error_fingerprint,
            )
            raise RunLifecycleWriteError(outcome)
        payload = outcome.payload
        finalizer = (
            getattr(run_lifecycle, "finalize_terminal_projection", None)
            if inspect.getattr_static(
                run_lifecycle, "finalize_terminal_projection", None
            )
            is not None
            else None
        )
        if callable(finalizer) and payload is not None:
            try:
                await finalizer(payload)
            except Exception:
                logger.warning(
                    "terminal projection attempt failed; durable retry remains pending",
                    exc_info=True,
                )
            return payload
        # No bound finalizer: the terminal fallback direct emit is eliminated
        # (Room Stream Snapshot plan §4.1). Terminal frames are delivered
        # exclusively by durable projection recovery; in production the
        # finalizer is always bound (``RunLifecycleAdapter``).
        return payload
    elif record_lifecycle:
        payload = await run_lifecycle.record_processing_status(
            room_id,
            status_value,
            lifecycle_message_id or message_id,
            client_request_id=client_request_id,
            details=typed_details,
            error_message=error_message,
        )
    # Non-terminal statuses are the main direct-emit path (work log and
    # spinner lifecycle). Terminal frames never reach this block: the
    # terminal branch returns above, so only the finalizer emits them.
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
    system_message_id: str | None = None,
    turn_event_enabled: bool = False,
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
        system_message_id=system_message_id,
        turn_event_enabled=turn_event_enabled,
    )
