from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from common.dto import (
    DeliveryEmitStatus,
    DeliveryEvent,
    ProcessingStatusEvent,
    RunEventNotification,
)
from common.utils.logger import get_logger
from execution.events import run_event_notification_from_payload
from execution.state.task_status_mapping import system_task_state_from_runtime_status

logger = get_logger(__name__)

_STEP_ORDER = (
    "descendant_cleanup",
    "run_event_sse",
    "processing_sse",
    "system_task",
    "system_task_delivery",
    "completion_metadata",
    "turn_event",
)

# The SSE emission steps are the frames' emitters: they can only run in the
# phase-2 pass once every OTHER durable side-effect step is completed or
# blocked (Room Stream Snapshot plan §4 client rule 4).
_SSE_EMIT_STEPS = frozenset({"run_event_sse", "processing_sse"})


class RunEventProjectionSettlementReader:
    """Publisher-side settlement reader over the private ``run_events`` log.

    Defense-in-depth for the terminal gating (Room Stream Snapshot plan §4/
    §5): answers whether the fact behind a terminal frame still has durable
    side-effect steps in ``{pending, running}``. The primary gate remains the
    two-phase finalizer above; a missing fact document is treated as settled
    so the reader can never withhold a frame with no recovery path.
    """

    def __init__(self, run_event_repository) -> None:
        self._run_events = run_event_repository

    async def is_terminal_settled(self, event: DeliveryEvent) -> bool:
        event_id = self._fact_event_id(event)
        if event_id is None:
            return True
        doc = await self._run_events.find_one({"event_id": event_id})
        if not doc:
            return True
        projection = doc.get("terminal_projection")
        if not isinstance(projection, dict):
            return True
        steps = projection.get("steps")
        if not isinstance(steps, dict):
            return True
        for name, value in steps.items():
            if name in _SSE_EMIT_STEPS:
                continue
            state = value.get("state") if isinstance(value, dict) else None
            if state in {"pending", "running"}:
                return False
        return True

    @staticmethod
    def _fact_event_id(event: DeliveryEvent) -> str | None:
        if isinstance(event, RunEventNotification):
            event_id = str(event.event_id or "").strip()
            return event_id or None
        delivery_id = getattr(event, "delivery_id", None)
        if not delivery_id:
            return None
        parts = str(delivery_id).split(":")
        if len(parts) >= 3 and parts[0] == "terminal" and parts[1]:
            return parts[1]
        return None


class ProjectionBlockedError(RuntimeError):
    """A durable opposing/missing target that retries cannot repair."""


class TerminalProjectionFinalizer:
    """Idempotently project one durable terminal run-event fact.

    Every effect has its own Mongo lease. Failures release only that effect back
    to ``pending``; they never alter or obscure the durable root winner.
    """

    def __init__(
        self,
        *,
        lifecycle,
        event_publisher,
        message_store,
        delivery,
        run_event_enabled: Callable[[], bool],
        turn_event_appender: Callable[[], Any | None] | None = None,
        head_healer: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._event_publisher = event_publisher
        self._message_store = message_store
        self._delivery = delivery
        self._run_event_enabled = run_event_enabled
        self._turn_event_appender = turn_event_appender or (lambda: None)
        self._head_healer = head_healer

    async def recover_pending(self, *, limit: int = 100) -> int:
        facts = await self._lifecycle.list_incomplete_terminal_projections(limit)
        recovered = 0
        for fact in facts:
            try:
                run_id = str(fact.get("run_id") or "") if isinstance(fact, dict) else ""
                if run_id and self._head_healer is not None:
                    try:
                        await self._head_healer(run_id)
                    except Exception:
                        logger.warning(
                            "terminal projection head healing failed run_id=%s",
                            run_id,
                            exc_info=True,
                        )
                if await self.finalize(fact):
                    recovered += 1
            except Exception:
                logger.warning(
                    "terminal projection fact recovery failed; continuing batch",
                    exc_info=True,
                )
        return recovered

    async def finalize(  # noqa: C901
        self, fact: dict[str, Any]
    ) -> bool:
        resolved = self._resolve_fact(fact)
        if resolved is None:
            return False
        event_id, projection = resolved

        all_complete = True
        projection_steps = await self._sanitize_projection_steps(
            event_id, projection.get("steps") or {}
        )
        if projection_steps is None:
            await self._lifecycle.refresh_terminal_projection_schedule(event_id)
            return False
        completed_steps = {
            name
            for name, value in projection_steps.items()
            if value.get("state") == "completed"
        }
        blocked_steps = {
            name
            for name, value in projection_steps.items()
            if value.get("state") == "blocked"
        }

        # Phase 1 — durable side-effect steps. The SSE emission steps are
        # skipped here; they run in phase 2 only once every other step has
        # settled (completed or blocked).
        for step in _STEP_ORDER:
            if step in _SSE_EMIT_STEPS:
                continue
            if step not in projection_steps:
                continue
            step_state = (projection_steps.get(step) or {}).get("state")
            if step_state in {"completed", "blocked"}:
                continue
            claim = await self._lifecycle.claim_terminal_projection_step(event_id, step)
            if claim is None:
                all_complete = False
                continue
            token, claimed_fact = claim
            claimed_projection = claimed_fact.get("terminal_projection") or projection
            try:
                dependency_error = self._step_dependency_error(
                    step, completed_steps, blocked_steps
                )
                if dependency_error is not None:
                    raise dependency_error
                await self._execute(step, claimed_fact, claimed_projection)
                completed = await self._lifecycle.complete_terminal_projection_step(
                    event_id, step, token
                )
                if completed:
                    completed_steps.add(step)
                all_complete = bool(completed) and all_complete
            except Exception as exc:
                all_complete = False
                if await self._release_failed_step(
                    event_id,
                    step,
                    token,
                    claimed_projection,
                    exc,
                ):
                    blocked_steps.add(step)

        # Phase 2 — SSE emission, gated on full durable settlement. A step
        # that is still pending/running (retry scheduled, or a concurrent
        # claim) defers the SSE frames to a later finalize pass.
        durable_settled = all(
            step in completed_steps or step in blocked_steps
            for step in projection_steps
            if step not in _SSE_EMIT_STEPS
        )
        if durable_settled:
            for step in _STEP_ORDER:
                if step not in _SSE_EMIT_STEPS or step not in projection_steps:
                    continue
                step_state = (projection_steps.get(step) or {}).get("state")
                if step_state in {"completed", "blocked"}:
                    continue
                claim = await self._lifecycle.claim_terminal_projection_step(
                    event_id, step
                )
                if claim is None:
                    all_complete = False
                    continue
                token, claimed_fact = claim
                claimed_projection = (
                    claimed_fact.get("terminal_projection") or projection
                )
                try:
                    await self._execute(step, claimed_fact, claimed_projection)
                    completed = await self._lifecycle.complete_terminal_projection_step(
                        event_id, step, token
                    )
                    if completed:
                        completed_steps.add(step)
                    all_complete = bool(completed) and all_complete
                except Exception as exc:
                    all_complete = False
                    if await self._release_failed_step(
                        event_id,
                        step,
                        token,
                        claimed_projection,
                        exc,
                    ):
                        blocked_steps.add(step)
        else:
            all_complete = False
        await self._lifecycle.refresh_terminal_projection_schedule(event_id)
        return all_complete

    async def _sanitize_projection_steps(self, event_id, steps):
        if not isinstance(steps, dict):
            return None
        sanitized = {}
        for name, value in steps.items():
            error = None
            if not isinstance(value, dict):
                error = TypeError("terminal projection step must be an object")
            elif name not in _STEP_ORDER:
                error = ProjectionBlockedError(
                    f"unknown terminal projection step: {name}"
                )
            else:
                sanitized[name] = value
            if error is not None:
                await self._lifecycle.block_terminal_projection_step(
                    event_id, name, error
                )
        return sanitized

    @staticmethod
    def _resolve_fact(fact):
        if not isinstance(fact, dict):
            return None
        projection = fact.get("terminal_projection")
        if not isinstance(projection, dict) or projection.get("version") != 1:
            return None
        event_id = str(fact.get("event_id") or projection.get("event_id") or "")
        return (event_id, projection) if event_id else None

    @staticmethod
    def _step_dependency_error(step, completed_steps, blocked_steps):
        if step != "system_task_delivery":
            return None
        if "system_task" in blocked_steps:
            return ProjectionBlockedError(
                "system task delivery blocked by task-state conflict"
            )
        if "system_task" not in completed_steps:
            return RuntimeError(
                "system task delivery waiting for task-state projection"
            )
        return None

    async def _release_failed_step(
        self,
        event_id,
        step,
        token,
        projection,
        exc,
    ) -> bool:
        attempts = (
            int(((projection.get("steps") or {}).get(step) or {}).get("attempts", 0))
            + 1
        )
        blocked = isinstance(exc, ProjectionBlockedError)
        await self._lifecycle.release_terminal_projection_step(
            event_id,
            step,
            token,
            exc,
            retryable=not blocked,
            delay_seconds=min(300, 2 ** min(attempts, 8)),
        )
        logger.warning(
            "terminal projection step failed event_id=%s step=%s blocked=%s",
            event_id,
            step,
            blocked,
            exc_info=True,
        )
        return blocked

    async def _execute(
        self,
        step: str,
        fact: dict[str, Any],
        projection: dict[str, Any],
    ) -> None:
        handlers = {
            "descendant_cleanup": self._project_descendant_cleanup,
            "run_event_sse": self._project_run_event_sse,
            "processing_sse": self._project_processing_sse,
            "system_task": self._project_system_task,
            "system_task_delivery": self._project_system_task_delivery,
            "completion_metadata": self._project_completion_metadata,
            "turn_event": self._project_turn_event,
        }
        try:
            handler = handlers[step]
        except KeyError as exc:
            raise ValueError(f"unknown terminal projection step: {step}") from exc
        await handler(fact, projection)

    async def _project_descendant_cleanup(self, fact, projection) -> None:
        root_id = projection.get("descendant_cleanup_root_id")
        if not root_id:
            return
        canonical_status = str(projection.get("canonical_status") or "failed")
        target_state = (
            "canceled"
            if canonical_status == "canceled"
            else "rejected"
            if canonical_status == "rejected"
            else "failed"
        )
        event_id = str(fact.get("event_id") or projection.get("event_id") or "")
        system_message_id = projection.get("system_message_id")
        child_ids = await self._message_store.project_descendant_terminal_state(
            str(root_id),
            event_id=event_id,
            target_state=target_state,
            exclude_message_ids=(
                [str(system_message_id)] if system_message_id else None
            ),
        )
        for child_id in child_ids:
            delivered = await self._delivery.send_task_update(
                room_id=str(fact.get("room_id") or ""),
                message_id=child_id,
                status=target_state,
                delivery_id=f"terminal:{event_id}:child:{child_id}",
                client_request_id=projection.get("client_request_id"),
            )
            if delivered is False:
                raise RuntimeError(
                    f"descendant task delivery was not accepted: {child_id}"
                )

    async def _project_run_event_sse(self, fact, projection) -> None:
        if not self._run_event_enabled():
            return
        await self._checked_emit(
            run_event_notification_from_payload(
                room_id=str(fact.get("room_id") or ""),
                payload={
                    "event_id": fact.get("event_id"),
                    "run_id": fact.get("run_id"),
                    "seq": fact.get("seq"),
                    "type": fact.get("type"),
                    "payload": fact.get("payload") or {},
                },
                correlation_id=projection.get("client_request_id"),
            )
        )

    async def _project_processing_sse(self, fact, projection) -> None:
        await self._checked_emit(
            ProcessingStatusEvent(
                room_id=str(fact.get("room_id") or ""),
                message_id=str(projection["frontend_message_id"]),
                status=str(projection["canonical_status"]),
                related_message_id=projection.get("lifecycle_message_id"),
                details=projection.get("details"),
                client_request_id=projection.get("client_request_id"),
                agents=projection.get("agents"),
                delivery_id=projection.get("delivery_id"),
            )
        )

    async def _project_system_task(self, fact, projection) -> None:
        message_id = projection.get("system_message_id")
        runtime_status = projection.get("system_task_status")
        if not message_id or not runtime_status:
            return
        task_state = system_task_state_from_runtime_status(runtime_status)
        target = str(getattr(task_state, "value", task_state))
        event_id = str(fact.get("event_id") or projection.get("event_id") or "")
        outcome = await self._message_store.set_system_task_terminal_state(
            message_id, target, event_id=event_id
        )
        if outcome == "conflict":
            raise ProjectionBlockedError(
                f"system task projection conflict: {message_id} -> {target}"
            )
        if outcome == "missing":
            attempts = int(
                ((projection.get("steps") or {}).get("system_task") or {}).get(
                    "attempts", 0
                )
            )
            if attempts >= 2:
                raise ProjectionBlockedError(
                    f"system task projection missing: {message_id}"
                )
            raise RuntimeError(f"system task projection missing: {message_id}")
        if outcome not in {"updated", "already"}:
            raise RuntimeError(f"unknown system task projection outcome: {outcome}")

    async def _project_system_task_delivery(self, fact, projection) -> None:
        message_id = projection.get("system_message_id")
        runtime_status = projection.get("system_task_status")
        if not message_id or not runtime_status:
            return
        delivery_id = f"terminal:{fact.get('event_id')}:system-task"
        try:
            delivered = await self._delivery.send_task_update(
                room_id=str(fact.get("room_id") or ""),
                message_id=message_id,
                status=runtime_status,
                delivery_id=delivery_id,
                client_request_id=projection.get("client_request_id"),
            )
        except TypeError as exc:
            if "delivery_id" not in str(exc):
                raise
            delivered = await self._delivery.send_task_update(
                room_id=str(fact.get("room_id") or ""),
                message_id=message_id,
                status=runtime_status,
                client_request_id=projection.get("client_request_id"),
            )
        if delivered is False:
            raise RuntimeError("system task delivery was not accepted")

    async def _project_completion_metadata(self, _fact, projection) -> None:
        completion_kind = projection.get("completion_kind")
        if not completion_kind:
            return
        outcome = await self._message_store.set_turn_completion_kind(
            str(projection["lifecycle_message_id"]), completion_kind
        )
        if outcome in {"missing", "conflict"}:
            raise ProjectionBlockedError(
                "completion metadata projection "
                f"{outcome}: {projection['lifecycle_message_id']}"
            )
        if outcome not in {"updated", "already"}:
            raise RuntimeError(f"unknown completion metadata outcome: {outcome}")

    async def _project_turn_event(self, fact, projection) -> None:
        event_type = projection.get("turn_event_type")
        if not event_type:
            return
        appender = self._turn_event_appender()
        if appender is None:
            raise RuntimeError("turn event appender is unavailable")
        append = appender.append
        kwargs: dict[str, Any] = {}
        try:
            if "idempotency_key" in inspect.signature(append).parameters:
                kwargs["idempotency_key"] = f"terminal:{fact.get('event_id')}:turn"
        except (TypeError, ValueError):
            pass
        await append(
            str(fact.get("room_id") or ""),
            str(projection["lifecycle_message_id"]),
            event_type,
            projection.get("turn_event_payload") or {},
            **kwargs,
        )

    async def _checked_emit(self, event) -> None:
        checked = getattr(self._event_publisher, "emit_checked", None)
        if callable(checked):
            outcome = await checked(event)
            if outcome in {
                DeliveryEmitStatus.DELIVERED,
                DeliveryEmitStatus.ALREADY_DELIVERED,
                DeliveryEmitStatus.DELIVERED.value,
                DeliveryEmitStatus.ALREADY_DELIVERED.value,
            }:
                return
            raise RuntimeError("terminal delivery was not accepted")
        if not await self._event_publisher.emit(event):
            raise RuntimeError("terminal delivery returned False")


__all__ = [
    "ProjectionBlockedError",
    "RunEventProjectionSettlementReader",
    "TerminalProjectionFinalizer",
]
