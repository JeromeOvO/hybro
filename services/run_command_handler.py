"""Single writer for `runs` / `run_events` (v1 orchestration run_id == trigger user message_id)."""

from __future__ import annotations

import os
from typing import Any

from pymongo.errors import DuplicateKeyError

from common.utils.logger import get_logger
from common.utils.time import utcnow
from database.mongodb import mongodb
from models.run import Run, RunEvent, RunEventType, RunState, TERMINAL_RUN_STATES
from services.a2a_constants import SSEProcessingStatus
from services.run_metrics import increment_counter
from services.run_reducer import RunTransitionError, ensure_transition_allowed

logger = get_logger(__name__)


def _feature_run_dual_write_enabled() -> bool:
    raw = (os.environ.get("FEATURE_RUN_DUAL_WRITE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


class RunCommandHandler:
    """Append-only run lifecycle + materialized head (orchestration runs only in v1)."""

    def _normalize_status(self, status: Any) -> str:
        return status.value if hasattr(status, "value") else str(status)

    def _run_id_for_message(self, message_id: str) -> str:
        return message_id

    async def record_processing_status(
        self,
        room_id: str,
        status: Any,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist lifecycle from processing_status semantics. Returns last run_event payload for SSE."""
        if not _feature_run_dual_write_enabled() or not room_id or not message_id:
            return None

        status_value = self._normalize_status(status)
        run_id = self._run_id_for_message(message_id)

        try:
            if status_value == SSEProcessingStatus.PROCESSING:
                return await self._record_active(
                    room_id=room_id,
                    run_id=run_id,
                    trigger_message_id=message_id,
                    client_request_id=client_request_id,
                    awaiting_input=False,
                )
            if status_value == SSEProcessingStatus.AWAITING_INPUT:
                return await self._record_active(
                    room_id=room_id,
                    run_id=run_id,
                    trigger_message_id=message_id,
                    client_request_id=client_request_id,
                    awaiting_input=True,
                )
            if status_value == SSEProcessingStatus.COMPLETED:
                return await self._record_terminal(
                    room_id=room_id,
                    run_id=run_id,
                    trigger_message_id=message_id,
                    client_request_id=client_request_id,
                    terminal_state=RunState.COMPLETED,
                    error_code=None,
                    error_message=None,
                )
            if status_value == SSEProcessingStatus.CANCELED:
                return await self._record_terminal(
                    room_id=room_id,
                    run_id=run_id,
                    trigger_message_id=message_id,
                    client_request_id=client_request_id,
                    terminal_state=RunState.CANCELED,
                    error_code="CANCELED",
                    error_message=details,
                )
            if status_value in {
                SSEProcessingStatus.FAILED,
                SSEProcessingStatus.REJECTED,
                SSEProcessingStatus.RATE_LIMITED,
                SSEProcessingStatus.ERROR,
            }:
                return await self._record_terminal(
                    room_id=room_id,
                    run_id=run_id,
                    trigger_message_id=message_id,
                    client_request_id=client_request_id,
                    terminal_state=RunState.FAILED,
                    error_code=status_value.upper(),
                    error_message=details,
                )
        except RunTransitionError as e:
            increment_counter(
                "run_transition_errors_total",
                source="processing_status",
                status=status_value,
            )
            logger.debug(
                "RunCommandHandler: skipped illegal transition (room=%s run=%s): %s",
                room_id,
                run_id,
                e,
            )
        except Exception as e:
            logger.warning(
                "RunCommandHandler: failed to persist run status (room=%s run=%s status=%s): %s",
                room_id,
                run_id,
                status_value,
                e,
            )
        return None

    async def append_run_timeout_failure(
        self, room_id: str, run_id: str, *, stale_minutes: int
    ) -> dict[str, Any] | None:
        """Watchdog: fail a stuck non-terminal run."""
        if not _feature_run_dual_write_enabled():
            return None
        run_doc = await mongodb.runs_collection.find_one({"run_id": run_id})
        if not run_doc or str(run_doc.get("room_id")) != room_id:
            return None
        trigger = str(run_doc.get("trigger_message_id") or run_id)
        return await self._record_terminal(
            room_id=room_id,
            run_id=run_id,
            trigger_message_id=trigger,
            client_request_id=run_doc.get("client_request_id"),
            terminal_state=RunState.FAILED,
            error_code="RUN_TIMEOUT",
            error_message=f"No terminal transition within {stale_minutes} minutes",
        )

    async def _record_active(
        self,
        *,
        room_id: str,
        run_id: str,
        trigger_message_id: str,
        client_request_id: str | None,
        awaiting_input: bool,
    ) -> dict[str, Any] | None:
        run_doc = await mongodb.runs_collection.find_one({"run_id": run_id})
        if not run_doc:
            run = Run(
                run_id=run_id,
                room_id=room_id,
                trigger_message_id=trigger_message_id,
                parent_message_id=trigger_message_id,
                client_request_id=client_request_id,
                state=RunState.QUEUED,
                seq=0,
            )
            try:
                await mongodb.runs_collection.insert_one(run.model_dump(mode="json"))
            except DuplicateKeyError:
                pass
            run_doc = await mongodb.runs_collection.find_one({"run_id": run_id}) or run.model_dump(
                mode="json"
            )
            await self._append_event_and_project(
                run_doc=run_doc,
                event_type=RunEventType.RUN_CREATED,
                next_state=RunState.QUEUED,
                payload={},
            )
            run_doc = await mongodb.runs_collection.find_one({"run_id": run_id}) or run_doc

        current_state = RunState(run_doc.get("state", RunState.QUEUED))
        if current_state in TERMINAL_RUN_STATES:
            return None

        if awaiting_input:
            if current_state == RunState.AWAITING_INPUT:
                return None
            event_type = RunEventType.RUN_AWAITING_INPUT
            next_state = RunState.AWAITING_INPUT
        else:
            if current_state == RunState.PROCESSING:
                return None
            event_type = (
                RunEventType.RUN_RESUMED
                if current_state == RunState.AWAITING_INPUT
                else RunEventType.RUN_STARTED
            )
            next_state = RunState.PROCESSING

        return await self._append_event_and_project(
            run_doc=run_doc,
            event_type=event_type,
            next_state=next_state,
            payload={},
        )

    async def _record_terminal(
        self,
        *,
        room_id: str,
        run_id: str,
        trigger_message_id: str,
        client_request_id: str | None,
        terminal_state: RunState,
        error_code: str | None,
        error_message: str | None,
    ) -> dict[str, Any] | None:
        run_doc = await mongodb.runs_collection.find_one({"run_id": run_id})
        if not run_doc:
            run = Run(
                run_id=run_id,
                room_id=room_id,
                trigger_message_id=trigger_message_id,
                parent_message_id=trigger_message_id,
                client_request_id=client_request_id,
                state=RunState.QUEUED,
                seq=0,
            )
            try:
                await mongodb.runs_collection.insert_one(run.model_dump(mode="json"))
            except DuplicateKeyError:
                pass
            run_doc = await mongodb.runs_collection.find_one({"run_id": run_id}) or run.model_dump(
                mode="json"
            )
            await self._append_event_and_project(
                run_doc=run_doc,
                event_type=RunEventType.RUN_CREATED,
                next_state=RunState.QUEUED,
                payload={},
            )
            run_doc = await mongodb.runs_collection.find_one({"run_id": run_id}) or run_doc

        current_state = RunState(run_doc.get("state", RunState.QUEUED))
        if current_state in TERMINAL_RUN_STATES:
            return None

        terminal_event_map = {
            RunState.COMPLETED: RunEventType.RUN_COMPLETED,
            RunState.FAILED: RunEventType.RUN_FAILED,
            RunState.CANCELED: RunEventType.RUN_CANCELED,
        }
        return await self._append_event_and_project(
            run_doc=run_doc,
            event_type=terminal_event_map[terminal_state],
            next_state=terminal_state,
            payload={
                "error_code": error_code,
                "error_message": error_message,
            },
        )

    async def _append_event_and_project(
        self,
        *,
        run_doc: dict,
        event_type: RunEventType,
        next_state: RunState,
        payload: dict,
    ) -> dict[str, Any] | None:
        now = utcnow()
        current_seq = int(run_doc.get("seq", 0))
        next_seq = current_seq + 1
        run_id = str(run_doc["run_id"])
        room_id = str(run_doc["room_id"])
        current_state = RunState(run_doc.get("state", RunState.QUEUED))

        try:
            ensure_transition_allowed(current_state, next_state)
        except RunTransitionError:
            increment_counter(
                "run_transition_errors_total",
                source="append",
                event_type=event_type.value,
                from_state=current_state.value,
                to_state=next_state.value,
            )
            raise

        event = RunEvent(
            run_id=run_id,
            room_id=room_id,
            seq=next_seq,
            type=event_type,
            payload=payload,
            ts=now,
        )
        dumped = event.model_dump(mode="json")
        try:
            await mongodb.run_events_collection.insert_one(dumped)
        except DuplicateKeyError:
            return None

        updates: dict[str, Any] = {
            "state": next_state.value,
            "seq": next_seq,
            "updated_at": now,
        }
        if next_state == RunState.PROCESSING and not run_doc.get("started_at"):
            updates["started_at"] = now
        if next_state in TERMINAL_RUN_STATES:
            updates["ended_at"] = now
            updates["error_code"] = payload.get("error_code")
            updates["error_message"] = payload.get("error_message")

        await mongodb.runs_collection.update_one(
            {"run_id": run_id},
            {"$set": updates},
        )
        increment_counter(
            "run_event_append_total",
            event_type=event_type.value,
            to_state=next_state.value,
        )

        return {
            "event_id": dumped.get("event_id"),
            "run_id": run_id,
            "room_id": room_id,
            "seq": next_seq,
            "type": event_type.value,
            "payload": payload,
            "ts": dumped.get("ts"),
        }


run_command_handler = RunCommandHandler()


def run_event_sse_enabled() -> bool:
    raw = (os.environ.get("FEATURE_RUN_EVENT_SSE") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")
