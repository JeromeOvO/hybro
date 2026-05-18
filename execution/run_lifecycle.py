from __future__ import annotations

from typing import Any

from common.utils.logger import get_logger
from execution.ports import ProcessingStatusLike
from models.run import NON_TERMINAL_RUN_STATE_VALUES

logger = get_logger(__name__)


class RunLifecycleAdapter:
    def __init__(self, command_handler, runs_collection) -> None:
        self._command_handler = command_handler
        self._runs_collection = runs_collection

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
        )

    async def heal_diverged_runs(self, limit: int = 500) -> int:
        try:
            cursor = self._runs_collection.find(
                {"state": {"$in": list(NON_TERMINAL_RUN_STATE_VALUES)}},
            ).limit(limit)
            docs = await cursor.to_list(length=limit)
        except Exception:
            logger.warning("startup heal: failed to query non-terminal runs", exc_info=True)
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
                logger.warning("startup heal: error healing run %s", run_id, exc_info=True)
        return healed

    async def append_run_timeout_failure(
        self,
        room_id: str,
        run_id: str,
        *,
        stale_minutes: int,
    ) -> dict[str, Any] | None:
        return await self._command_handler.append_run_timeout_failure(
            room_id,
            run_id,
            stale_minutes=stale_minutes,
        )
