"""Backward-compatible facade for run persistence (delegates to RunCommandHandler)."""

from __future__ import annotations

import os
from typing import Any

from services.run_command_handler import run_command_handler


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
    ) -> None:
        if not _feature_run_dual_write_enabled():
            return
        await run_command_handler.record_processing_status(
            room_id=room_id,
            status=status,
            message_id=message_id,
            client_request_id=client_request_id,
            details=details,
        )


run_lifecycle_service = RunLifecycleService()
