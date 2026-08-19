"""
Stale Task Checker Background Job

This module provides a background job that:
1. Polls agents for tasks that haven't received webhook updates
2. Auto-fails tasks that have been pending too long
3. Handles tasks that were never acknowledged by agents
4. Recovers orphaned agent messages that were never processed
5. Cleans up stuck room processing status
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from a2a_adapter.remote_task import fetch_remote_task
from a2a_adapter.task_status import build_failed_text_task
from common.a2a_constants import (
    INTERACTIVE_STATES,
    NON_TERMINAL_STATES,
    CommonTaskState,
    is_terminal_state,
)
from common.a2a_task_projection import public_persisted_task_data
from common.config import settings
from common.observability import safe_exception_metadata, traced_create_task
from common.types import Task
from common.utils.a2a_helpers import (
    artifacts_to_dicts,
    extract_text_from_artifacts,
    materialize_artifacts,
)
from common.utils.logger import get_logger
from common.utils.time import ensure_utc, utcnow
from execution.cancellation.ports import CancellationReconciliationPort
from execution.dispatch.agent_event import AgentEvent
from execution.orchestration.run_store import OrchestrationStoreConflict
from jobs.constants import STALE_TASK_CHECKER
from models.orchestration import (
    TERMINAL_ORCHESTRATION_STATUSES,
    OrchestrationEventType,
    OrchestrationRunEvent,
)
from models.request import OrchestrationRequest
from models.room import RoomAgentMessage

logger = get_logger(__name__)

MAX_CONCURRENT_RECOVERIES = 5


class LeaderGate(Protocol):
    async def try_acquire(self, name: str, ttl_seconds: int) -> bool: ...

    async def release(self, name: str) -> None: ...


class _UnboundDependency:
    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str):
        async def _missing(*_args, **_kwargs):
            raise RuntimeError(
                f"Stale task checker dependency {self._name} is not bound"
            )

        return _missing


store: Any = _UnboundDependency("store")
a2a_service: Any = _UnboundDependency("a2a_service")


async def notify_task_update(**_kwargs) -> Any:
    raise RuntimeError("Stale task checker notification dependency is not bound")


def increment_counter(_name: str) -> None:
    raise RuntimeError("Stale task checker metrics dependency is not bound")


@dataclass(frozen=True)
class StaleRecoveryDeps:
    schedule_recovery: Callable[..., asyncio.Task]


@dataclass(frozen=True)
class StaleOrchestrationRunRecoveryDeps:
    orchestration_run_store: Any


@dataclass(frozen=True)
class StaleCancellationReconciliationDeps:
    reconciliation: CancellationReconciliationPort


@dataclass(frozen=True)
class StaleRunWatchdogEventDeps:
    append_run_timeout_failure: Callable[..., Awaitable[dict[str, Any] | None]]
    emit_run_event: Callable[..., Awaitable[None]]
    emit_processing_status: Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class StaleTerminalProjectionDeps:
    recover_pending: Callable[..., Awaitable[int]]


@dataclass(frozen=True)
class StaleHITLDeps:
    recover_stale_processing: Callable[[], Awaitable[Any]]
    cancel_requests_for_message: Callable[..., Awaitable[Any]]
    reconcile_lifecycle: Callable[[], Awaitable[Any]] | None = None


@dataclass(frozen=True)
class StaleTaskCheckerDeps:
    store: Any
    rooms_collection: Any
    notify_task_update: Callable[..., Awaitable[Any]]
    increment_counter: Callable[[str], Any]
    a2a_service: Any


async def _best_effort_materialize_artifacts(
    artifacts: list,
    *,
    room_id: str,
    message_id: str,
) -> None:
    try:
        await materialize_artifacts(
            artifacts,
            room_id=room_id,
            message_id=message_id,
        )
    except Exception:
        logger.warning(
            "Stale task checker artifact conversion failed for message %s; "
            "continuing with public projection",
            message_id,
            exc_info=True,
        )


class StaleTaskChecker:
    """
    Background job that checks for stale and expired tasks.

    This provides a fallback mechanism when webhooks fail:
    1. Stale tasks: Poll agent for current status
    2. Expired tasks: Auto-fail tasks that have been pending too long
    3. Never-acknowledged tasks: Fail tasks where agent never responded
    4. Orphaned messages: Recover agent messages that were never processed
    5. Legacy room mirror: null rooms.processing_message_id when the room has
       no non-terminal runs (runs are source of truth)
    """

    def __init__(
        self,
        stale_check_minutes: int = 10,
        task_expiry_hours: int = 4,
        pending_task_warning_hours: int = 1,
        check_interval_minutes: int = 5,
        orphan_threshold_minutes: int = 2,
        processing_status_expiry_minutes: int = 30,
    ):
        """
        Initialize the stale task checker.

        Args:
            stale_check_minutes: Poll tasks not updated in this time
            task_expiry_hours: Auto-fail tasks older than this
            pending_task_warning_hours: Warn (log) after this time
            check_interval_minutes: How often to run the check
            orphan_threshold_minutes: Recover orphaned messages older than this
            processing_status_expiry_minutes: Reserved (legacy cleanup no longer uses age threshold)
        """
        self.stale_check_minutes = stale_check_minutes
        self.task_expiry_hours = task_expiry_hours
        self.pending_task_warning_hours = pending_task_warning_hours
        self.check_interval_minutes = check_interval_minutes
        self.orphan_threshold_minutes = orphan_threshold_minutes
        self.processing_status_expiry_minutes = processing_status_expiry_minutes
        self._running = False
        self._task: asyncio.Task | None = None
        self._recovery_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RECOVERIES)
        self._leader: LeaderGate | None = None
        self._execution_recovery_deps: StaleRecoveryDeps | None = None
        self._orchestration_run_recovery_deps: (
            StaleOrchestrationRunRecoveryDeps | None
        ) = None
        self._cancellation_reconciliation_deps: (
            StaleCancellationReconciliationDeps | None
        ) = None
        self._watchdog_event_deps: StaleRunWatchdogEventDeps | None = None
        self._terminal_projection_deps: StaleTerminalProjectionDeps | None = None
        self._hitl_deps: StaleHITLDeps | None = None
        self._runtime_deps: StaleTaskCheckerDeps | None = None
        self._terminal_event_handler: Callable[[AgentEvent], Awaitable[None]] | None = (
            None
        )

    def set_cancellation_reconciliation_deps(
        self,
        deps: StaleCancellationReconciliationDeps,
    ) -> None:
        self._cancellation_reconciliation_deps = deps

    def set_leader_election(self, leader: LeaderGate | None) -> None:
        """Attach a leader gate instance for distributed leader gating."""
        self._leader = leader

    def configure_timing(
        self,
        *,
        stale_check_minutes: int,
        task_expiry_hours: int,
        pending_task_warning_hours: int,
        orphan_threshold_minutes: int,
        processing_status_expiry_minutes: int,
    ) -> None:
        self.stale_check_minutes = stale_check_minutes
        self.task_expiry_hours = task_expiry_hours
        self.pending_task_warning_hours = pending_task_warning_hours
        self.orphan_threshold_minutes = orphan_threshold_minutes
        self.processing_status_expiry_minutes = processing_status_expiry_minutes

    def set_runtime_deps(self, deps: StaleTaskCheckerDeps) -> None:
        self._runtime_deps = deps

    def set_terminal_event_handler(
        self, handler: Callable[[AgentEvent], Awaitable[None]]
    ) -> None:
        self._terminal_event_handler = handler

    def _deps(self) -> StaleTaskCheckerDeps:
        if self._runtime_deps is not None:
            return self._runtime_deps
        return StaleTaskCheckerDeps(
            store=store,
            rooms_collection=None,
            notify_task_update=notify_task_update,
            increment_counter=increment_counter,
            a2a_service=a2a_service,
        )

    @property
    def _store(self) -> Any:
        return self._deps().store

    @property
    def _rooms_collection(self) -> Any:
        return self._deps().rooms_collection

    @property
    def _a2a_service(self) -> Any:
        return self._deps().a2a_service

    async def _notify_task_update(self, **kwargs) -> Any:
        return await self._deps().notify_task_update(**kwargs)

    def _increment_counter(self, name: str) -> Any:
        return self._deps().increment_counter(name)

    def set_execution_recovery_deps(self, deps: StaleRecoveryDeps) -> None:
        self._execution_recovery_deps = deps

    def set_orchestration_run_recovery_deps(
        self,
        deps: StaleOrchestrationRunRecoveryDeps,
    ) -> None:
        self._orchestration_run_recovery_deps = deps

    def set_run_watchdog_event_deps(self, deps: StaleRunWatchdogEventDeps) -> None:
        self._watchdog_event_deps = deps

    def set_terminal_projection_deps(self, deps: StaleTerminalProjectionDeps) -> None:
        self._terminal_projection_deps = deps

    def set_hitl_deps(self, deps: StaleHITLDeps) -> None:
        self._hitl_deps = deps

    async def start(self) -> None:
        """Start the background checker."""
        if self._running:
            logger.warning("Stale task checker already running")
            return

        self._running = True
        self._task = traced_create_task(self._run_loop(), name="stale-task-checker")
        logger.info(
            f"Stale task checker started (interval: {self.check_interval_minutes}min)"
        )

    async def stop(self) -> None:
        """Stop the background checker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Stale task checker stopped")

    async def _run_loop(self) -> None:
        """Main loop that runs the check periodically."""
        while self._running:
            try:
                await self._run_one_iteration()
            except Exception as exc:
                logger.error(
                    "stale_task_checker_failed",
                    extra={"error_type": type(exc).__name__},
                )

            # Wait for next check
            await asyncio.sleep(self.check_interval_minutes * 60)

    async def _run_one_iteration(self) -> None:
        """Run a single iteration, gated by leader election if available."""
        if self._leader:
            ttl = int(self.check_interval_minutes * 60 * 2)
            acquired = await self._leader.try_acquire(STALE_TASK_CHECKER, ttl)
            if not acquired:
                return  # another instance is the leader
            try:
                await self.check_stale_tasks()
            finally:
                await self._leader.release(STALE_TASK_CHECKER)
        else:
            await self.check_stale_tasks()

    async def check_stale_tasks(self) -> None:
        """
        Main check function that handles stale, expired, and orphaned tasks.

        This is called periodically by the background loop.
        """
        # Durable user cancellation wins before any polling, expiry, or recovery.
        await self._reconcile_pending_cancellations()

        # Aggregate deadlines, durable applications, and command uncertainty are
        # authoritative and must converge before generic stale-agent handling.
        if self._hitl_deps and self._hitl_deps.reconcile_lifecycle is not None:
            try:
                await self._hitl_deps.reconcile_lifecycle()
            except Exception:
                logger.exception("HITL lifecycle reconciliation failed")

        # Get non-terminal state values for queries
        non_terminal_state_values = [s.value for s in NON_TERMINAL_STATES]

        # 1. Check stale tasks (not updated recently)
        stale_messages = await self._store.get_stale_task_messages(
            self.stale_check_minutes, non_terminal_state_values
        )
        stale_log = logger.info if stale_messages else logger.debug
        stale_log(
            "stale_task_scan_completed",
            extra={"result_count": len(stale_messages)},
        )

        for msg in stale_messages:
            await self._process_stale_task(msg)

        # 2. Auto-fail expired tasks (been pending too long)
        expired_messages = await self._store.get_expired_task_messages(
            self.task_expiry_hours, non_terminal_state_values
        )
        expired_log = logger.info if expired_messages else logger.debug
        expired_log(
            "expired_task_scan_completed",
            extra={"result_count": len(expired_messages)},
        )

        for msg in expired_messages:
            await self._auto_fail_expired_task(msg)

        # 3. Recover interrupted orchestration and queue work.
        await self._recover_claimed_orchestration_envelopes()
        await self._recover_orphaned_messages()

        # 4. Clean up stuck room processing status
        await self._cleanup_stuck_processing_status()

        # 5. Auto-fail non-tracked tasks stuck in submitted/working state too long.
        #    These are typically queued pipeline steps that were never picked up
        #    (e.g., server restarted before the pipeline got to them and orphan
        #    recovery didn't re-trigger them).  We use the same expiry threshold
        #    as tracked tasks to be consistent.
        #    We intentionally exclude interactive states (input_required,
        #    auth_required) since non-tracked tasks should never reach those.
        non_tracked_state_values = [
            CommonTaskState.SUBMITTED.value,
            CommonTaskState.WORKING.value,
        ]
        non_tracked_stale = await self._store.get_non_tracked_stale_task_messages(
            self.task_expiry_hours, non_tracked_state_values
        )
        if non_tracked_stale:
            logger.info(
                f"Found {len(non_tracked_stale)} non-tracked stale tasks to auto-fail"
            )
        for msg in non_tracked_stale:
            try:
                await self._auto_fail_non_tracked_task(msg)
            except Exception as exc:
                logger.error(
                    "non_tracked_task_auto_fail_failed",
                    extra={
                        "message_id": msg.message_id,
                        **safe_exception_metadata(exc),
                    },
                )

        # 6. Recover supervisor trajectories stuck in "running" status.
        #    This handles mid-loop crashes where the server restarted while
        #    SupervisorExecutor.run() was in-flight.

        # 6b. Recover stale durable orchestration runs.
        await self._recover_stuck_orchestration_runs()

        # 7. Recover HITL requests stuck in "processing" (worker crashed
        #    between CAS claim and finalization).
        try:
            if self._hitl_deps is None:
                logger.warning(
                    "Skipped stale HITL processing recovery: HITL deps are not bound"
                )
            else:
                await self._hitl_deps.recover_stale_processing()
        except Exception as e:
            logger.error("Failed to recover stale HITL processing requests: %s", e)

        # 8. Repair projections from durable terminal facts before the watchdog
        # can attempt any competing terminal transition.
        await self._recover_terminal_projections()

        # 9. Fail runs stuck without a terminal transition (run lifecycle watchdog).
        await self._fail_stale_runs()

    async def _recover_terminal_projections(self) -> None:
        if self._terminal_projection_deps is None:
            return
        try:
            recovered = await self._terminal_projection_deps.recover_pending(limit=100)
            if recovered:
                logger.info(
                    "terminal_projection_recovery_completed",
                    extra={"result_count": recovered},
                )
        except Exception:
            logger.error("terminal projection recovery failed", exc_info=True)

    async def _fail_stale_runs(self) -> None:
        """Append run_failed for non-terminal runs past RUN_WATCHDOG_STALE_MINUTES."""
        if not settings.feature_run_watchdog:
            return
        stale_mins = settings.run_watchdog_stale_minutes
        try:
            stale = await self._store.find_stale_non_terminal_runs(
                stale_mins,
                limit=100,
            )
        except Exception as e:
            logger.error("run watchdog: failed to list stale runs: %s", e)
            return
        if not stale:
            return
        if self._watchdog_event_deps is None:
            logger.warning(
                "run watchdog: skipped because Execution event dependencies are not bound"
            )
            return
        event_deps = self._watchdog_event_deps

        for doc in stale:
            room_id = str(doc.get("room_id") or "")
            run_id = str(doc.get("run_id") or "")
            if not room_id or not run_id:
                continue
            try:
                payload = await event_deps.append_run_timeout_failure(
                    room_id, run_id, stale_minutes=stale_mins
                )
                if payload is None:
                    continue
                self._increment_counter("run_watchdog_forced_failure_total")
            except Exception as e:
                logger.error(
                    "run watchdog: failed to timeout run_id=%s room=%s: %s",
                    run_id,
                    room_id,
                    e,
                    exc_info=True,
                )

    async def _message_or_parent_cancelled(self, msg: RoomAgentMessage) -> bool:
        if await self._is_message_cancelled_for_recovery(msg.message_id):
            return True
        if msg.related_message_id:
            return await self._is_message_cancelled_for_recovery(msg.related_message_id)
        return False

    async def _process_stale_task(
        self,
        msg: RoomAgentMessage,
    ) -> None:
        """Process a single stale task."""
        message_id = msg.message_id
        if not msg.has_task_tracking:
            return

        agent_url = msg.agent_url
        task = msg.message_content.message_task if msg.message_content else None
        if not task:
            return

        finalization = msg.terminal_finalization or {}
        if (
            finalization.get("state") == "finalizing"
            and finalization.get("recovery_source") == "journal"
        ):
            return
        if (
            finalization.get("state") == "finalizing"
            and self._terminal_event_handler is not None
        ):
            target_state = str(finalization.get("target_state") or "failed")
            kind = (
                "response"
                if target_state == "completed"
                else "canceled"
                if target_state == "canceled"
                else "error"
            )
            await self._terminal_event_handler(
                AgentEvent(
                    kind=kind,
                    message_id=message_id,
                    room_id=msg.room_id,
                    agent_id=msg.agent_id or "",
                    user_id=msg.user_id,
                    related_message_id=msg.related_message_id,
                    turn_id=msg.turn_id,
                    state=target_state,
                    text=msg.message_content.message_text or "",
                    artifacts=(
                        artifacts_to_dicts(task.artifacts) if task.artifacts else None
                    ),
                )
            )
            return

        if await self._message_or_parent_cancelled(msg):
            await self._notify_task_update(
                message_id=message_id,
                state=CommonTaskState.CANCELED,
                room_id=msg.room_id,
                user_id=msg.user_id or "",
            )
            return

        agent_task_id = task.id
        if agent_task_id.startswith(("pending-", "relay-pending-")):
            metadata = task.metadata if isinstance(task.metadata, dict) else {}
            continuation = (
                msg.pending_continuation
                if isinstance(msg.pending_continuation, dict)
                else {}
            )
            recovered_task_id = metadata.get("hitl_a2a_task_id") or continuation.get(
                "a2a_task_id"
            )
            recovered_context_id = metadata.get(
                "hitl_a2a_context_id"
            ) or continuation.get("a2a_context_id")
            if (
                isinstance(recovered_task_id, str)
                and recovered_task_id
                and not recovered_task_id.startswith(("pending-", "relay-pending-"))
                and isinstance(recovered_context_id, str)
                and recovered_context_id
            ):
                logger.warning(
                    "Recovered authoritative A2A continuation IDs for stale message %s",
                    message_id,
                )
                agent_task_id = recovered_task_id

        created_at = (
            ensure_utc(msg.task_created_at) if msg.task_created_at else utcnow()
        )

        # Log warning for long-running tasks
        age_hours = (utcnow() - created_at).total_seconds() / 3600
        if age_hours > self.pending_task_warning_hours:
            logger.warning(
                f"Task for message {message_id} has been pending for {age_hours:.1f} hours"
            )

        # Task was never acknowledged by agent
        if agent_task_id.startswith("pending") or agent_task_id.startswith(
            "relay-pending"
        ):
            logger.warning(
                f"Task for message {message_id} never acknowledged, marking failed"
            )
            await self._mark_task_failed(
                message_id=message_id,
                msg=msg,
                error="Agent did not acknowledge the task",
            )
            return

        if not agent_url:
            logger.warning(
                f"Task for message {message_id} has no agent_url, touching timestamp"
            )
            await self._store.touch_task_message(message_id)
            return

        try:
            # Check if the message was cancelled while the agent was processing
            is_cancelled = await self._is_message_cancelled_for_recovery(message_id)
            if not is_cancelled and msg.related_message_id:
                is_cancelled = await self._is_message_cancelled_for_recovery(
                    msg.related_message_id
                )
            if is_cancelled:
                logger.info(
                    "Stale task for message %s was cancelled — notifying as canceled",
                    message_id,
                )
                await self._notify_task_update(
                    message_id=message_id,
                    state=CommonTaskState.CANCELED,
                    room_id=msg.room_id,
                    user_id=msg.user_id or "",
                )
                return

            # Poll agent for current status
            agent_card = await self._a2a_service.get_agent_card_from_url(agent_url)
            current_task = await self._get_task_from_agent(agent_card, agent_task_id)

            if current_task is None:
                # Agent doesn't have this task anymore
                logger.warning(
                    f"Task for message {message_id} not found on agent, touching timestamp"
                )
                await self._store.touch_task_message(message_id)
                return

            if current_task.artifacts:
                await _best_effort_materialize_artifacts(
                    current_task.artifacts,
                    room_id=msg.room_id,
                    message_id=message_id,
                )

            # Update our record using the same public projection as the normal
            # task completion path; stale recovery must not persist raw remote
            # task metadata or inline file bytes.
            projected_task_data = public_persisted_task_data(current_task)
            projected_task = Task.model_validate(projected_task_data)
            task_text = None
            state_value = getattr(
                current_task.status.state,
                "value",
                current_task.status.state,
            )
            if (
                is_terminal_state(current_task.status.state)
                and state_value == CommonTaskState.COMPLETED.value
                and projected_task.artifacts
            ):
                task_text = (
                    extract_text_from_artifacts(projected_task.artifacts) or None
                )
            if is_terminal_state(current_task.status.state) and (
                self._terminal_event_handler is not None
            ):
                terminal_state = current_task.status.state
                re_cancelled = await self._is_message_cancelled_for_recovery(message_id)
                if not re_cancelled and msg.related_message_id:
                    re_cancelled = await self._is_message_cancelled_for_recovery(
                        msg.related_message_id
                    )
                if re_cancelled or terminal_state == CommonTaskState.CANCELED:
                    kind = "canceled"
                elif terminal_state == CommonTaskState.COMPLETED:
                    kind = "response"
                else:
                    kind = "error"
                await self._terminal_event_handler(
                    AgentEvent(
                        kind=kind,
                        message_id=message_id,
                        room_id=msg.room_id,
                        agent_id=msg.agent_id or "",
                        related_message_id=msg.related_message_id,
                        user_id=msg.user_id,
                        client_request_id=msg.client_request_id,
                        task_id=current_task.id,
                        context_id=current_task.context_id,
                        state=getattr(terminal_state, "value", str(terminal_state)),
                        text=task_text or "",
                        artifacts=[
                            artifact.model_dump(mode="json", exclude_none=True)
                            for artifact in (current_task.artifacts or [])
                        ]
                        or None,
                    )
                )
                return
            await self._store.update_task_on_message(
                message_id,
                projected_task_data,
                message_text=task_text,
            )

            # Notify if terminal or interactive state changed
            new_state = current_task.status.state
            if is_terminal_state(new_state) or new_state in INTERACTIVE_STATES:
                # Re-check cancellation — user may have cancelled between poll and now
                re_cancelled = await self._is_message_cancelled_for_recovery(message_id)
                if not re_cancelled and msg.related_message_id:
                    re_cancelled = await self._is_message_cancelled_for_recovery(
                        msg.related_message_id
                    )
                if re_cancelled:
                    new_state = CommonTaskState.CANCELED
                await self._notify_task_update(
                    message_id=message_id,
                    state=new_state,
                    room_id=msg.room_id,
                    user_id=msg.user_id or "",
                )
            else:
                # Still working - timestamp already touched by update_task_on_message
                logger.debug(
                    f"Task for message {message_id} still in state: {new_state}"
                )

        except Exception as exc:
            logger.warning(
                "stale_task_poll_failed",
                extra={
                    "message_id": message_id,
                    "error_type": type(exc).__name__,
                },
            )
            # Don't fail the task yet - might be transient network issue
            # Touch timestamp to prevent immediate re-check
            await self._store.touch_task_message(message_id)

    async def _get_task_from_agent(self, agent_card, task_id: str) -> Any | None:
        """Get task status from agent."""
        try:
            return await fetch_remote_task(agent_card, task_id)
        except Exception as exc:
            logger.error(
                "remote_task_fetch_failed",
                extra={"error_type": type(exc).__name__},
            )
            return None

    HITL_EXPIRY_HOURS = 24

    async def _auto_fail_expired_task(
        self,
        msg: RoomAgentMessage,
    ) -> None:
        """Auto-fail a task that has been pending too long."""
        message_id = msg.message_id
        if not msg.has_task_tracking:
            return
        if await self._message_or_parent_cancelled(msg):
            return

        task_state = (
            msg.message_content.message_task.status.state
            if msg.message_content
            and msg.message_content.message_task
            and msg.message_content.message_task.status
            else None
        )

        is_interactive = task_state and task_state in INTERACTIVE_STATES
        created_at = (
            ensure_utc(msg.task_created_at) if msg.task_created_at else utcnow()
        )
        age_hours = (utcnow() - created_at).total_seconds() / 3600

        if is_interactive:
            if age_hours < self.HITL_EXPIRY_HOURS:
                return
            logger.warning(
                "Auto-failing HITL task for message %s after %.1f hours "
                "(HITL threshold: %dh)",
                message_id,
                age_hours,
                self.HITL_EXPIRY_HOURS,
            )

        threshold = self.HITL_EXPIRY_HOURS if is_interactive else self.task_expiry_hours
        logger.error(
            f"Auto-failing task for message {message_id} after {age_hours:.1f} hours "
            f"(threshold: {threshold}h)"
        )

        await self._mark_task_failed(
            message_id=message_id,
            msg=msg,
            error=f"Task expired after {threshold} hours without completion. "
            "The agent may be unresponsive.",
        )

    async def _auto_fail_non_tracked_task(
        self,
        msg: RoomAgentMessage,
    ) -> None:
        """Auto-fail a non-tracked task that has been stuck in non-terminal state too long.

        These are typically queued pipeline steps that were created but never
        picked up for processing (e.g., server restarted before the pipeline
        reached them, and orphan recovery did not re-trigger them).
        """
        message_id = msg.message_id
        if await self._message_or_parent_cancelled(msg):
            return
        created_at = msg.message_created_at
        age_hours = (
            (utcnow() - ensure_utc(created_at)).total_seconds() / 3600
            if created_at
            else 0
        )

        current_state = (
            msg.message_content.message_task.status.state
            if msg.message_content and msg.message_content.message_task
            else "unknown"
        )

        logger.warning(
            f"Auto-failing non-tracked task for message {message_id} "
            f"(state: {current_state}, age: {age_hours:.1f}h, "
            f"threshold: {self.task_expiry_hours}h)"
        )

        await self._mark_task_failed(
            message_id=message_id,
            msg=msg,
            error="Task was never processed — it may have been orphaned "
            "due to a server restart or processing failure.",
        )

    async def _mark_task_failed(
        self,
        message_id: str,
        msg: RoomAgentMessage,
        error: str,
    ) -> None:
        """Mark a task as failed and notify the user."""
        task = msg.message_content.message_task if msg.message_content else None
        task_id = task.id if task else "unknown"
        context_id = task.context_id if task else ""

        failed_task = build_failed_text_task(
            task_id=task_id,
            context_id=context_id,
            error_text=error,
        )

        await self._store.update_task_on_message(
            message_id,
            public_persisted_task_data(failed_task),
        )

        # Clear any orphaned continuation (on both agent and user messages)
        await self._store.get_and_clear_continuation_on_message(message_id)
        if msg.related_message_id:
            await self._store.get_and_clear_continuation_on_user_message(
                msg.related_message_id
            )

        # Cancel any pending HITL request for this message
        try:
            # HITL requests are keyed by user_message_id, not agent message_id.
            # related_message_id on the agent message points to the user message.
            user_msg_id = msg.related_message_id or message_id
            if self._hitl_deps is None:
                logger.warning(
                    "stale_task_checker: HITL deps are not bound; cannot cancel "
                    "requests for %s",
                    user_msg_id,
                )
            else:
                await self._hitl_deps.cancel_requests_for_message(
                    user_msg_id,
                    failure_reason=error,
                )
        except Exception as e:
            logger.warning(
                "stale_task_checker: Failed to cancel HITL requests for %s: %s",
                message_id,
                e,
            )

        await self._notify_task_update(
            message_id=message_id,
            state=CommonTaskState.FAILED,
            room_id=msg.room_id,
            user_id=msg.user_id or "",
        )

    async def _cleanup_stuck_processing_status(self) -> None:
        """
        Null legacy rooms.processing_message_id when there are no non-terminal runs.

        Mirrors are no longer written on the lifecycle path; this job clears stale
        Mongo values using the same predicate as compaction (runs-only busy).
        """
        try:
            busy_ids = await self._store.get_room_ids_with_non_terminal_runs()
        except Exception as e:
            logger.warning(
                "legacy processing_message_id cleanup: could not list active rooms: %s",
                e,
            )
            return

        busy = list({rid for rid in busy_ids if rid})
        flt: dict = {"processing_message_id": {"$ne": None}}
        if busy:
            flt["room_id"] = {"$nin": busy}
        try:
            coll = self._rooms_collection
            if coll is None:
                raise RuntimeError("rooms_collection is not bound")
            res = await coll.update_many(flt, {"$set": {"processing_message_id": None}})
            modified_count = getattr(res, "modified_count", res)
            if modified_count:
                logger.info(
                    "legacy processing_message_id cleanup: nulled field on %d rooms "
                    "(no non-terminal runs)",
                    modified_count,
                )
        except Exception as e:
            logger.warning("legacy processing_message_id cleanup failed: %s", e)

    async def _is_message_cancelled_for_recovery(self, message_id: str) -> bool:
        strict_reader = getattr(
            self._store,
            "is_message_cancelled_strict",
            self._store.is_message_cancelled,
        )
        return await strict_reader(message_id)

    def _release_recovery_slot(self, _task: asyncio.Task) -> None:
        self._recovery_semaphore.release()

    async def _schedule_recovery_with_slot(
        self,
        request: OrchestrationRequest,
        *,
        reason: str,
    ) -> bool:
        """Schedule recovery with exception-safe semaphore ownership."""
        if self._execution_recovery_deps is None:
            raise RuntimeError("Execution recovery dependencies are not bound")
        if self._recovery_semaphore.locked():
            return False

        await self._recovery_semaphore.acquire()
        try:
            task = self._execution_recovery_deps.schedule_recovery(
                request,
                reason=reason,
            )
        except Exception:
            self._recovery_semaphore.release()
            raise
        task.add_done_callback(self._release_recovery_slot)
        return True

    async def _repair_orchestration_envelope_from_run(
        self,
        message: Any,
        state: Any,
    ) -> None:
        """Repair stale message projection from the canonical durable run."""
        extend_info = getattr(message, "extend_info", None)
        if not isinstance(extend_info, dict):
            return
        status = getattr(state, "status", None)
        status_value = getattr(status, "value", status)
        if not isinstance(status_value, str) or not status_value:
            return

        terminal = status in TERMINAL_ORCHESTRATION_STATUSES
        expected_status = extend_info.get("orchestration_status")
        if not isinstance(expected_status, str) or not expected_status:
            return
        if expected_status == status_value and (
            not terminal or getattr(message, "processing_claimed_at", None) is None
        ):
            return
        updated = await self._store.update_orchestration_projection_if_status(
            message.message_id,
            expected_status=expected_status,
            status=status_value,
            clear_processing_claim=terminal,
        )
        if updated:
            extend_info["orchestration_status"] = status_value
            if terminal:
                message.processing_claimed_at = None

    async def _recover_claimed_orchestration_envelopes(self) -> None:
        """Recover supervisor requests interrupted before durable run creation."""
        if self._execution_recovery_deps is None:
            logger.warning(
                "Orchestration envelope recovery skipped: Execution recovery "
                "dependencies are not bound"
            )
            return
        if self._orchestration_run_recovery_deps is None:
            logger.warning(
                "Orchestration envelope recovery skipped: run-store dependencies "
                "are not bound"
            )
            return

        run_store = self._orchestration_run_recovery_deps.orchestration_run_store
        batch_size = 100
        after_message_id: str | None = None
        while True:
            try:
                messages = await self._store.get_stale_claimed_orchestration_messages(
                    self.orphan_threshold_minutes,
                    limit=batch_size,
                    after_message_id=after_message_id,
                )
            except Exception:
                logger.error(
                    "orchestration_envelope_recovery_scan_failed",
                    exc_info=True,
                )
                return
            if not messages:
                return

            for message in messages:
                message_id = getattr(message, "message_id", None)
                room_id = getattr(message, "room_id", None)
                if not isinstance(message_id, str) or not message_id:
                    continue
                if not isinstance(room_id, str) or not room_id:
                    continue
                try:
                    existing = await run_store.get_latest_by_user_message_id(message_id)
                    if existing is not None:
                        await self._repair_orchestration_envelope_from_run(
                            message,
                            existing,
                        )
                        continue
                    if await self._is_message_cancelled_for_recovery(message_id):
                        extend_info = getattr(message, "extend_info", None)
                        expected_status = (
                            extend_info.get("orchestration_status")
                            if isinstance(extend_info, dict)
                            else None
                        )
                        if isinstance(expected_status, str) and expected_status:
                            await self._store.update_orchestration_projection_if_status(
                                message_id,
                                expected_status=expected_status,
                                status="canceled",
                                clear_processing_claim=True,
                            )
                        continue
                    scheduled = await self._schedule_recovery_with_slot(
                        OrchestrationRequest(
                            room_id=room_id,
                            room_user_message_id=message_id,
                            room_related_message_id="",
                            is_recovery=True,
                        ),
                        reason="orchestration-envelope",
                    )
                    if not scheduled:
                        logger.info(
                            "Recovery slots full, deferring remaining orchestration "
                            "envelopes"
                        )
                        return
                except Exception as exc:
                    logger.error(
                        "orchestration_envelope_recovery_schedule_failed",
                        extra={
                            "room_id": room_id,
                            "user_message_id": message_id,
                            **safe_exception_metadata(exc),
                        },
                    )

            if len(messages) < batch_size:
                return
            after_message_id = messages[-1].message_id

    async def _recover_orphaned_messages(self) -> None:
        """
        Recover orphaned agent messages that were never processed.

        This handles the case where:
        1. User sends a message
        2. SendMessage creates user message + agent messages
        3. User refreshes before processRoomUserMessage is called
        4. Agent messages exist but were never executed

        Recovery groups orphaned messages by their related_message_id (user message)
        and triggers processing for each unique user message.
        """
        orphaned_messages = await self._store.get_orphaned_agent_messages(
            self.orphan_threshold_minutes
        )

        if not orphaned_messages:
            return
        if self._execution_recovery_deps is None:
            logger.warning(
                "Orphan recovery skipped: Execution recovery dependencies are not bound"
            )
            return

        logger.info(
            f"Found {len(orphaned_messages)} orphaned agent messages to recover"
        )

        # Group by related_message_id (user message) to avoid duplicate processing
        user_messages_to_process: dict[str, str] = {}  # user_message_id -> room_id

        for msg in orphaned_messages:
            # Skip hub-sourced agents — their timeouts are managed by the
            # relay offline queue TTL, not the orphan recovery job.
            agent = (
                await self._store.get_agent_by_agent_id(msg.agent_id)
                if msg.agent_id
                else None
            )
            if agent and getattr(agent, "source", "cloud") == "hub":
                continue

            user_message_id = msg.related_message_id
            if user_message_id and user_message_id not in user_messages_to_process:
                user_messages_to_process[user_message_id] = msg.room_id
                logger.info(
                    f"Orphaned message {msg.message_id} belongs to user message {user_message_id} "
                    f"in room {msg.room_id}"
                )

        for user_message_id, room_id in user_messages_to_process.items():
            try:
                if await self._is_message_cancelled_for_recovery(user_message_id):
                    continue
                logger.info(
                    f"Recovering orphaned messages for user message {user_message_id} in room {room_id}"
                )

                request = OrchestrationRequest(
                    room_id=room_id,
                    room_user_message_id=user_message_id,
                    room_related_message_id="",
                    is_recovery=True,
                )

                # Process in background with bounded concurrency (SDR 2.13).
                # Non-blocking: if all slots are occupied, defer remaining
                # to the next checker cycle so steps 4-7 aren't starved.
                scheduled = await self._schedule_recovery_with_slot(
                    request,
                    reason="orphan",
                )
                if not scheduled:
                    logger.info(
                        "Recovery slots full, deferring remaining orphan recoveries "
                        "to next cycle"
                    )
                    break

            except Exception as exc:
                logger.error(
                    "orphan_recovery_schedule_failed",
                    extra={
                        "room_id": room_id,
                        "user_message_id": user_message_id,
                        **safe_exception_metadata(exc),
                    },
                )

    async def _reconcile_pending_cancellations(self) -> None:
        """Trigger Execution-owned reconciliation of pending markers."""
        if self._cancellation_reconciliation_deps is None:
            logger.warning("Cancellation reconciliation dependency is not bound")
            return
        settle_cutoff = utcnow() - timedelta(minutes=self.orphan_threshold_minutes)
        await self._cancellation_reconciliation_deps.reconciliation.reconcile_pending(
            settle_cutoff=settle_cutoff
        )

    async def _recover_stuck_orchestration_runs(self) -> None:
        """Recover stale durable orchestration runs.

        Supervisor checkpoints keep durable progress in ``OrchestrationRunState``
        rather than ``user_message.extend_info``. This watchdog claims stale
        non-terminal runs with optimistic
        concurrency and reuses the normal recovery orchestration path.
        """
        if self._orchestration_run_recovery_deps is None:
            return
        if self._execution_recovery_deps is None:
            logger.warning(
                "orchestration_recovery: skipped because Execution recovery dependencies are not bound"
            )
            return

        run_store = self._orchestration_run_recovery_deps.orchestration_run_store
        cutoff = utcnow() - timedelta(minutes=self.orphan_threshold_minutes)
        try:
            states = await run_store.list_recoverable()
        except Exception as e:
            logger.error("orchestration_recovery: failed to list runs: %s", e)
            return

        for state in states:
            if ensure_utc(state.updated_at) > cutoff:
                continue
            if await self._is_message_cancelled_for_recovery(state.user_message_id):
                # Pending durable markers are retried by the Execution-owned
                # reconciliation pass at the start of each checker cycle.
                continue
            if state.status.value == "awaiting_user":
                logger.info(
                    "orchestration_recovery: skipping run %s — awaiting user input",
                    state.run_id,
                )
                continue
            if self._recovery_semaphore.locked():
                logger.info(
                    "Recovery slots full, deferring remaining orchestration recoveries"
                )
                break

            get_user_message = getattr(
                self._store,
                "get_room_user_message_by_message_id",
                None,
            )
            if not callable(get_user_message):
                logger.error(
                    "orchestration_recovery: skipping run %s because the "
                    "processing-claim reader is not bound",
                    state.run_id,
                )
                continue
            try:
                user_message = await get_user_message(state.user_message_id)
                processing_claimed_at = (
                    user_message.get("processing_claimed_at")
                    if isinstance(user_message, dict)
                    else getattr(user_message, "processing_claimed_at", None)
                )
                if (
                    processing_claimed_at is not None
                    and ensure_utc(processing_claimed_at) > cutoff
                ):
                    logger.info(
                        "orchestration_recovery: skipping live run %s",
                        state.run_id,
                    )
                    continue
            except Exception:
                logger.warning(
                    "orchestration_recovery: failed to inspect processing "
                    "claim for %s; skipping recovery",
                    state.run_id,
                    exc_info=True,
                )
                continue

            expected_version = state.state_version
            claimed = state.model_copy(deep=True)
            claimed.state_version = expected_version + 1
            claimed.updated_at = utcnow()
            try:
                saved = await run_store.save_state(
                    claimed,
                    expected_version=expected_version,
                )
            except OrchestrationStoreConflict:
                logger.info(
                    "orchestration_recovery: run %s already claimed",
                    state.run_id,
                )
                continue
            except Exception as e:
                logger.error(
                    "orchestration_recovery: failed to claim run %s: %s",
                    state.run_id,
                    e,
                )
                continue

            try:
                await run_store.append_event(
                    OrchestrationRunEvent(
                        run_id=saved.run_id,
                        room_id=saved.room_id,
                        type=OrchestrationEventType.RUN_RECOVERED,
                        state_version=saved.state_version,
                        payload={
                            "previous_status": state.status.value,
                            "user_message_id": state.user_message_id,
                        },
                    )
                )
            except OrchestrationStoreConflict:
                logger.info(
                    "orchestration_recovery: recovery event already recorded for %s",
                    saved.run_id,
                )
            except Exception:
                logger.debug(
                    "orchestration_recovery: failed to append recovery event",
                    exc_info=True,
                )

            try:
                logger.info(
                    "orchestration_recovery: re-triggering run %s message %s",
                    saved.run_id,
                    saved.user_message_id,
                )
                request = OrchestrationRequest(
                    room_id=saved.room_id,
                    room_user_message_id=saved.user_message_id,
                    room_related_message_id="",
                    is_recovery=True,
                )
                scheduled = await self._schedule_recovery_with_slot(
                    request,
                    reason="orchestration",
                )
                if not scheduled:
                    logger.info(
                        "Recovery slots full, deferring orchestration run %s",
                        saved.run_id,
                    )
            except Exception as e:
                logger.error(
                    "orchestration_recovery: failed to trigger recovery for %s: %s",
                    saved.run_id,
                    e,
                )


# Singleton instance. Application startup binds runtime dependencies and settings.
stale_task_checker = StaleTaskChecker()
