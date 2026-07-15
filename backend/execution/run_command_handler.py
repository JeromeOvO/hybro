"""Single writer for `runs` / `run_events` (v1 orchestration run_id == trigger user message_id)."""

from __future__ import annotations

from typing import Any

from pymongo.errors import DuplicateKeyError

from common.a2a_constants import SSEProcessingStatus
from common.config import settings
from common.observability.run_metrics import increment_counter
from common.protocols import RunEventRepository, RunRepository
from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.run_reducer import RunTransitionError, ensure_transition_allowed
from models.run import TERMINAL_RUN_STATES, Run, RunEvent, RunEventType, RunState

logger = get_logger(__name__)
_UNBOUND_MESSAGE = "RunCommandHandler has not been bound"


class _UnboundRunRepository:
    async def find_one(self, *args, **kwargs):  # pragma: no cover - guardrail
        raise RuntimeError(_UNBOUND_MESSAGE)

    async def insert_one(self, *args, **kwargs):  # pragma: no cover - guardrail
        raise RuntimeError(_UNBOUND_MESSAGE)

    async def update_one(self, *args, **kwargs):  # pragma: no cover - guardrail
        raise RuntimeError(_UNBOUND_MESSAGE)


class _UnboundRunEventRepository:
    async def find_one(self, *args, **kwargs):  # pragma: no cover - guardrail
        raise RuntimeError(_UNBOUND_MESSAGE)

    async def insert_one(self, *args, **kwargs):  # pragma: no cover - guardrail
        raise RuntimeError(_UNBOUND_MESSAGE)


def feature_run_dual_write_enabled() -> bool:
    return settings.feature_run_dual_write


def _feature_run_dual_write_enabled() -> bool:
    return feature_run_dual_write_enabled()


class RunCommandHandler:
    """Append-only run lifecycle + materialized head (orchestration runs only in v1)."""

    def __init__(
        self,
        *,
        run_repository: RunRepository,
        run_event_repository: RunEventRepository,
    ) -> None:
        self._runs = run_repository
        self._run_events = run_event_repository

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

    async def project_run_state(
        self,
        *,
        room_id: str,
        run_id: str,
        trigger_message_id: str,
        target_state: Any,
        terminal_reason: str | None,
        causation_id: str,
        client_request_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not _feature_run_dual_write_enabled():
            return None

        state = RunState(self._normalize_status(target_state))
        existing = await self._find_existing_projection_event(
            run_id=run_id,
            state=state,
            causation_id=causation_id,
        )
        if existing:
            await self._repair_head_from_existing_event(existing)
            return self._event_payload_from_doc(existing)

        if state == RunState.PROCESSING:
            return await self._record_active(
                room_id=room_id,
                run_id=run_id,
                trigger_message_id=trigger_message_id,
                client_request_id=client_request_id,
                awaiting_input=False,
                causation_id=causation_id,
            )
        if state == RunState.AWAITING_INPUT:
            return await self._record_active(
                room_id=room_id,
                run_id=run_id,
                trigger_message_id=trigger_message_id,
                client_request_id=client_request_id,
                awaiting_input=True,
                causation_id=causation_id,
            )
        if state == RunState.COMPLETED:
            return await self._record_terminal(
                room_id=room_id,
                run_id=run_id,
                trigger_message_id=trigger_message_id,
                client_request_id=client_request_id,
                terminal_state=RunState.COMPLETED,
                error_code=None,
                error_message=terminal_reason,
                causation_id=causation_id,
            )
        if state == RunState.CANCELED:
            return await self._record_terminal(
                room_id=room_id,
                run_id=run_id,
                trigger_message_id=trigger_message_id,
                client_request_id=client_request_id,
                terminal_state=RunState.CANCELED,
                error_code="CANCELED",
                error_message=terminal_reason,
                causation_id=causation_id,
            )
        if state == RunState.FAILED:
            return await self._record_terminal(
                room_id=room_id,
                run_id=run_id,
                trigger_message_id=trigger_message_id,
                client_request_id=client_request_id,
                terminal_state=RunState.FAILED,
                error_code="FAILED",
                error_message=terminal_reason,
                causation_id=causation_id,
            )
        return None

    async def heal_head_from_events(self, run_id: str) -> bool:
        """Check run_events for events ahead of runs head and project forward.

        Returns True if the head was healed (a newer event was found and
        projected), False otherwise.  This fixes the divergence scenario
        where run_events has a terminal event committed but the runs head
        update was lost (crash / timeout between the two writes).
        """
        run_doc = await self._runs.find_one({"run_id": run_id})
        if not run_doc:
            return False

        head_seq = int(run_doc.get("seq", 0))

        latest_event = await self._run_events.find_one(
            {"run_id": run_id, "seq": {"$gt": head_seq}},
            sort=[("seq", -1)],
        )
        if not latest_event:
            return False

        event_type_str = str(latest_event.get("type", ""))
        event_seq = int(latest_event["seq"])
        payload = latest_event.get("payload") or {}

        terminal_type_map = {
            RunEventType.RUN_COMPLETED.value: RunState.COMPLETED,
            RunEventType.RUN_FAILED.value: RunState.FAILED,
            RunEventType.RUN_CANCELED.value: RunState.CANCELED,
        }
        active_type_map = {
            RunEventType.RUN_STARTED.value: RunState.PROCESSING,
            RunEventType.RUN_RESUMED.value: RunState.PROCESSING,
            RunEventType.RUN_AWAITING_INPUT.value: RunState.AWAITING_INPUT,
            RunEventType.RUN_CREATED.value: RunState.QUEUED,
        }

        resolved_state: RunState | None = terminal_type_map.get(event_type_str) or active_type_map.get(event_type_str)
        if resolved_state is None:
            logger.warning(
                "heal_head_from_events: unknown event type %s for run %s — skipping",
                event_type_str,
                run_id,
            )
            return False

        current_state = RunState(run_doc.get("state", RunState.QUEUED.value))
        try:
            ensure_transition_allowed(current_state, resolved_state)
        except RunTransitionError:
            logger.warning(
                "heal_head_from_events: run %s would-be-illegal transition %s→%s "
                "(event=%s seq %d→%d) — healing anyway",
                run_id,
                current_state.value,
                resolved_state.value,
                event_type_str,
                head_seq,
                event_seq,
            )

        updates: dict[str, Any] = {
            "state": resolved_state.value,
            "seq": event_seq,
            "updated_at": latest_event.get("ts") or utcnow(),
        }
        if resolved_state in TERMINAL_RUN_STATES:
            updates["ended_at"] = latest_event.get("ts") or utcnow()
            updates["error_code"] = payload.get("error_code")
            updates["error_message"] = payload.get("error_message")

        await self._runs.update_one(
            {"run_id": run_id},
            {"$set": updates},
        )
        room_id = str(run_doc.get("room_id", ""))
        increment_counter(
            "run_head_healed_total",
            event_type=event_type_str,
            to_state=resolved_state.value,
        )
        logger.info(
            "heal_head_from_events: healed run %s (room=%s) seq %d→%d state→%s from event %s",
            run_id,
            room_id,
            head_seq,
            event_seq,
            resolved_state.value,
            event_type_str,
        )
        return True

    async def append_run_timeout_failure(
        self, room_id: str, run_id: str, *, stale_minutes: int
    ) -> dict[str, Any] | None:
        """Watchdog: fail a stuck non-terminal run.

        Before appending a new RUN_FAILED event, checks run_events for any
        events ahead of the runs head.  If found (diverged head), projects
        the head forward instead — avoiding the DuplicateKeyError loop that
        previously left the run stuck forever.
        """
        if not _feature_run_dual_write_enabled():
            return None

        if await self.heal_head_from_events(run_id):
            return None

        run_doc = await self._runs.find_one({"run_id": run_id})
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

    async def _ensure_run_exists(
        self,
        *,
        room_id: str,
        run_id: str,
        trigger_message_id: str,
        client_request_id: str | None,
    ) -> dict[str, Any]:
        """Return an up-to-date run_doc, creating the run + RUN_CREATED event if needed."""
        run_doc = await self._runs.find_one({"run_id": run_id})
        if run_doc:
            return run_doc

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
            await self._runs.insert_one(run.model_dump(mode="json"))
        except DuplicateKeyError:
            pass
        run_doc = await self._runs.find_one({"run_id": run_id}) or run.model_dump(
            mode="json"
        )
        await self._append_event_and_project(
            run_doc=run_doc,
            event_type=RunEventType.RUN_CREATED,
            next_state=RunState.QUEUED,
            payload={},
        )
        return await self._runs.find_one({"run_id": run_id}) or run_doc

    async def _record_active(
        self,
        *,
        room_id: str,
        run_id: str,
        trigger_message_id: str,
        client_request_id: str | None,
        awaiting_input: bool,
        causation_id: str | None = None,
    ) -> dict[str, Any] | None:
        run_doc = await self._ensure_run_exists(
            room_id=room_id,
            run_id=run_id,
            trigger_message_id=trigger_message_id,
            client_request_id=client_request_id,
        )

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
            causation_id=causation_id,
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
        causation_id: str | None = None,
    ) -> dict[str, Any] | None:
        run_doc = await self._ensure_run_exists(
            room_id=room_id,
            run_id=run_id,
            trigger_message_id=trigger_message_id,
            client_request_id=client_request_id,
        )

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
            causation_id=causation_id,
        )

    async def _append_event_and_project(
        self,
        *,
        run_doc: dict,
        event_type: RunEventType,
        next_state: RunState,
        payload: dict,
        causation_id: str | None = None,
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

        existing = await self._find_existing_event_by_causation_id(
            run_id=run_id,
            event_type=event_type,
            causation_id=causation_id,
        )
        if existing:
            await self._repair_head_from_existing_event(existing)
            return self._event_payload_from_doc(existing)

        event = RunEvent(
            run_id=run_id,
            room_id=room_id,
            seq=next_seq,
            type=event_type,
            payload=payload,
            causation_id=causation_id,
            ts=now,
        )
        dumped = event.model_dump(mode="json")
        try:
            await self._run_events.insert_one(dumped)
        except DuplicateKeyError:
            existing = await self._find_existing_event_by_causation_id(
                run_id=run_id,
                event_type=event_type,
                causation_id=causation_id,
            )
            if existing:
                await self._repair_head_from_existing_event(existing)
                return self._event_payload_from_doc(existing)
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

        await self._runs.update_one(
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

    async def _find_existing_projection_event(
        self,
        *,
        run_id: str,
        state: RunState,
        causation_id: str | None,
    ) -> dict[str, Any] | None:
        if state == RunState.PROCESSING:
            event_types = (RunEventType.RUN_STARTED, RunEventType.RUN_RESUMED)
        elif state == RunState.AWAITING_INPUT:
            event_types = (RunEventType.RUN_AWAITING_INPUT,)
        elif state == RunState.COMPLETED:
            event_types = (RunEventType.RUN_COMPLETED,)
        elif state == RunState.CANCELED:
            event_types = (RunEventType.RUN_CANCELED,)
        elif state == RunState.FAILED:
            event_types = (RunEventType.RUN_FAILED,)
        else:
            return None

        for event_type in event_types:
            existing = await self._find_existing_event_by_causation_id(
                run_id=run_id,
                event_type=event_type,
                causation_id=causation_id,
            )
            if existing:
                return existing
        return None

    async def _find_existing_event_by_causation_id(
        self,
        *,
        run_id: str,
        event_type: RunEventType,
        causation_id: str | None,
    ) -> dict[str, Any] | None:
        if causation_id is None:
            return None
        return await self._run_events.find_one(
            {
                "run_id": run_id,
                "type": event_type.value,
                "causation_id": causation_id,
            }
        )

    def _event_payload_from_doc(self, event_doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event_doc.get("event_id"),
            "run_id": event_doc.get("run_id"),
            "room_id": event_doc.get("room_id"),
            "seq": event_doc.get("seq"),
            "type": event_doc.get("type"),
            "payload": event_doc.get("payload") or {},
            "ts": event_doc.get("ts"),
        }

    async def _repair_head_from_existing_event(self, event_doc: dict[str, Any]) -> None:
        run_id = str(event_doc.get("run_id") or "")
        if not run_id:
            return

        run_doc = await self._runs.find_one({"run_id": run_id})
        if not isinstance(run_doc, dict):
            return

        event_seq = int(event_doc.get("seq", 0))
        head_seq = int(run_doc.get("seq", 0))
        if event_seq <= head_seq:
            return

        event_type_str = str(event_doc.get("type", ""))
        payload = event_doc.get("payload") or {}
        terminal_type_map = {
            RunEventType.RUN_COMPLETED.value: RunState.COMPLETED,
            RunEventType.RUN_FAILED.value: RunState.FAILED,
            RunEventType.RUN_CANCELED.value: RunState.CANCELED,
        }
        active_type_map = {
            RunEventType.RUN_STARTED.value: RunState.PROCESSING,
            RunEventType.RUN_RESUMED.value: RunState.PROCESSING,
            RunEventType.RUN_AWAITING_INPUT.value: RunState.AWAITING_INPUT,
            RunEventType.RUN_CREATED.value: RunState.QUEUED,
        }
        resolved_state: RunState | None = terminal_type_map.get(
            event_type_str
        ) or active_type_map.get(event_type_str)
        if resolved_state is None:
            logger.warning(
                "project_run_state: unknown existing event type %s for run %s",
                event_type_str,
                run_id,
            )
            return

        current_state = RunState(run_doc.get("state", RunState.QUEUED.value))
        try:
            ensure_transition_allowed(current_state, resolved_state)
        except RunTransitionError:
            logger.warning(
                "project_run_state: repairing run %s through would-be-illegal "
                "transition %s→%s from existing event %s",
                run_id,
                current_state.value,
                resolved_state.value,
                event_type_str,
            )

        updates: dict[str, Any] = {
            "state": resolved_state.value,
            "seq": event_seq,
            "updated_at": event_doc.get("ts") or utcnow(),
        }
        if resolved_state == RunState.PROCESSING and not run_doc.get("started_at"):
            updates["started_at"] = event_doc.get("ts") or utcnow()
        if resolved_state in TERMINAL_RUN_STATES:
            updates["ended_at"] = event_doc.get("ts") or utcnow()
            updates["error_code"] = payload.get("error_code")
            updates["error_message"] = payload.get("error_message")

        await self._runs.update_one({"run_id": run_id}, {"$set": updates})


def run_event_sse_enabled() -> bool:
    return settings.feature_run_event_sse


run_command_handler = RunCommandHandler(
    run_repository=_UnboundRunRepository(),
    run_event_repository=_UnboundRunEventRepository(),
)
