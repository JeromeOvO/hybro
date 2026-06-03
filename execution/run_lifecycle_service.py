"""Backward-compatible facade for run persistence (delegates to RunCommandHandler)."""

from __future__ import annotations

from typing import Any, Protocol

from execution.run_command_handler import (
    RunCommandHandler,
    feature_run_dual_write_enabled,
    run_event_sse_enabled,
)


class RunEventBroadcaster(Protocol):
    async def broadcast_to_room(
        self,
        room_id: str,
        message_type: str,
        data: Any,
    ) -> Any:
        ...


class RunLifecycleService:
    """Delegates to :class:`RunCommandHandler` (single writer for runs / run_events)."""

    async def record_processing_status(
        self,
        room_id: str,
        status: Any,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: str | None = None,
    ) -> dict[str, Any] | None:
        if not feature_run_dual_write_enabled():
            return None
        return await run_command_handler.record_processing_status(
            room_id=room_id,
            status=status,
            message_id=message_id,
            client_request_id=client_request_id,
            details=details,
        )


run_command_handler = RunCommandHandler()
run_lifecycle_service = RunLifecycleService()


def bind_run_lifecycle_service(command_handler: RunCommandHandler) -> None:
    global run_command_handler

    run_command_handler = command_handler


async def record_and_maybe_broadcast_run_event(
    room_id: str,
    status: Any,
    message_id: str | None,
    *,
    sse: RunEventBroadcaster,
    client_request_id: str | None = None,
    details: str | None = None,
) -> dict[str, Any] | None:
    payload = await run_lifecycle_service.record_processing_status(
        room_id=room_id,
        status=status,
        message_id=message_id,
        client_request_id=client_request_id,
        details=details,
    )
    await broadcast_run_event_payload(
        room_id,
        payload,
        sse=sse,
        client_request_id=client_request_id,
    )
    return payload


def build_run_event_sse_payload(
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


async def broadcast_run_event_payload(
    room_id: str,
    payload: dict[str, Any] | None,
    *,
    sse: RunEventBroadcaster,
    client_request_id: str | None = None,
) -> None:
    if not (run_event_sse_enabled() and payload):
        return

    await sse.broadcast_to_room(
        room_id,
        "run_event",
        build_run_event_sse_payload(payload, client_request_id=client_request_id),
    )
