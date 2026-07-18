"""Backward-compatible facade for run persistence (delegates to RunCommandHandler)."""

from __future__ import annotations

from typing import Any

from common.protocols import EventPublisher
from execution.events import run_event_notification_from_payload
from execution.run_command_handler import (
    RunCommandHandler,
    feature_run_dual_write_enabled,
    run_event_sse_enabled,
)
from execution.run_command_handler import (
    run_command_handler as _default_run_command_handler,
)
from models.run import RunState


class RunLifecycleService:
    """Delegates to :class:`RunCommandHandler` (single writer for runs / run_events)."""

    async def record_processing_status(
        self,
        room_id: str,
        status: Any,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: dict[str, Any] | str | None = None,
    ) -> dict[str, Any] | None:
        if not feature_run_dual_write_enabled():
            return None
        detail_text = (
            details.get("message") or details.get("error")
            if isinstance(details, dict)
            else details
        )
        return await run_command_handler.record_processing_status(
            room_id=room_id,
            status=status,
            message_id=message_id,
            client_request_id=client_request_id,
            details=detail_text,
        )

    async def project_run_state(
        self,
        *,
        room_id: str,
        run_id: str,
        trigger_message_id: str,
        target_state: RunState,
        terminal_reason: str | None,
        causation_id: str,
        client_request_id: str | None = None,
        terminal_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not feature_run_dual_write_enabled():
            return None
        return await run_command_handler.project_run_state(
            room_id=room_id,
            run_id=run_id,
            trigger_message_id=trigger_message_id,
            target_state=target_state,
            terminal_reason=terminal_reason,
            causation_id=causation_id,
            client_request_id=client_request_id,
            terminal_summary=terminal_summary,
        )


run_command_handler = _default_run_command_handler
run_lifecycle_service = RunLifecycleService()


def bind_run_lifecycle_service(command_handler: RunCommandHandler) -> None:
    global run_command_handler

    run_command_handler = command_handler


async def record_and_maybe_emit_run_event(
    room_id: str,
    status: Any,
    message_id: str | None,
    *,
    event_publisher: EventPublisher,
    client_request_id: str | None = None,
    details: dict[str, Any] | str | None = None,
) -> dict[str, Any] | None:
    payload = await run_lifecycle_service.record_processing_status(
        room_id=room_id,
        status=status,
        message_id=message_id,
        client_request_id=client_request_id,
        details=details,
    )
    await emit_run_event_payload(
        room_id,
        payload,
        event_publisher=event_publisher,
        client_request_id=client_request_id,
    )
    return payload


def build_run_event_payload(
    payload: dict[str, Any],
    *,
    client_request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": payload.get("event_id"),
        "run_id": payload.get("run_id"),
        "seq": payload.get("seq"),
        "type": payload.get("type"),
        "payload": payload.get("payload") or {},
        "correlation_id": client_request_id,
    }


async def emit_run_event_payload(
    room_id: str,
    payload: dict[str, Any] | None,
    *,
    event_publisher: EventPublisher,
    client_request_id: str | None = None,
) -> None:
    if not (run_event_sse_enabled() and payload):
        return

    await event_publisher.emit(
        run_event_notification_from_payload(
            room_id=room_id,
            payload=payload,
            correlation_id=client_request_id,
        )
    )
