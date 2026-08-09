from __future__ import annotations

from typing import Any

from common.utils.logger import get_logger
from execution.ports import ProcessingStatusLike
from execution.run_lifecycle_outcome import RunLifecycleWriteOutcome
from models.run import RunState

logger = get_logger(__name__)


class RunLifecycleAdapter:
    def __init__(self, command_handler, run_repository) -> None:
        self._command_handler = command_handler
        self._run_repository = run_repository
        self._terminal_finalizer = None

    def bind_terminal_finalizer(self, finalizer) -> None:
        self._terminal_finalizer = finalizer

    async def record_processing_status(
        self,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        status_value = getattr(status, "value", status)
        error_message = kwargs.get("error_message")
        details = kwargs.get("details")
        if error_message is None and isinstance(details, dict):
            error_message = details.get("message") or details.get("error")
        return await self._command_handler.record_processing_status(
            room_id=room_id,
            status=status_value,
            message_id=message_id,
            client_request_id=kwargs.get("client_request_id"),
            details=error_message,
            terminal_projection=kwargs.get("terminal_projection"),
        )

    async def write_processing_status(
        self,
        room_id: str,
        status: ProcessingStatusLike,
        message_id: str | None,
        **kwargs: Any,
    ) -> RunLifecycleWriteOutcome:
        status_value = getattr(status, "value", status)
        error_message = kwargs.get("error_message")
        details = kwargs.get("details")
        if error_message is None and isinstance(details, dict):
            error_message = details.get("message") or details.get("error")
        writer = getattr(self._command_handler, "write_processing_status", None)
        try:
            if callable(writer):
                return await writer(
                    room_id=room_id,
                    status=status_value,
                    message_id=message_id,
                    client_request_id=kwargs.get("client_request_id"),
                    details=error_message,
                    terminal_projection=kwargs.get("terminal_projection"),
                )
            payload = await self._command_handler.record_processing_status(
                room_id=room_id,
                status=status_value,
                message_id=message_id,
                client_request_id=kwargs.get("client_request_id"),
                details=error_message,
                terminal_projection=kwargs.get("terminal_projection"),
            )
        except Exception as exc:
            return RunLifecycleWriteOutcome.error(exc)
        return (
            RunLifecycleWriteOutcome.accepted(payload)
            if payload is not None
            else RunLifecycleWriteOutcome.conflict()
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
        payload = await self._command_handler.project_run_state(
            room_id=room_id,
            run_id=run_id,
            trigger_message_id=trigger_message_id,
            target_state=target_state,
            terminal_reason=terminal_reason,
            causation_id=causation_id,
            client_request_id=client_request_id,
            terminal_summary=terminal_summary,
        )
        if payload and payload.get("terminal_projection") is not None:
            await self.finalize_terminal_projection(payload)
        return payload

    async def finalize_terminal_projection(self, payload: dict[str, Any]) -> None:
        if self._terminal_finalizer is not None:
            await self._terminal_finalizer.finalize(payload)

    async def recover_terminal_projections(self, limit: int = 100) -> int:
        if self._terminal_finalizer is None:
            return 0
        return await self._terminal_finalizer.recover_pending(limit=limit)

    async def list_incomplete_terminal_projections(self, limit: int = 100):
        return await self._command_handler.list_incomplete_terminal_projections(limit)

    async def claim_terminal_projection_step(self, *args, **kwargs):
        return await self._command_handler.claim_terminal_projection_step(
            *args, **kwargs
        )

    async def complete_terminal_projection_step(self, *args, **kwargs):
        return await self._command_handler.complete_terminal_projection_step(
            *args, **kwargs
        )

    async def release_terminal_projection_step(self, *args, **kwargs):
        return await self._command_handler.release_terminal_projection_step(
            *args, **kwargs
        )

    async def block_terminal_projection_step(self, *args, **kwargs):
        return await self._command_handler.block_terminal_projection_step(
            *args, **kwargs
        )

    async def refresh_terminal_projection_schedule(self, event_id: str) -> bool:
        return await self._command_handler.refresh_terminal_projection_schedule(
            event_id
        )

    async def heal_diverged_runs(self, limit: int = 500) -> int:
        try:
            docs = await self._run_repository.get_diverged(limit=limit)
        except Exception:
            logger.warning(
                "startup heal: failed to query non-terminal runs", exc_info=True
            )
            return 0

        healed = 0
        for doc in docs:
            run_id = str(doc.get("run_id", ""))
            if not run_id:
                continue
            try:
                if await self._command_handler.heal_head_from_events(run_id):
                    healed += 1
            except Exception:
                logger.warning(
                    "startup heal: error healing run %s", run_id, exc_info=True
                )
        return healed

    async def append_run_timeout_failure(
        self,
        room_id: str,
        run_id: str,
        *,
        stale_minutes: int,
    ) -> dict[str, Any] | None:
        payload = await self._command_handler.append_run_timeout_failure(
            room_id,
            run_id,
            stale_minutes=stale_minutes,
        )
        if payload and payload.get("terminal_projection") is not None:
            await self.finalize_terminal_projection(payload)
        return payload
