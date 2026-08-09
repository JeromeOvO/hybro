"""Single writer for public `runs` / `run_events` projections."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from common.a2a_constants import SSEProcessingStatus
from common.config import settings
from common.observability.run_metrics import increment_counter
from common.protocols import RunEventRepository, RunRepository
from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.run_lifecycle_outcome import RunLifecycleWriteOutcome
from execution.run_reducer import RunTransitionError, ensure_transition_allowed
from models.run import (
    TERMINAL_RUN_STATES,
    Run,
    RunEvent,
    RunEventType,
    RunState,
    TerminalProjection,
)

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

    async def find(self, *args, **kwargs):  # pragma: no cover - guardrail
        raise RuntimeError(_UNBOUND_MESSAGE)

    async def find_one_and_update(self, *args, **kwargs):  # pragma: no cover
        raise RuntimeError(_UNBOUND_MESSAGE)

    async def update_one(self, *args, **kwargs):  # pragma: no cover - guardrail
        raise RuntimeError(_UNBOUND_MESSAGE)

    async def insert_one(self, *args, **kwargs):  # pragma: no cover - guardrail
        raise RuntimeError(_UNBOUND_MESSAGE)


class RunCommandHandler:
    """Append-only public run lifecycle and materialized head projection."""

    def __init__(
        self,
        *,
        run_repository: RunRepository,
        run_event_repository: RunEventRepository,
        room_files=None,
    ) -> None:
        self._runs = run_repository
        self._run_events = run_event_repository
        self._room_files = room_files

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
        terminal_projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Legacy payload-only API retained for non-terminal compatibility."""
        outcome = await self.write_processing_status(
            room_id,
            status,
            message_id,
            client_request_id=client_request_id,
            details=details,
            terminal_projection=terminal_projection,
        )
        return outcome.payload if outcome.status in {"accepted", "replayed"} else None

    async def write_processing_status(
        self,
        room_id: str,
        status: Any,
        message_id: str | None,
        *,
        client_request_id: str | None = None,
        details: str | None = None,
        terminal_projection: dict[str, Any] | None = None,
        _lease_held: bool = False,
    ) -> RunLifecycleWriteOutcome:
        """Persist status with an explicit accepted/conflict/error result."""
        if not room_id or not message_id:
            return RunLifecycleWriteOutcome.error(
                ValueError("room_id and message_id are required")
            )
        if self._room_files is not None and not _lease_held:
            try:
                async with self._room_files.write_lease(
                    room_id, "run-processing-status"
                ):
                    return await self.write_processing_status(
                        room_id,
                        status,
                        message_id,
                        client_request_id=client_request_id,
                        details=details,
                        terminal_projection=terminal_projection,
                        _lease_held=True,
                    )
            except Exception as exc:
                return RunLifecycleWriteOutcome.error(exc)

        status_value = self._normalize_status(status)
        run_id = self._run_id_for_message(message_id)

        try:
            payload = await self._persist_processing_status(
                room_id=room_id,
                run_id=run_id,
                message_id=message_id,
                status_value=status_value,
                client_request_id=client_request_id,
                details=details,
                terminal_projection=terminal_projection,
            )
            if payload is None:
                return RunLifecycleWriteOutcome.conflict()
            if payload.pop("_terminal_replay", False):
                return RunLifecycleWriteOutcome.replayed(payload)
            return RunLifecycleWriteOutcome.accepted(payload)
        except RunTransitionError:
            increment_counter(
                "run_transition_errors_total",
                source="processing_status",
                status=status_value,
            )
            logger.debug(
                "RunCommandHandler: skipped illegal transition (room=%s run=%s)",
                room_id,
                run_id,
            )
            return RunLifecycleWriteOutcome.conflict()
        except Exception as exc:
            outcome = RunLifecycleWriteOutcome.error(exc)
            logger.warning(
                "RunCommandHandler: failed to persist run status "
                "(room=%s run=%s status=%s error_class=%s error_fingerprint=%s)",
                room_id,
                run_id,
                status_value,
                outcome.error_class,
                outcome.error_fingerprint,
            )
            return outcome

    async def _persist_processing_status(
        self,
        *,
        room_id: str,
        run_id: str,
        message_id: str,
        status_value: str,
        client_request_id: str | None,
        details: str | None,
        terminal_projection: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
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
        terminal = {
            SSEProcessingStatus.COMPLETED: (RunState.COMPLETED, None),
            SSEProcessingStatus.CANCELED: (RunState.CANCELED, "CANCELED"),
            SSEProcessingStatus.FAILED: (RunState.FAILED, "FAILED"),
            SSEProcessingStatus.REJECTED: (RunState.FAILED, "REJECTED"),
            SSEProcessingStatus.RATE_LIMITED: (RunState.FAILED, "RATE_LIMITED"),
            SSEProcessingStatus.ERROR: (RunState.FAILED, "ERROR"),
        }.get(status_value)
        if terminal is None:
            return None
        terminal_state, error_code = terminal
        return await self._record_terminal(
            room_id=room_id,
            run_id=run_id,
            trigger_message_id=message_id,
            client_request_id=client_request_id,
            terminal_state=terminal_state,
            error_code=error_code,
            error_message=None if terminal_state == RunState.COMPLETED else details,
            terminal_projection=terminal_projection,
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
        _lease_held: bool = False,
    ) -> dict[str, Any] | None:
        if not isinstance(target_state, RunState):
            raise TypeError("target_state must be a public RunState")
        if self._room_files is not None and not _lease_held:
            async with self._room_files.write_lease(room_id, "run-projection"):
                return await self.project_run_state(
                    room_id=room_id,
                    run_id=run_id,
                    trigger_message_id=trigger_message_id,
                    target_state=target_state,
                    terminal_reason=terminal_reason,
                    causation_id=causation_id,
                    client_request_id=client_request_id,
                    terminal_summary=terminal_summary,
                    _lease_held=True,
                )

        state = target_state
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
                terminal_summary=terminal_summary,
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
                terminal_summary=terminal_summary,
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
                terminal_summary=terminal_summary,
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

        resolved_state: RunState | None = terminal_type_map.get(
            event_type_str
        ) or active_type_map.get(event_type_str)
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
            updates["terminal_summary"] = payload.get("terminal_summary")
            updates["terminal_projection"] = latest_event.get("terminal_projection")

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
        self,
        room_id: str,
        run_id: str,
        *,
        stale_minutes: int,
        _lease_held: bool = False,
    ) -> dict[str, Any] | None:
        """Watchdog: fail a stuck non-terminal run.

        Before appending a new RUN_FAILED event, checks run_events for any
        events ahead of the runs head.  If found (diverged head), projects
        the head forward instead — avoiding the DuplicateKeyError loop that
        previously left the run stuck forever.
        """
        if self._room_files is not None and not _lease_held:
            async with self._room_files.write_lease(room_id, "run-watchdog"):
                return await self.append_run_timeout_failure(
                    room_id,
                    run_id,
                    stale_minutes=stale_minutes,
                    _lease_held=True,
                )

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
            if current_state == RunState.AWAITING_INPUT and causation_id is None:
                return None
            event_type = RunEventType.RUN_AWAITING_INPUT
            next_state = RunState.AWAITING_INPUT
        else:
            if current_state == RunState.PROCESSING and causation_id is None:
                return None
            event_type = (
                RunEventType.RUN_RESUMED
                if current_state in {RunState.AWAITING_INPUT, RunState.PROCESSING}
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
        terminal_summary: dict[str, Any] | None = None,
        terminal_projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        run_doc = await self._ensure_run_exists(
            room_id=room_id,
            run_id=run_id,
            trigger_message_id=trigger_message_id,
            client_request_id=client_request_id,
        )

        current_state = RunState(run_doc.get("state", RunState.QUEUED))
        terminal_event_map = {
            RunState.COMPLETED: RunEventType.RUN_COMPLETED,
            RunState.FAILED: RunEventType.RUN_FAILED,
            RunState.CANCELED: RunEventType.RUN_CANCELED,
        }
        if current_state in TERMINAL_RUN_STATES:
            if current_state != terminal_state:
                return None
            existing = await self._run_events.find_one(
                {
                    "run_id": run_id,
                    "type": terminal_event_map[terminal_state].value,
                },
                sort=[("seq", -1)],
            )
            if existing is None:
                return None
            await self._repair_head_from_existing_event(existing)
            replay = self._event_payload_from_doc(existing)
            replay["_terminal_replay"] = True
            return replay

        payload = {
            "error_code": error_code,
            "error_message": error_message,
        }
        if terminal_projection is None:
            terminal_projection = self._default_terminal_projection(
                terminal_state=terminal_state,
                trigger_message_id=trigger_message_id,
                client_request_id=client_request_id,
                error_code=error_code,
                error_message=error_message,
            )
        if terminal_summary is not None:
            payload["terminal_summary"] = terminal_summary
        return await self._append_event_and_project(
            run_doc=run_doc,
            event_type=terminal_event_map[terminal_state],
            next_state=terminal_state,
            payload=payload,
            causation_id=causation_id,
            terminal_projection=terminal_projection,
        )

    @staticmethod
    def _default_terminal_projection(
        *,
        terminal_state: RunState,
        trigger_message_id: str,
        client_request_id: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> dict[str, Any]:
        status = (
            "completed"
            if terminal_state == RunState.COMPLETED
            else "canceled"
            if terminal_state == RunState.CANCELED
            else str(error_code or "failed").lower()
        )
        if (
            status
            not in {
                "failed",
                "rejected",
                "rate_limited",
                "error",
            }
            and terminal_state == RunState.FAILED
        ):
            status = "failed"
        return {
            "version": 1,
            "canonical_status": status,
            "frontend_message_id": trigger_message_id,
            "lifecycle_message_id": trigger_message_id,
            "descendant_cleanup_root_id": (
                trigger_message_id if terminal_state != RunState.COMPLETED else None
            ),
            "client_request_id": client_request_id,
            "details": {"message": error_message} if error_message else None,
            "pending": True,
            "steps": {
                "run_event_sse": {"state": "pending"},
                "processing_sse": {"state": "pending"},
                **(
                    {"descendant_cleanup": {"state": "pending"}}
                    if terminal_state != RunState.COMPLETED
                    else {}
                ),
            },
        }

    async def _append_event_and_project(
        self,
        *,
        run_doc: dict,
        event_type: RunEventType,
        next_state: RunState,
        payload: dict,
        causation_id: str | None = None,
        terminal_projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = utcnow()
        current_seq = int(run_doc.get("seq", 0))
        next_seq = current_seq + 1
        run_id = str(run_doc["run_id"])
        room_id = str(run_doc["room_id"])
        current_state = RunState(run_doc.get("state", RunState.QUEUED))

        try:
            if not (current_state == next_state and next_state in TERMINAL_RUN_STATES):
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

        projection_model = self._terminal_projection_model(terminal_projection)
        event = RunEvent(
            run_id=run_id,
            room_id=room_id,
            seq=next_seq,
            type=event_type,
            payload=payload,
            causation_id=causation_id,
            terminal_projection=projection_model,
            ts=now,
        )
        if event.terminal_projection is not None:
            event.terminal_projection.event_id = event.event_id
            event.terminal_projection.delivery_id = (
                event.terminal_projection.delivery_id
                or f"terminal:{event.event_id}:processing"
            )
        dumped = event.model_dump(mode="json")
        try:
            await self._run_events.insert_one(dumped)
        except DuplicateKeyError:
            return await self._resolve_duplicate_event(
                run_id=run_id,
                seq=next_seq,
                event_type=event_type,
                next_state=next_state,
                causation_id=causation_id,
            )

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
            updates["terminal_summary"] = payload.get("terminal_summary")
            updates["terminal_projection"] = dumped.get("terminal_projection")

        await self._project_or_repair_appended_event(
            run_id=run_id,
            updates=updates,
            event_doc=dumped,
        )
        increment_counter(
            "run_event_append_total",
            event_type=event_type.value,
            to_state=next_state.value,
        )

        result = {
            "event_id": dumped.get("event_id"),
            "run_id": run_id,
            "room_id": room_id,
            "seq": next_seq,
            "type": event_type.value,
            "payload": payload,
            "ts": dumped.get("ts"),
        }
        if dumped.get("terminal_projection") is not None:
            result["terminal_projection"] = dumped["terminal_projection"]
        return result

    @staticmethod
    def _terminal_projection_model(
        projection: dict[str, Any] | None,
    ) -> TerminalProjection | None:
        if projection is None:
            return None
        return TerminalProjection.model_validate(dict(projection))

    async def _resolve_duplicate_event(
        self,
        *,
        run_id: str,
        seq: int,
        event_type: RunEventType,
        next_state: RunState,
        causation_id: str | None,
    ) -> dict[str, Any] | None:
        existing = await self._find_existing_event_by_causation_id(
            run_id=run_id,
            event_type=event_type,
            causation_id=causation_id,
        )
        if existing is None:
            canonical = await self._run_events.find_one({"run_id": run_id, "seq": seq})
            if canonical is not None:
                if canonical.get("type") != event_type.value:
                    await self._repair_head_from_existing_event(canonical)
                    return None
                existing = canonical
        if existing is None:
            return None
        await self._repair_head_from_existing_event(existing)
        replay = self._event_payload_from_doc(existing)
        if next_state in TERMINAL_RUN_STATES:
            replay["_terminal_replay"] = True
        return replay

    async def _project_or_repair_appended_event(
        self,
        *,
        run_id: str,
        updates: dict[str, Any],
        event_doc: dict[str, Any],
    ) -> None:
        try:
            projected = await self._runs.update_one(
                {"run_id": run_id},
                {"$set": updates},
            )
            if projected is not False:
                return
            projection_error = RuntimeError("run head projection was not acknowledged")
        except Exception as exc:
            projection_error = exc

        # The append is durable. Repair from that exact event before reporting
        # success; otherwise the checked API returns ERROR for retry/healing.
        if not await self._repair_head_from_existing_event(event_doc):
            raise RuntimeError(
                "run head repair was not acknowledged"
            ) from projection_error

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
        result = {
            "event_id": event_doc.get("event_id"),
            "run_id": event_doc.get("run_id"),
            "room_id": event_doc.get("room_id"),
            "seq": event_doc.get("seq"),
            "type": event_doc.get("type"),
            "payload": event_doc.get("payload") or {},
            "ts": event_doc.get("ts"),
        }
        if event_doc.get("terminal_projection") is not None:
            result["terminal_projection"] = event_doc["terminal_projection"]
        return result

    async def _repair_head_from_existing_event(self, event_doc: dict[str, Any]) -> bool:
        run_id = str(event_doc.get("run_id") or "")
        if not run_id:
            return False

        run_doc = await self._runs.find_one({"run_id": run_id})
        if not isinstance(run_doc, dict):
            return False

        event_seq = int(event_doc.get("seq", 0))
        head_seq = int(run_doc.get("seq", 0))
        if event_seq <= head_seq:
            return True

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
            return False

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
            updates["terminal_summary"] = payload.get("terminal_summary")
            updates["terminal_projection"] = event_doc.get("terminal_projection")

        return (
            await self._runs.update_one({"run_id": run_id}, {"$set": updates})
        ) is not False

    async def list_incomplete_terminal_projections(
        self, limit: int = 100
    ) -> list[dict[str, Any]]:
        now = utcnow().isoformat()
        return await self._run_events.find(
            {
                "terminal_projection.version": 1,
                "terminal_projection.pending": True,
                "terminal_projection.next_attempt_at": {"$lte": now},
            },
            sort=[("terminal_projection.next_attempt_at", 1), ("ts", 1)],
            limit=limit,
        )

    async def claim_terminal_projection_step(
        self,
        event_id: str,
        step: str,
        *,
        lease_seconds: int = 300,
    ) -> tuple[str, dict[str, Any]] | None:
        token = uuid4().hex
        now_dt = utcnow()
        now = now_dt.isoformat()
        claim_expires_at = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        path = f"terminal_projection.steps.{step}"
        doc = await self._run_events.find_one_and_update(
            {
                "event_id": event_id,
                "terminal_projection.version": 1,
                "$or": [
                    {
                        f"{path}.state": "pending",
                        "$or": [
                            {f"{path}.next_attempt_at": {"$exists": False}},
                            {f"{path}.next_attempt_at": {"$lte": now}},
                        ],
                    },
                    {
                        f"{path}.state": "running",
                        f"{path}.claim_expires_at": {"$lte": now},
                    },
                ],
            },
            {
                "$set": {
                    f"{path}.state": "running",
                    f"{path}.claim_token": token,
                    f"{path}.claim_expires_at": claim_expires_at,
                    f"{path}.last_attempt_at": now,
                    "terminal_projection.updated_at": now,
                },
                "$inc": {f"{path}.attempts": 1},
            },
        )
        return (token, doc) if doc is not None else None

    async def complete_terminal_projection_step(
        self, event_id: str, step: str, claim_token: str
    ) -> bool:
        now = utcnow().isoformat()
        path = f"terminal_projection.steps.{step}"
        return await self._run_events.update_one(
            {
                "event_id": event_id,
                f"{path}.state": "running",
                f"{path}.claim_token": claim_token,
            },
            {
                "$set": {
                    f"{path}.state": "completed",
                    f"{path}.completed_at": now,
                    f"{path}.claim_token": None,
                    f"{path}.claim_expires_at": None,
                    f"{path}.last_error": None,
                    f"{path}.next_attempt_at": None,
                    "terminal_projection.updated_at": now,
                }
            },
        )

    async def release_terminal_projection_step(
        self,
        event_id: str,
        step: str,
        claim_token: str,
        error: BaseException,
        *,
        retryable: bool = True,
        delay_seconds: int = 1,
    ) -> bool:
        now_dt = utcnow()
        now = now_dt.isoformat()
        path = f"terminal_projection.steps.{step}"
        state = "pending" if retryable else "blocked"
        retry_at = (
            (now_dt + timedelta(seconds=delay_seconds)).isoformat()
            if retryable
            else None
        )
        return await self._run_events.update_one(
            {
                "event_id": event_id,
                f"{path}.state": "running",
                f"{path}.claim_token": claim_token,
            },
            {
                "$set": {
                    f"{path}.state": state,
                    f"{path}.claim_token": None,
                    f"{path}.claim_expires_at": None,
                    f"{path}.next_attempt_at": retry_at,
                    f"{path}.blocked_at": None if retryable else now,
                    f"{path}.last_error": f"{type(error).__name__}: {error}"[:512],
                    "terminal_projection.updated_at": now,
                }
            },
        )

    async def block_terminal_projection_step(
        self,
        event_id: str,
        step: str,
        error: BaseException,
    ) -> bool:
        now = utcnow().isoformat()
        path = f"terminal_projection.steps.{step}"
        return await self._run_events.update_one(
            {"event_id": event_id, "terminal_projection.version": 1},
            {
                "$set": {
                    path: {
                        "state": "blocked",
                        "claim_token": None,
                        "claim_expires_at": None,
                        "next_attempt_at": None,
                        "blocked_at": now,
                        "last_error": f"{type(error).__name__}: {error}"[:512],
                    },
                    "terminal_projection.updated_at": now,
                }
            },
        )

    async def refresh_terminal_projection_schedule(self, event_id: str) -> bool:
        doc = await self._run_events.find_one({"event_id": event_id})
        if not isinstance(doc, dict):
            return False
        projection = doc.get("terminal_projection") or {}
        steps = projection.get("steps") or {}
        if not isinstance(steps, dict):
            steps = {}
        due_times = []
        now = utcnow().isoformat()
        for step in steps.values():
            if not isinstance(step, dict):
                continue
            state = step.get("state")
            if state == "pending":
                due_times.append(step.get("next_attempt_at") or now)
            elif state == "running":
                due_times.append(step.get("claim_expires_at") or now)
        pending = bool(due_times)
        return await self._run_events.update_one(
            {"event_id": event_id, "terminal_projection.version": 1},
            {
                "$set": {
                    "terminal_projection.pending": pending,
                    "terminal_projection.next_attempt_at": (
                        min(due_times) if due_times else None
                    ),
                    "terminal_projection.updated_at": now,
                }
            },
        )


def run_event_sse_enabled() -> bool:
    return settings.feature_run_event_sse


run_command_handler = RunCommandHandler(
    run_repository=_UnboundRunRepository(),
    run_event_repository=_UnboundRunEventRepository(),
)
