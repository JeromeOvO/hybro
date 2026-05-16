"""Backward-compatible facade for run persistence (delegates to RunCommandHandler)."""

from __future__ import annotations

import os
from typing import Any

from services.run_command_handler import run_command_handler, run_event_sse_enabled


def _feature_run_dual_write_enabled() -> bool:
    raw = (os.environ.get("FEATURE_RUN_DUAL_WRITE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


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
        if not _feature_run_dual_write_enabled():
            return None
        return await run_command_handler.record_processing_status(
            room_id=room_id,
            status=status,
            message_id=message_id,
            client_request_id=client_request_id,
            details=details,
        )


run_lifecycle_service = RunLifecycleService()


async def record_and_maybe_broadcast_run_event(
    room_id: str,
    status: Any,
    message_id: str | None,
    *,
    client_request_id: str | None = None,
    details: str | None = None,
    sse: Any | None = None,
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
        client_request_id=client_request_id,
        sse=sse,
    )
    return payload


async def broadcast_run_event_payload(
    room_id: str,
    payload: dict[str, Any] | None,
    *,
    client_request_id: str | None = None,
    sse: Any | None = None,
) -> None:
    if not (run_event_sse_enabled() and payload):
        return

    if sse is None:
        from services.sse_services import sse_manager

        sse = sse_manager

    await sse.broadcast_to_room(
        room_id,
        "run_event",
        {
            "event_id": payload.get("event_id"),
            "run_id": payload.get("run_id"),
            "seq": payload.get("seq"),
            "type": payload.get("type"),
            "payload": payload.get("payload") or {},
            "correlation_id": client_request_id,
        },
    )
