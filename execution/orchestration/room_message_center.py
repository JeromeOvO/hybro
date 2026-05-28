from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import timedelta
from typing import Any
from uuid import uuid4

from a2a_adapter.task_status import build_completed_text_task
from common.a2a_constants import CommonTaskState, SSEProcessingStatus, is_terminal_state
from common.protocols import RoomDistributedLock
from common.utils.context_utils import get_context_stats
from common.utils.logger import get_logger
from common.utils.summary_streaming import stream_summary_to_sse
from common.utils.time import utcnow
from models.request import OrchestrationRequest, RoomCenterAgentMessageRequest
from models.response import OrchestrationResponse
from models.room import CoordinatorAgentId
from models.supervisor_v2 import (
    AgentProfile,
    RoomConfig,
    RunStatus,
    StepStatus,
    SupervisorRunResult,
    SupervisorTrajectory,
    TrajectoryStatus,
)
from execution.dispatch.agent_dispatcher import AgentDispatcher
from execution.dispatch.agent_message_processor import AgentMessageProcessor
from execution.dispatch.response_handler import AgentResponseHandler
from execution.legacy_processing_status import LegacyProcessingStatusC3Adapter
from execution.orchestration.queue_executor import QueueExecutor, QueueResult
from execution.dispatch.transports.direct import DirectTransport
from execution.orchestration.supervisor_executor import SupervisorExecutor
from execution.state.task_state_manager import TaskStateManager

a2a_service = None
agent_resolver_service = None
db_service = None
debate_service = None
room_memory_service = None
notification_service = None
rate_limit_service = None
room_coordinator_service = None
openai_service = None
room_services = None
room_supervisor_service = None
sse_manager = None
task_service = None
context_assembly_service = None
memory_search_service = None
compaction_service = None
build_turn_content = None
SupervisorPlanningError = RuntimeError

logger = get_logger(__name__)


class _RoomMessageCenterSettings:
    orphan_threshold_minutes = 2


settings = _RoomMessageCenterSettings()

# Maximum time (seconds) to wait for a per-room lock before giving up.
# MUST be shorter than orphan_threshold_minutes (default 2 min = 120s) to
# prevent the stale-task checker from reclaiming a message that is still
# queued behind the lock — which would cause double-processing.
ROOM_LOCK_TIMEOUT_SECONDS = 90

# Redis key TTL for the distributed lock.  Must be much longer than the
# expected room-processing duration (supervisor loops, LLM calls, agent
# round-trips).  If a room genuinely takes this long, the lock will expire
# and another worker may enter.  600 s = 10 minutes is generous enough for
# multi-step supervisor flows while still recovering from crashed workers.
ROOM_LOCK_HOLD_TTL_SECONDS = 600


class RoomMessageCenter:
    """Room user message processing: agent communication,
    streaming/sync responses, queue management, and memory updates."""

    def __init__(
        self,
        *,
        room_services,
        database_service,
        sse_manager,
        room_coordinator_service,
        openai_service,
        notification_service,
        agent_resolver_service,
        a2a_service,
        task_service,
        room_memory_service,
        debate_service,
        rate_limit_service,
        room_supervisor_service,
        hitl_coordinator,
        task_notifications,
        task_notification_impl=None,
        agent_health_service=None,
        s3_service=None,
        capability_issue_service=None,
        context_assembly_service=None,
        memory_search_service=None,
        compaction_service=None,
        build_turn_content_func=None,
        supervisor_planning_error_cls=RuntimeError,
        orphan_threshold_minutes: int | None = None,
        debate_rounds: int = 1,
        cloud_health_cache_ttl: float = 30.0,
        cloud_health_check_timeout: float = 5.0,
    ):
        self.room_services = room_services
        self.database_service = database_service
        self.sse_manager = sse_manager
        self.room_coordinator_service = room_coordinator_service
        self.openai_service = openai_service
        self.task_notifications = task_notifications
        self.room_memory_service = room_memory_service
        self.context_assembly_service = context_assembly_service
        self.memory_search_service = memory_search_service
        self.compaction_service = compaction_service
        self.build_turn_content = build_turn_content_func
        self.supervisor_planning_error_cls = supervisor_planning_error_cls
        self.orphan_threshold_minutes = (
            settings.orphan_threshold_minutes
            if orphan_threshold_minutes is None
            else orphan_threshold_minutes
        )
        self.debate_rounds = debate_rounds
        self.tsm = TaskStateManager(self.room_services, notification_service)
        self.agent_dispatcher = AgentDispatcher(
            agent_resolver=agent_resolver_service,
            database_service=self.database_service,
        )

        # Shared result handler used by all transports
        self.agent_response_handler = AgentResponseHandler(
            db=self.database_service,
            sse=self.sse_manager,
            room_message_center=self,
            hitl_coordinator=hitl_coordinator,
            notification_service=notification_service,
            task_notification_impl=task_notification_impl,
        )

        # DirectTransport contains all streaming/sync response processing
        self.direct_transport = DirectTransport(
            response_handler=self.agent_response_handler,
            tsm=self.tsm,
            a2a_service=a2a_service,
            task_service=task_service,
            sse_manager=self.sse_manager,
            database_service=self.database_service,
            s3_service=s3_service,
            capability_issue_service=capability_issue_service,
        )

        # Relay service + dispatch middleware are initialized eagerly in
        # init_relay_service().  AgentMessageProcessor resolves the singleton
        # lazily on first use and builds the outbound transport in Execution.
        self.agent_message_processor = AgentMessageProcessor(
            sse_manager=self.sse_manager,
            room_services=self.room_services,
            database_service=self.database_service,
            transports={"direct": self.direct_transport},
            health_service=agent_health_service,
            cloud_health_cache_ttl=cloud_health_cache_ttl,
            cloud_health_check_timeout=cloud_health_check_timeout,
        )
        self.queue_executor = QueueExecutor(
            tsm=self.tsm,
            sse_manager=self.sse_manager,
            a2a_service=a2a_service,
            room_services=self.room_services,
            room_memory_service=room_memory_service,
            database_service=self.database_service,
            debate_service=debate_service,
            rate_limit_service=rate_limit_service,
            agent_dispatcher=self.agent_dispatcher,
            agent_message_processor=self.agent_message_processor,
            response_handler=self.agent_response_handler,
            hitl_coordinator=hitl_coordinator,
        )
        self.supervisor_executor = SupervisorExecutor(
            supervisor_service=room_supervisor_service,
            room_services=self.room_services,
            tsm=self.tsm,
            sse_manager=self.sse_manager,
            database_service=self.database_service,
            room_memory_service=room_memory_service,
            rate_limit_service=rate_limit_service,
            agent_dispatcher=self.agent_dispatcher,
            agent_message_processor=self.agent_message_processor,
            room_coordinator_service=self.room_coordinator_service,
            hitl_coordinator=hitl_coordinator,
            debate_rounds=self.debate_rounds,
        )
        self._turn_event_appender = None

        # Per-room asyncio locks to serialise processing within the same room.
        # Prevents concurrent supervisor runs / queue executions that would
        # corrupt shared state (context, compaction, memory).
        #
        # With multi-worker Gunicorn these process-local locks are supplemented
        # by a Redis distributed lock (SETNX + TTL).  When Redis is available
        # the distributed lock is the primary gate and the asyncio lock provides
        # intra-process fairness.  Without Redis the asyncio lock alone guards
        # the critical section (single-worker only).
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._room_distributed_lock: RoomDistributedLock | None = None
        self._room_facade = None
        self._room_bound = False
        self._processing_status_emitter = None
        # Turn-event infrastructure is retired; keep placeholders to
        # preserve defensive getattr/None checks in legacy code paths.

    # -- Redis wiring (called from main.py at startup) ---------------------

    def bind_execution_event_deps(self, processing_status_emitter) -> None:
        self._processing_status_emitter = processing_status_emitter
        for component in (
            self.agent_response_handler,
            self.queue_executor,
            self.supervisor_executor,
        ):
            binder = getattr(component, "bind_execution_event_deps", None)
            if binder is not None:
                binder(processing_status_emitter)

    async def _emit_processing_status(
        self,
        *,
        room_id: str,
        status,
        message_id: str | None,
        lifecycle_message_id: str | None = None,
        record_lifecycle: bool = True,
        client_request_id: str | None = None,
        details=None,
        agents: list[dict] | None = None,
    ) -> None:
        legacy_details = details if isinstance(details, str) else None
        structured_details = details if isinstance(details, dict) else None
        if getattr(self, "_processing_status_emitter", None) is not None:
            await self._processing_status_emitter(
                room_id=room_id,
                status=status,
                message_id=message_id,
                lifecycle_message_id=lifecycle_message_id or message_id,
                record_lifecycle=record_lifecycle,
                client_request_id=client_request_id,
                details=structured_details,
                legacy_details=legacy_details,
                error_message=legacy_details,
                agents=agents,
            )
            return
        await LegacyProcessingStatusC3Adapter(self.sse_manager).emit_processing_status(
            room_id=room_id,
            status=status,
            message_id=message_id,
            details=details,
            client_request_id=client_request_id,
            agents=agents,
        )

    def bind_facade(self, facade) -> None:
        self._room_facade = facade
        self._room_bound = True

    def _require_room_facade(self):
        if (
            not getattr(self, "_room_bound", False)
            or getattr(self, "_room_facade", None) is None
        ):
            raise RuntimeError(
                "RoomMessageCenter.bind_facade() not called - startup incomplete"
            )
        return self._room_facade

    def set_room_distributed_lock(
        self, room_lock: RoomDistributedLock | None
    ) -> None:
        self._room_distributed_lock = room_lock
        # Turn-event dual-write wiring removed. Runtime now uses
        # message/task SSE as the single source of truth.

    def set_redis_service(self, redis_service: RoomDistributedLock | None) -> None:
        self.set_room_distributed_lock(redis_service)
        # Turn-event dual-write wiring removed. Runtime now uses
        # message/task SSE as the single source of truth.

    # -- Distributed room lock ---------------------------------------------

    _ROOM_LOCK_PREFIX = "room:lock:"

    # Lua script for safe release: only delete the key if we still own it.
    _RELEASE_LOCK_LUA = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"

    async def _acquire_distributed_lock(
        self, room_id: str, owner: str, ttl: int = ROOM_LOCK_HOLD_TTL_SECONDS,
    ) -> bool | None:
        """Try to acquire the Redis distributed lock for *room_id*.

        Returns ``True`` if the lock was acquired, ``False`` if another
        worker already holds it, or ``None`` if Redis is unavailable or
        erroring (caller should fall back to local-only locking).

        The injected lock preserves the tri-state result so Redis errors
        remain distinguishable from contention.
        """
        if self._room_distributed_lock is None:
            return None
        return await self._room_distributed_lock.acquire(room_id, owner, ttl)

    async def _release_distributed_lock(self, room_id: str, owner: str) -> None:
        """Release the Redis distributed lock only if we still own it."""
        if self._room_distributed_lock is None:
            return
        await self._room_distributed_lock.release(room_id, owner)

    async def _acquire_room_lock(
        self, room_id: str, timeout: float = ROOM_LOCK_TIMEOUT_SECONDS,
    ) -> str | None:
        """Acquire both the distributed Redis lock and the local asyncio lock.

        Returns an *owner* token on success (must be passed to
        ``_release_room_lock``), or ``None`` on timeout / contention.

        Acquisition order: distributed first (cross-process), then local
        (intra-process).  If the distributed lock cannot be obtained within
        *timeout* seconds, returns ``None`` without touching the local lock.
        When Redis is unavailable, falls back to the local lock only (safe
        for single-worker deployment).
        """
        owner = uuid4().hex
        use_distributed = self._room_distributed_lock is not None

        loop = asyncio.get_event_loop()
        t0 = loop.time()
        elapsed = 0.0

        # --- Distributed lock (polling with back-off) ---------------------
        if use_distributed:
            poll_interval = 0.5
            redis_errors = 0
            while elapsed < timeout:
                result = await self._acquire_distributed_lock(room_id, owner, ttl=ROOM_LOCK_HOLD_TTL_SECONDS)
                if result is True:
                    if elapsed > 1.0:
                        logger.info(
                            "Distributed lock acquired for room %s (owner=%s, waited=%.1fs, ttl=%ds)",
                            room_id, owner[:8], elapsed, ROOM_LOCK_HOLD_TTL_SECONDS,
                        )
                    else:
                        logger.debug(
                            "Distributed lock acquired for room %s (owner=%s, waited=%.1fs)",
                            room_id, owner[:8], elapsed,
                        )
                    break
                if result is None:
                    redis_errors += 1
                    if redis_errors >= 2:
                        logger.warning(
                            "Redis unhealthy (%d consecutive errors) while acquiring "
                            "lock for room %s — falling back to local lock only",
                            redis_errors, room_id,
                        )
                        use_distributed = False
                        break
                else:
                    redis_errors = 0
                await asyncio.sleep(poll_interval)
                elapsed = loop.time() - t0
                poll_interval = min(poll_interval * 1.5, 5.0)
            else:
                logger.warning(
                    "Distributed lock timeout for room %s after %.1fs (owner=%s)",
                    room_id, elapsed, owner[:8],
                )
                return None  # timed out waiting for distributed lock

        # --- Local asyncio lock (intra-process fairness) ------------------
        local_lock = self._get_local_lock(room_id)
        try:
            remaining = max(0.1, timeout - elapsed)
            await asyncio.wait_for(local_lock.acquire(), timeout=remaining)
        except asyncio.TimeoutError:
            if use_distributed:
                await self._release_distributed_lock(room_id, owner)
            return None

        return owner

    async def _release_room_lock(
        self, room_id: str, owner: str | None, *, acquired_at: float | None = None,
    ) -> None:
        """Release both the local asyncio lock and the distributed Redis lock."""
        if acquired_at is not None:
            held_seconds = time.monotonic() - acquired_at
            if held_seconds > ROOM_LOCK_HOLD_TTL_SECONDS * 0.8:
                logger.warning(
                    "Room %s held lock for %.0fs — approaching TTL of %ds. "
                    "Consider investigating slow processing or adding lock renewal.",
                    room_id, held_seconds, ROOM_LOCK_HOLD_TTL_SECONDS,
                )
            elif held_seconds > 60:
                logger.info(
                    "Room %s held lock for %.0fs",
                    room_id, held_seconds,
                )
        local_lock = self._room_locks.get(room_id)
        if local_lock is not None and local_lock.locked():
            local_lock.release()
        if owner is not None:
            await self._release_distributed_lock(room_id, owner)
            logger.debug(
                "Distributed lock released for room %s (owner=%s)",
                room_id, owner[:8],
            )

    def _get_local_lock(self, room_id: str) -> asyncio.Lock:
        """Return (or create) the process-local asyncio.Lock for *room_id*."""
        lock = self._room_locks.get(room_id)
        if lock is None:
            lock = asyncio.Lock()
            self._room_locks[room_id] = lock
        return lock

    # ------------------------------------------------------------------

    async def process_room_user_message(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse:
        """
        Process a room user message by executing all related agent messages in sequence.

        This method:
        1. Gets room memory context
        2. Queries all agent messages related to the user message
        3. Processes each agent message in order using streaming
        4. Updates room memory after all agents have responded
        5. Sends SSE events to the room for real-time updates

        Args:
            request: Contains room_id and room_user_message_id

        Returns:
            OrchestrationResponse with success status
        """
        logger.debug(
            "RoomMessageCenter: Starting to process room user message %s in room %s",
            request.room_user_message_id,
            request.room_id,
        )

        # Validate request
        validation_response = self._validate_room_message_request(request)
        if validation_response:
            return validation_response

        # Idempotency guard (SDR 2.5)
        if request.is_recovery:
            stale_threshold = utcnow() - timedelta(
                minutes=getattr(
                    self,
                    "orphan_threshold_minutes",
                    settings.orphan_threshold_minutes,
                )
            )
            claimed = await self.database_service.claim_or_reclaim_user_message(
                request.room_user_message_id, stale_threshold
            )
        else:
            claimed = await self.database_service.claim_user_message_for_processing(
                request.room_user_message_id
            )

        if not claimed:
            logger.warning(
                "Message %s: claim failed (is_recovery=%s), skipping",
                request.room_user_message_id,
                request.is_recovery,
            )
            return OrchestrationResponse(
                room_id=request.room_id,
                success=False,
                error="Message is already being processed",
                status_code=409,
            )

        room_id = request.room_id
        room_user_message_id = request.room_user_message_id

        # ----- Per-room lock: serialise all processing within a room -----
        lock_owner = await self._acquire_room_lock(room_id)
        lock_acquired_at = time.monotonic()
        if lock_owner is None:
            logger.error(
                "RoomMessageCenter: Timed out waiting for room lock on %s "
                "(message %s). Another processing run may be stuck.",
                room_id,
                room_user_message_id,
            )
            # Release the claim so the message can be retried (by user or
            # stale-recovery) instead of staying permanently orphaned.
            await self.database_service.unclaim_user_message(
                room_user_message_id
            )
            # Fail any descendant agent messages created during the
            # parse/prepare step so they don't remain as orphaned
            # non-terminal task bubbles in the frontend.
            await self._notify_all_non_terminal_tasks_failed(
                room_id, room_user_message_id,
            )
            # Send terminal SSE so the frontend clears the processing
            # indicator.  Without this, the Stop button stays stuck because
            # send_message_to_room already emitted PROCESSING and this
            # BackgroundTask response is never seen by the client.
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="Room is busy processing another message — please retry shortly",
            )
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error="Room is busy processing another message — please retry shortly",
                status_code=429,
            )

        # Refresh the processing claim timestamp now that we hold the lock.
        # The claim was set before the lock wait; if the wait was long, the
        # stale task checker (orphan_threshold_minutes=2 min) might consider
        # the message orphaned.  Touching the timestamp here resets the clock
        # so processing won't be reclaimed prematurely.
        await self.database_service.refresh_processing_claim(
            room_user_message_id
        )

        # Busy / cancel targeting use `runs` + `active_runs` (not rooms.processing_message_id).

        try:
            return await self._process_room_user_message_locked(
                request, room_id, room_user_message_id
            )
        except asyncio.CancelledError:
            logger.info(
                "RoomMessageCenter: processing task cancelled for message %s",
                room_user_message_id,
            )
            if getattr(self, '_turn_event_appender', None):
                try:
                    await self._turn_event_appender.append(
                        room_id, room_user_message_id, "turn_canceled", {},
                    )
                except Exception:
                    pass
            await self._notify_all_non_terminal_tasks_failed(
                room_id, room_user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.CANCELED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
            )
            self.sse_manager.clear_cancellation(room_user_message_id)
            raise
        finally:
            await self._release_room_lock(room_id, lock_owner, acquired_at=lock_acquired_at)

    async def _process_room_user_message_locked(
        self,
        request: OrchestrationRequest,
        room_id: str,
        room_user_message_id: str,
    ) -> OrchestrationResponse:
        """Inner processing path — caller MUST hold the per-room lock."""

        # Get user_id from the user message for rate limiting.
        # Fall back to the request-level user_id (from auth) if the stored
        # message is missing or has no user_id.
        user_message = await self.database_service.get_room_user_message_by_message_id(
            room_user_message_id
        )
        if user_message and self._turn_event_appender:
            try:
                already_started = await self.database_service.turn_exists(
                    room_id, room_user_message_id
                )
                if not already_started:
                    message_content = user_message.message_content
                    attachments = message_content.attachments or []
                    await self._turn_event_appender.start_turn(
                        room_id=room_id,
                        turn_id=room_user_message_id,
                        user_input={
                            "text": message_content.message_text or "",
                            "attachment_count": len(attachments),
                        },
                        client_request_id=request.client_request_id,
                    )
            except Exception:
                logger.exception(
                    "Failed to start turn journal for room=%s turn=%s",
                    room_id,
                    room_user_message_id,
                )
        user_id = (
            (user_message.user_id if user_message else None) or request.user_id
        )

        # Extract quoted context via TurnContext (QUOTE_REPLY: quote_id + snippet or legacy)
        quoted_text: str | None = None
        if user_message:
            from execution.orchestration.turn_context import (
                TurnQuoteMissingError,
                load_turn_context,
            )

            try:
                _tc = await load_turn_context(self.database_service, user_message)
                quoted_text = _tc.quoted_text
            except TurnQuoteMissingError as e:
                logger.error("RoomMessageCenter: %s", e)
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.FAILED,
                    message_id=room_user_message_id,
                    lifecycle_message_id=room_user_message_id,
                    details="Quoted context could not be loaded for this turn",
                )
                return OrchestrationResponse(
                    room_id=room_id,
                    success=False,
                    error=str(e),
                    status_code=400,
                )

        # Create a CancellationToken for this message pipeline (A-3).
        # The token is pre-signalled if cancel_message() was called before
        # processing started — no race window.
        # If a token was already created (e.g. by send_message_to_room for
        # the parsing phase), reuse it so the entire pipeline shares one token.
        token = self.sse_manager.get_token(room_user_message_id)
        if token is None:
            token = self.sse_manager.create_token(room_user_message_id)

        # --- V2 Supervisor branch ---
        # The primary signal is supervisor_v2=True in extend_info (set by
        # _prepare_for_supervisor_v2).
        is_supervisor_v2 = (
            user_message
            and isinstance(user_message.extend_info, dict)
            and user_message.extend_info.get("supervisor_v2", False)
        )
        if is_supervisor_v2:
            return await self._process_supervisor_v2(
                user_message=user_message,
                room_id=room_id,
                room_user_message_id=room_user_message_id,
                user_id=user_id,
                quoted_text=quoted_text,
                token=token,
            )

        # --- QueueExecutor path (legacy routing, @mentions, etc.) ---
        # Query pre-created agent messages.  Both the legacy parse path and
        # the @mentions flow create RoomAgentMessage records during Phase 1
        # (send_message_to_room).  The V2 supervisor loop does NOT pre-create
        # agent messages — it generates them dynamically — so this query is
        # only reached for non-V2 messages.
        query_response = (
            await self.room_services.inquiry_agent_messages_by_related_message_id(
                RoomCenterAgentMessageRequest(related_message_id=room_user_message_id)
            )
        )
        if not query_response.success:
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error=query_response.error,
                status_code=500,
            )

        has_pending_agent_messages = bool(query_response.message_list)

        # Safety net: supervisor-enabled room but no V2 flag and no
        # pre-created agent messages (e.g. @mentions).  This catches genuine
        # bugs where _prepare_for_supervisor_v2 was skipped.
        if not has_pending_agent_messages and user_message:
            room = await self.database_service.get_room_by_room_id(room_id)
            if room and isinstance(room.extend_info, dict) and room.extend_info.get("use_supervisor"):
                logger.error(
                    "RoomMessageCenter: Room %s has use_supervisor=True but user "
                    "message %s lacks supervisor_v2 flag and has no pre-created "
                    "agent messages. Failing instead of silently doing nothing.",
                    room_id,
                    room_user_message_id,
                )
                await self._notify_all_non_terminal_tasks_failed(
                    room_id, room_user_message_id
                )
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.FAILED,
                    message_id=room_user_message_id,
                    lifecycle_message_id=room_user_message_id,
                    details="Supervisor-enabled room missing V2 preparation data",
                )
                return OrchestrationResponse(
                    room_id=room_id,
                    success=False,
                    error="Supervisor V2 data not prepared for this message",
                    status_code=500,
                )

        # Process all agent messages in sequence
        message_queue = (
            deque(query_response.message_list)
            if query_response.message_list is not None
            else deque()
        )

        logger.debug(
            "RoomMessageCenter: Starting to process %d agent messages for room %s and user message %s",
            len(message_queue),
            room_id,
            room_user_message_id,
        )

        # Check for cancellation before processing agent messages
        if token.is_cancelled:
            logger.info(
                "RoomMessageCenter: Processing cancelled for message %s, stopping all processing",
                room_user_message_id,
            )
            # Capture IDs before cancellation for descendant cleanup
            step1_ids = [msg.message_id for msg in message_queue]
            await self.tsm.cancel_remaining_queue(message_queue)
            # Cancel DB-only descendants (step 2, 3, …) downstream in the
            # related_message_id chain from these step-1 messages.
            for mid in step1_ids:
                await self.database_service.cancel_descendants(mid)
            if getattr(self, '_turn_event_appender', None):
                try:
                    await self._turn_event_appender.append(
                        room_id, room_user_message_id, "turn_canceled",
                        {},
                    )
                except Exception:
                    pass
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.CANCELED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
            )
            self.sse_manager.clear_cancellation(room_user_message_id)
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        if hasattr(self.queue_executor, "_turn_event_appender"):
            self.queue_executor._turn_event_appender = getattr(
                self, "_turn_event_appender", None
            )

        queue_processing_result = await self.queue_executor.process_queue(
            message_queue,
            room_id,
            room_user_message_id,
            token=token,
            request_user_id=user_id,
            quoted_text=quoted_text,
        )

        if queue_processing_result.result == QueueResult.FAILED:
            # Emit turn_failed event
            if getattr(self, '_turn_event_appender', None):
                try:
                    await self._turn_event_appender.append(
                        room_id, room_user_message_id, "turn_failed",
                        {"reason": "Queue processing failed", "code": "error"},
                    )
                except Exception:
                    pass
            await self._notify_all_non_terminal_tasks_failed(
                room_id, room_user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="Failed to process agent messages",
            )
            return OrchestrationResponse(
                success=False,
                error="Failed to process agent messages",
                status_code=500,
            )

        if queue_processing_result.result == QueueResult.PAUSED:
            return OrchestrationResponse(
                room_id=room_id, success=True, error=None, status_code=200
            )

        if queue_processing_result.result == QueueResult.CANCELED:
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        # QueueResult.COMPLETED — emit unified summary + completion.
        room = await self.database_service.get_room_by_room_id(room_id)
        is_debate = bool(
            room and isinstance(room.extend_info, dict)
            and room.extend_info.get("debateMode", False)
        )
        await self._emit_unified_summary(
            room_id, room_user_message_id, is_debate=is_debate
        )

        # Emit turn_completed event
        if self._turn_event_appender:
            try:
                await self._turn_event_appender.append(
                    room_id, room_user_message_id, "turn_completed",
                    {"duration_ms": 0},
                )
            except Exception:
                pass

        # Send completion status
        await self._emit_processing_status(
            room_id=room_id,
            status=SSEProcessingStatus.COMPLETED,
            message_id=room_user_message_id,
            lifecycle_message_id=room_user_message_id,
        )

        # Log room memory stats (debug/monitoring)
        await self._log_room_memory_stats(room_id)

        return OrchestrationResponse(
            room_id=room_id, success=True, error=None, status_code=200
        )

    async def _notify_all_non_terminal_tasks_failed(
        self,
        room_id: str,
        user_message_id: str,
    ) -> None:
        """Safety net: send ``task_update`` SSE for every agent message under
        *user_message_id* whose task is still in a non-terminal state.

        Called before terminal ``processing_status`` emits so that individual
        task bubbles in the frontend also transition to their correct final
        state.  The idempotency check inside
        ``notify_task_update`` ensures messages already notified as terminal
        are skipped (no double-notification).
        """
        try:
            agent_messages = (
                await self.database_service
                .get_room_agent_messages_by_related_message_id(user_message_id)
            )
        except Exception:
            logger.exception(
                "RoomMessageCenter: Failed to query agent messages for "
                "safety-net notification on user_message_id=%s",
                user_message_id,
            )
            return

        for msg in agent_messages:
            if not msg.has_task_tracking:
                continue

            task = (
                msg.message_content.message_task
                if msg.message_content
                else None
            )
            if not task or not task.status:
                continue

            state = task.status.state
            if is_terminal_state(state):
                # Already terminal — notify in case the SSE was missed,
                # but idempotency in notify_task_update will skip duplicates.
                try:
                    await self.task_notifications.notify_task_update(
                        message_id=msg.message_id,
                        state=state,
                        room_id=room_id,
                        user_id=msg.user_id or "",
                    )
                except Exception:
                    logger.exception(
                        "RoomMessageCenter: safety-net notify_task_update failed "
                        "for already-terminal message %s",
                        msg.message_id,
                    )
                continue

            # Non-terminal task: transition to failed in DB, then notify.
            # This safety-net path must never block the root terminal
            # processing_status emit; a failed child transition is logged and
            # notification remains best-effort below.
            try:
                await self.tsm.transition_task(
                    msg, CommonTaskState.FAILED, error="Processing failed"
                )
            except Exception:
                logger.exception(
                    "RoomMessageCenter: safety-net transition_task failed "
                    "for message %s",
                    msg.message_id,
                )
            try:
                await self.task_notifications.notify_task_update(
                    message_id=msg.message_id,
                    state=CommonTaskState.FAILED,
                    room_id=room_id,
                    user_id=msg.user_id or "",
                )
            except Exception:
                logger.exception(
                    "RoomMessageCenter: safety-net notify_task_update failed "
                    "for message %s",
                    msg.message_id,
                )

    def _validate_room_message_request(
        self, request: OrchestrationRequest
    ) -> OrchestrationResponse | None:
        """Validate the room message request parameters."""
        if request.room_id is None:
            return OrchestrationResponse(
                success=False,
                error="Room id is required",
                status_code=400,
            )

        if request.room_user_message_id is None:
            return OrchestrationResponse(
                success=False,
                error="Room user message id is required",
                status_code=400,
            )

        return None

    # ------------------------------------------------------------------
    # V2 Supervisor adaptive loop
    # ------------------------------------------------------------------

    async def _process_supervisor_v2(
        self,
        user_message,
        room_id: str,
        room_user_message_id: str,
        user_id: str | None,
        quoted_text: str | None,
        token,
    ) -> OrchestrationResponse:
        """Execute the V2 supervisor adaptive loop for a user message.

        Deserializes ``agent_registry``, ``room_config``, and
        ``conversation_context`` from the user message's ``extend_info``
        (set by ``_prepare_for_supervisor_v2`` in ``RoomServices``), then
        delegates to ``SupervisorExecutor.run()``.

        Also handles clarify-resume: when the user message was prepared by
        ``_prepare_clarify_resume_v2``, the ``extend_info`` contains
        ``supervisor_v2_clarify_resume=True`` and a ``resumed_trajectory``
        that already has ``clarify_user_reply`` set.

        Handles all 5 ``RunStatus`` variants.
        """
        extend = user_message.extend_info
        try:
            agent_registry = [
                AgentProfile(**p) for p in extend["agent_registry"]
            ]
            room_config = RoomConfig(**extend["room_config"])
        except (KeyError, TypeError) as e:
            logger.error(
                "RoomMessageCenter: V2 extend_info missing required keys: %s",
                e,
            )
            await self._notify_all_non_terminal_tasks_failed(
                room_id, room_user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="V2 supervisor data corrupted or incomplete",
            )
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error=f"V2 supervisor data corrupted: {e}",
                status_code=500,
            )
        conversation_context = extend.get("conversation_context")

        # Clarify-resume: deserialize the trajectory from the previous run
        resumed_trajectory = None
        is_clarify_resume = extend.get("supervisor_v2_clarify_resume", False)
        if is_clarify_resume:
            traj_data = extend.get("resumed_trajectory")
            if traj_data:
                try:
                    resumed_trajectory = SupervisorTrajectory(**traj_data)
                    logger.info(
                        "supervisor_v2_clarify_resume_started",
                        extra={
                            "room_id": room_id,
                            "trajectory_id": resumed_trajectory.trajectory_id,
                            "original_message_id": extend.get(
                                "clarify_original_message_id"
                            ),
                            "user_reply_len": len(
                                resumed_trajectory.clarify_user_reply or ""
                            ),
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "RoomMessageCenter: clarify resume trajectory "
                        "deserialization failed: %s — starting fresh run",
                        e,
                    )

        # Carry the original clarify message ID on the trajectory so it
        # survives pause/resume serialization.
        if is_clarify_resume and resumed_trajectory:
            resumed_trajectory.clarify_original_message_id = extend.get(
                "clarify_original_message_id"
            )

        # Crash-recovery resume: if the checkpointed trajectory has
        # status="running" or "recovering" (set by the atomic claim in the
        # stale task checker), a previous server instance crashed mid-loop.
        # Resume from the checkpoint instead of starting fresh.
        if resumed_trajectory is None and not is_clarify_resume:
            checkpoint_data = extend.get("supervisor_trajectory")
            if isinstance(checkpoint_data, dict) and checkpoint_data.get("status") in (
                TrajectoryStatus.RUNNING, TrajectoryStatus.RECOVERING,
            ):
                try:
                    resumed_trajectory = SupervisorTrajectory(**checkpoint_data)
                    logger.info(
                        "supervisor_v2_crash_recovery_started",
                        extra={
                            "room_id": room_id,
                            "trajectory_id": resumed_trajectory.trajectory_id,
                            "checkpointed_steps": len(resumed_trajectory.entries),
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "RoomMessageCenter: crash-recovery trajectory "
                        "deserialization failed: %s — starting fresh run",
                        e,
                    )

        try:
            build_turn_content = self.build_turn_content or (
                lambda text, _attachments: text
            )
            message_text = build_turn_content(
                user_message.message_content.message_text or "",
                user_message.message_content.attachments,
            )
            result = await self.supervisor_executor.run(
                room_id=room_id,
                user_message_id=room_user_message_id,
                message_text=message_text,
                agent_registry=agent_registry,
                room_config=room_config,
                conversation_context=conversation_context,
                token=token,
                request_user_id=user_id,
                quoted_text=quoted_text,
                resumed_trajectory=resumed_trajectory,
                user_message=user_message,
            )
        except self.supervisor_planning_error_cls:
            if is_clarify_resume and resumed_trajectory:
                original_msg_id = extend.get("clarify_original_message_id")
                if original_msg_id:
                    logger.warning(
                        "RoomMessageCenter: clarify-resume decide_next failed "
                        "for %s — restoring pending clarification on room %s",
                        room_user_message_id,
                        room_id,
                    )
                    resumed_trajectory.status = TrajectoryStatus.CLARIFYING
                    resumed_trajectory.clarify_user_reply = None
                    room = await self.database_service.get_room_by_room_id(room_id)
                    if room:
                        if room.extend_info is None:
                            room.extend_info = {}
                        room.extend_info["pending_clarification_message_id"] = (
                            original_msg_id
                        )
                        await self.database_service.update_room_by_room_id(
                            room_id, room
                        )
                    # Persist the restored trajectory on the original message
                    # so the DB status is consistent with the room state.
                    try:
                        orig_msg = await self.database_service.get_room_user_message_by_message_id(
                            original_msg_id
                        )
                        if orig_msg and isinstance(orig_msg.extend_info, dict):
                            orig_msg.extend_info["supervisor_trajectory"] = (
                                resumed_trajectory.model_dump(mode="json")
                            )
                            await self.database_service.update_room_user_message_by_message_id(
                                original_msg_id, orig_msg
                            )
                    except Exception as persist_err:
                        logger.warning(
                            "RoomMessageCenter: failed to persist restored clarify "
                            "trajectory on %s: %s",
                            original_msg_id,
                            persist_err,
                        )
                    self.sse_manager.remove_token(room_user_message_id)
                    await self._emit_processing_status(
                        room_id=room_id,
                        status=SSEProcessingStatus.COMPLETED,
                        message_id=room_user_message_id,
                        lifecycle_message_id=room_user_message_id,
                        details="Clarify resume failed — please answer the clarification question again",
                        record_lifecycle=False,
                    )
                    return OrchestrationResponse(
                        room_id=room_id,
                        success=False,
                        error="Clarify resume supervisor call failed",
                        status_code=500,
                    )

            logger.error(
                "RoomMessageCenter: Supervisor first decide_next failed for %s",
                room_user_message_id,
            )
            # Persist a failed trajectory so the recovery job doesn't retry.
            await self._persist_failed_trajectory(
                user_message, room_user_message_id, resumed_trajectory,
            )
            try:
                await self.room_coordinator_service.emit_synthesis_message(
                    room_id=room_id,
                    room_user_message_id=room_user_message_id,
                    synthesis_text=(
                        "Sorry, I was unable to process your request. "
                        "The supervisor encountered an error while planning. "
                        "Please try again."
                    ),
                    coordinator_agent_id=CoordinatorAgentId.SUPERVISOR_ERROR,
                )
            except Exception as emit_err:
                logger.warning(
                    "RoomMessageCenter: Failed to emit planning error message: %s",
                    emit_err,
                )
            self.sse_manager.remove_token(room_user_message_id)
            await self._notify_all_non_terminal_tasks_failed(
                room_id, room_user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="Supervisor planning failed",
            )
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error="Supervisor planning failed",
                status_code=500,
            )
        except Exception:
            logger.exception(
                "RoomMessageCenter: Unhandled error in supervisor_executor.run "
                "for message %s",
                room_user_message_id,
            )
            if resumed_trajectory and resumed_trajectory.status == TrajectoryStatus.RUNNING:
                resumed_trajectory.status = TrajectoryStatus.FAILED
            # Persist the failed trajectory so the recovery job
            # (_recover_stuck_supervisor_trajectories) doesn't endlessly
            # retry a permanently-broken execution.
            await self._persist_failed_trajectory(
                user_message, room_user_message_id, resumed_trajectory,
            )
            self.sse_manager.remove_token(room_user_message_id)
            await self._notify_all_non_terminal_tasks_failed(
                room_id, room_user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="Supervisor execution failed unexpectedly",
            )
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error="Supervisor execution failed unexpectedly",
                status_code=500,
            )

        # Persist trajectory + handle SSE/synthesis
        await self._handle_v2_run_result(
            result=result,
            room_id=room_id,
            user_message_id=room_user_message_id,
            original_clarify_message_id=result.trajectory.clarify_original_message_id,
            user_message=user_message,
        )

        await self._log_room_memory_stats(room_id)

        is_failure = result.status in (RunStatus.FAILED,)
        return OrchestrationResponse(
            room_id=room_id,
            success=not is_failure,
            error="Supervisor V2 execution failed" if is_failure else None,
            status_code=500 if is_failure else 200,
        )

    # ------------------------------------------------------------------
    # V2 Supervisor resume (push notification webhook)
    # ------------------------------------------------------------------

    async def _resume_supervisor_v2(
        self,
        continuation: dict,
        paused_message_id: str,
        task_result_text: str | None,
    ) -> RunStatus:
        """Resume a V2 supervisor loop after an interrupt.

        Handles all three interrupt kinds:
        - PUSH_NOTIFICATION: webhook result appended to trajectory
        - HITL_AGENT: webhook result appended (same as push notification)
        - HITL_SUPERVISOR: user reply injected into conversation context

        Steps:
        1. Deserialize the trajectory from continuation data.
        2. Branch on interrupt_kind.
        3. Refresh agent registry and conversation context.
        4. Call SupervisorExecutor.run(resumed_trajectory=...).
        5. Handle the RunStatus result.
        """
        from models.hitl import InterruptKind
        from models.supervisor_v2 import (
            SupervisorTrajectory,
        )

        room_id = continuation.get("room_id")
        user_message_id = continuation.get("user_message_id")
        message_text = continuation.get("message_text", "")
        request_user_id = continuation.get("request_user_id")
        conversation_context = continuation.get("conversation_context")

        # Reload quoted text from DB (QUOTE_REPLY: prefer TurnContext over continuation)
        quoted_text: str | None = None
        if user_message_id:
            from execution.orchestration.turn_context import (
                TurnQuoteMissingError,
                load_turn_context,
            )

            um = await self.database_service.get_room_user_message_by_message_id(
                user_message_id
            )
            if um:
                try:
                    _tc = await load_turn_context(self.database_service, um)
                    quoted_text = _tc.quoted_text
                except TurnQuoteMissingError:
                    logger.error(
                        "RoomMessageCenter: V2 resume missing quoted snippet for turn %s",
                        user_message_id,
                    )
        if quoted_text is None:
            quoted_text = continuation.get("quoted_text")

        if not room_id or not user_message_id:
            logger.error(
                "RoomMessageCenter: V2 resume missing room_id or user_message_id "
                "in continuation. message_id=%s",
                paused_message_id,
            )
            return RunStatus.FAILED

        # 1. Deserialize trajectory
        try:
            trajectory = SupervisorTrajectory(
                **continuation["trajectory"]
            )
        except (KeyError, TypeError) as e:
            logger.error(
                "RoomMessageCenter: V2 resume failed to deserialize trajectory: %s",
                e,
            )
            await self._notify_all_non_terminal_tasks_failed(
                room_id, user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
                details="V2 resume: corrupted trajectory data",
            )
            return RunStatus.FAILED

        # 2. Read and validate interrupt_kind
        raw_kind = continuation.get("interrupt_kind", "push_notification")
        try:
            interrupt_kind = InterruptKind(raw_kind)
        except ValueError:
            logger.error(
                "Unknown interrupt_kind=%r in continuation for message %s — "
                "defaulting to PUSH_NOTIFICATION",
                raw_kind,
                paused_message_id,
            )
            interrupt_kind = InterruptKind.PUSH_NOTIFICATION

        logger.info(
            "supervisor_v2_resume_started",
            extra={
                "room_id": room_id,
                "trajectory_id": trajectory.trajectory_id,
                "paused_message_id": paused_message_id,
                "user_message_id": user_message_id,
                "interrupt_kind": interrupt_kind.value,
            },
        )

        # 3. Branch on interrupt_kind
        if interrupt_kind in (
            InterruptKind.PUSH_NOTIFICATION,
            InterruptKind.HITL_AGENT,
        ):
            # Identify the paused agent before appending result
            paused_agent_id: str | None = None
            paused_agent_name: str | None = None
            if task_result_text:
                paused_agent_id, paused_agent_name = self._find_paused_agent(
                    trajectory, paused_message_id
                )

            # Append the webhook result to the matching trajectory entry
            self._append_paused_result_to_trajectory(
                trajectory,
                paused_message_id=paused_message_id,
                task_result_text=task_result_text,
            )

            # Add completed agent response to room memory
            if task_result_text and paused_agent_id:
                await self.room_memory_service.add_agent_response_to_memory(
                    room_id=room_id,
                    agent_id=paused_agent_id,
                    agent_name=paused_agent_name or "Agent",
                    response_text=task_result_text,
                    was_successful=True,
                    message_id=paused_message_id,
                )

        elif interrupt_kind == InterruptKind.HITL_SUPERVISOR:
            # User reply is already patched onto trajectory by
            # HITLService._handle_supervisor_response(). Re-fetch
            # conversation_context to avoid staleness.
            try:
                room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
                if room_memory and self.context_assembly_service is not None:
                    room_tmp = await self.database_service.get_room_by_room_id(room_id)
                    agent_dicts = [
                        {"agent_id": aid, "agent_name": aname}
                        for aid, aname in ((room_tmp.room_agent_set or {}).items() if room_tmp else [])
                    ]
                    memory_search_results = None
                    if self.memory_search_service is not None:
                        try:
                            search_response = await self.memory_search_service.search(
                                query=message_text, room_id=room_id,
                            )
                            if search_response.results:
                                memory_search_results = search_response.results
                        except Exception:
                            pass
                    result_ctx = self.context_assembly_service.build_supervisor_context(
                        room_memory=room_memory,
                        current_task=message_text,
                        agent_registry=agent_dicts,
                        max_turns=5,
                        memory_search_results=memory_search_results,
                    )
                    conversation_context = result_ctx.context
            except Exception as e:
                logger.warning(
                    "supervisor_v2_resume: failed to refresh conversation_context "
                    "for %s (HITL_SUPERVISOR), using serialized fallback: %s",
                    room_id, e,
                )

            # Inject the HITL user reply into conversation context
            effective_reply = (
                trajectory.hitl_user_reply
                or trajectory.clarify_user_reply
            )
            if effective_reply:
                original_question = self._extract_clarify_question(trajectory)
                hitl_block = ""
                if original_question:
                    hitl_block += f"[Supervisor asked the user]: {original_question}\n"
                hitl_block += f"[User replied]: {effective_reply}"
                conversation_context = (
                    f"{conversation_context or ''}\n\n{hitl_block}"
                ).strip()

        # 5. Refresh agent registry from database (not serialized)
        room = await self.database_service.get_room_by_room_id(room_id)
        if not room:
            logger.error(
                "RoomMessageCenter: V2 resume room not found: %s", room_id
            )
            await self._notify_all_non_terminal_tasks_failed(
                room_id, user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
                details="V2 resume: room not found",
            )
            return RunStatus.FAILED

        # 5b. Refresh conversation_context from room memory (§7.6).
        # The serialized context may be stale after a push-notification pause
        # (compaction, new messages, etc.). Rebuild via ContextAssemblyService
        # with the same logic used in _prepare_for_supervisor_v2 / _prepare_clarify_resume_v2.
        # SKIP for HITL_SUPERVISOR: that branch already refreshes context and
        # appends the hitl_block — a second refresh would overwrite the user's reply.
        if interrupt_kind != InterruptKind.HITL_SUPERVISOR:
            try:
                room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
                if room_memory and self.context_assembly_service is not None:
                    agent_dicts = [
                        {"agent_id": aid, "agent_name": aname}
                        for aid, aname in (room.room_agent_set or {}).items()
                    ]
                    memory_search_results = None
                    if self.memory_search_service is not None:
                        try:
                            search_response = await self.memory_search_service.search(
                                query=message_text,
                                room_id=room_id,
                            )
                            if search_response.results:
                                memory_search_results = search_response.results
                        except Exception as search_err:
                            logger.debug(
                                "supervisor_v2_resume: memory search skipped: %s",
                                search_err,
                            )
                    result_ctx = self.context_assembly_service.build_supervisor_context(
                        room_memory=room_memory,
                        current_task=message_text,
                        agent_registry=agent_dicts,
                        max_turns=5,
                        memory_search_results=memory_search_results,
                    )
                    conversation_context = result_ctx.context
                    logger.debug(
                        "supervisor_v2_resume: refreshed conversation_context for %s "
                        "(occupancy=%.1f%%)",
                        room_id, result_ctx.occupancy_pct,
                    )
            except Exception as e:
                logger.warning(
                    "supervisor_v2_resume: failed to refresh conversation_context "
                    "for %s, using serialized fallback: %s",
                    room_id, e,
                )

        agent_registry: list[AgentProfile] = []
        room_agent_items = list((room.room_agent_set or {}).items())
        if room_agent_items:
            agents = await asyncio.gather(
                *(
                    self.database_service.get_agent_by_agent_id(aid)
                    for aid, _ in room_agent_items
                )
            )
            for (aid, aname), agent in zip(room_agent_items, agents, strict=True):
                if agent:
                    agent_registry.append(AgentProfile.from_agent(agent))
                else:
                    agent_registry.append(
                        AgentProfile(
                            agent_id=aid,
                            agent_name=aname,
                            description="",
                            is_healthy=False,
                        )
                    )

        if not agent_registry:
            # Fall back to serialized registry if DB refresh yields nothing
            try:
                agent_registry = [
                    AgentProfile(**p)
                    for p in continuation.get("agent_registry", [])
                ]
            except (TypeError, KeyError) as e:
                logger.warning(
                    "RoomMessageCenter: V2 resume fallback registry failed: %s", e
                )

        # Use serialized room_config from continuation as the base (preserves
        # all fields), then selectively refresh fields that may have changed
        # while execution was paused.
        try:
            room_config = RoomConfig(**continuation.get("room_config", {}))
        except (TypeError, KeyError):
            room_config = RoomConfig()
        room_config.is_debate_mode = bool(
            room.extend_info.get("debateMode", False)
            if isinstance(room.extend_info, dict)
            else False
        )
        room_config.room_agent_set = room.room_agent_set or {}

        # --- Debate participant preservation ---
        # If this is a debate resume, ensure all original debate participants
        # are present in agent_registry even if they were removed from the room
        # during the pause. This prevents participant drift.
        if (
            trajectory.debate_agent_ids
            and room_config.is_debate_mode
        ):
            current_ids = {a.agent_id for a in agent_registry}
            missing_ids = [
                aid for aid in trajectory.debate_agent_ids
                if aid not in current_ids
            ]
            if missing_ids:
                serialized_registry = continuation.get("agent_registry", [])
                serialized_map = {
                    p["agent_id"]: p for p in serialized_registry
                    if isinstance(p, dict) and "agent_id" in p
                }
                for mid in missing_ids:
                    if mid in serialized_map:
                        try:
                            agent_registry.append(AgentProfile(**serialized_map[mid]))
                        except (TypeError, KeyError):
                            pass
                    # If not in serialized registry either, executor will
                    # create a FAILED entry and skip (unhealthy agent path).
                logger.info(
                    "supervisor_v2_resume: merged %d debate participants from continuation",
                    len(missing_ids),
                )

        # 6. Create/reuse cancellation token
        token = self.sse_manager.get_token(user_message_id)
        if token is None:
            token = self.sse_manager.create_token(user_message_id)

        # Guard: if the request was already canceled during the pause,
        # don't restart the loop.
        if token.is_cancelled:
            logger.info(
                "supervisor_v2_resume_already_canceled",
                extra={
                    "room_id": room_id,
                    "user_message_id": user_message_id,
                    "paused_message_id": paused_message_id,
                },
            )
            if getattr(self, '_turn_event_appender', None):
                try:
                    await self._turn_event_appender.append(
                        room_id, user_message_id, "turn_canceled",
                        {},
                    )
                except Exception:
                    pass
            await self._notify_all_non_terminal_tasks_failed(
                room_id, user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.CANCELED,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
            )
            self.sse_manager.clear_cancellation(user_message_id)
            return RunStatus.CANCELED

        # 7. Resume the supervisor loop
        try:
            result = await self.supervisor_executor.run(
                room_id=room_id,
                user_message_id=user_message_id,
                message_text=message_text,
                agent_registry=agent_registry,
                room_config=room_config,
                conversation_context=conversation_context,
                token=token,
                request_user_id=request_user_id,
                quoted_text=quoted_text,
                resumed_trajectory=trajectory,
            )
        except Exception:
            logger.exception(
                "RoomMessageCenter: V2 resume supervisor_executor.run() failed"
            )
            await self._notify_all_non_terminal_tasks_failed(
                room_id, user_message_id
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
                details="V2 resume: executor failed",
            )
            return RunStatus.FAILED

        # 8. Handle the result
        await self._handle_v2_run_result(
            result=result,
            room_id=room_id,
            user_message_id=user_message_id,
            room=room,
            original_clarify_message_id=result.trajectory.clarify_original_message_id,
        )

        await self._log_room_memory_stats(room_id)

        logger.info(
            "supervisor_v2_resume_completed",
            extra={
                "room_id": room_id,
                "trajectory_id": trajectory.trajectory_id,
                "status": result.status,
            },
        )
        return result.status

    @staticmethod
    def _append_paused_result_to_trajectory(
        trajectory: SupervisorTrajectory,
        paused_message_id: str,
        task_result_text: str | None,
    ) -> None:
        """Replace the PAUSED ``V2StepResult`` with a completed one carrying
        the push notification response.

        PAUSED results are now preserved in the serialized trajectory (with
        ``status=PAUSED`` and ``agent_message_id`` set).  We find the exact
        result by matching ``agent_message_id == paused_message_id`` and
        replace it in-place, which is correct even when multiple agents in
        the same multi-target DELEGATE are paused.
        """
        from common.utils.time import utcnow
        from models.supervisor_v2 import StepStatus, V2StepResult

        for entry in trajectory.entries:
            for idx, result in enumerate(entry.results):
                if (
                    result.status in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT)
                    and result.agent_message_id == paused_message_id
                ):
                    entry.results[idx] = V2StepResult(
                        step_number=entry.step_number,
                        agent_id=result.agent_id,
                        agent_name=result.agent_name,
                        task=result.task,
                        response_text=task_result_text or "",
                        success=bool(task_result_text),
                        status=(
                            StepStatus.SUCCESS
                            if task_result_text
                            else StepStatus.FAILED
                        ),
                        error_message=(
                            None
                            if task_result_text
                            else "No result from push notification"
                        ),
                        agent_message_id=paused_message_id,
                        completed_at=utcnow(),
                    )
                    # Mark entry completed if no more PAUSED/AWAITING results remain
                    still_paused = any(
                        r.status in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT)
                        for r in entry.results
                    )
                    if not still_paused and entry.completed_at is None:
                        entry.completed_at = utcnow()

                    logger.info(
                        "supervisor_v2_resume_paused_result_replaced",
                        extra={
                            "trajectory_id": trajectory.trajectory_id,
                            "step_number": entry.step_number,
                            "agent_id": result.agent_id,
                            "paused_message_id": paused_message_id,
                            "success": bool(task_result_text),
                        },
                    )
                    return

        logger.warning(
            "supervisor_v2_resume_no_matching_paused_result: could not find a "
            "PAUSED V2StepResult with agent_message_id=%s. "
            "The push notification result will be visible to the supervisor "
            "only via room memory.",
            paused_message_id,
        )

    @staticmethod
    def _find_paused_agent(
        trajectory: SupervisorTrajectory,
        paused_message_id: str,
    ) -> tuple[str | None, str | None]:
        """Return (agent_id, agent_name) for the agent that was paused.

        Matches by ``agent_message_id`` on PAUSED results (which are now
        preserved in the serialized trajectory).  Returns ``(None, None)``
        if not found.
        """
        for entry in trajectory.entries:
            for result in entry.results:
                if (
                    result.status in (StepStatus.PAUSED, StepStatus.AWAITING_INPUT)
                    and result.agent_message_id == paused_message_id
                ):
                    return result.agent_id, result.agent_name
        return None, None

    @staticmethod
    def _extract_clarify_question(
        trajectory: SupervisorTrajectory,
    ) -> str | None:
        """Extract the CLARIFY question from the last CLARIFY trajectory entry."""
        from models.supervisor_v2 import ActionType

        for entry in reversed(trajectory.entries):
            if entry.action.action == ActionType.CLARIFY:
                return entry.action.clarification_question
        return None

    async def _persist_failed_trajectory(
        self,
        user_message,
        user_message_id: str,
        trajectory: SupervisorTrajectory | None,
    ) -> None:
        """Best-effort: mark a trajectory as failed in the DB so the recovery
        job (``_recover_stuck_supervisor_trajectories``) does not retry it."""
        try:
            msg = user_message
            if msg is None:
                msg = await self.database_service.get_room_user_message_by_message_id(
                    user_message_id
                )
            if msg and isinstance(msg.extend_info, dict):
                traj_data = msg.extend_info.get("supervisor_trajectory")
                if isinstance(traj_data, dict) and traj_data.get("status") == TrajectoryStatus.RUNNING:
                    traj_data["status"] = TrajectoryStatus.FAILED
                elif trajectory is not None:
                    trajectory.status = TrajectoryStatus.FAILED
                    msg.extend_info["supervisor_trajectory"] = (
                        trajectory.model_dump(mode="json")
                    )
                await self.database_service.update_room_user_message_by_message_id(
                    user_message_id, msg
                )
        except Exception as e:
            logger.warning(
                "RoomMessageCenter: failed to persist failed trajectory for %s: %s",
                user_message_id,
                e,
            )

    async def _handle_v2_run_result(
        self,
        result: SupervisorRunResult,
        room_id: str,
        user_message_id: str,
        room=None,
        original_clarify_message_id: str | None = None,
        user_message=None,
    ) -> None:
        """Persist trajectory and emit SSE/synthesis for a V2 run result.

        Shared by ``_process_supervisor_v2`` and ``_resume_supervisor_v2``.

        When ``original_clarify_message_id`` is set (clarify-resume path),
        the original message's trajectory is also updated so it doesn't stay
        permanently in ``"clarifying"`` status.
        """
        if user_message is None:
            user_message = (
                await self.database_service.get_room_user_message_by_message_id(
                    user_message_id
                )
            )
        if user_message and result.status in (
            RunStatus.COMPLETED,
            RunStatus.CLARIFYING,
            RunStatus.AWAITING_INPUT,
            RunStatus.FAILED,
            RunStatus.CANCELED,
            RunStatus.PAUSED,
        ):
            if not isinstance(user_message.extend_info, dict):
                user_message.extend_info = {}
            user_message.extend_info["supervisor_trajectory"] = (
                result.trajectory.model_dump(mode="json")
            )
            await self.database_service.update_room_user_message_by_message_id(
                user_message_id, user_message
            )

        # Update the original clarify message's trajectory so it doesn't
        # stay in "clarifying" status forever.
        if original_clarify_message_id and original_clarify_message_id != user_message_id:
            try:
                orig_msg = (
                    await self.database_service.get_room_user_message_by_message_id(
                        original_clarify_message_id
                    )
                )
                if orig_msg and isinstance(orig_msg.extend_info, dict):
                    orig_traj = orig_msg.extend_info.get("supervisor_trajectory")
                    if isinstance(orig_traj, dict):
                        orig_traj["status"] = result.trajectory.status
                        await self.database_service.update_room_user_message_by_message_id(
                            original_clarify_message_id, orig_msg
                        )
            except Exception as e:
                logger.warning(
                    "RoomMessageCenter: failed to update original clarify "
                    "message %s trajectory status: %s",
                    original_clarify_message_id,
                    e,
                )

        await self._run_v2_terminal_post_loop_integration(result, room_id)

        match result.status:
            case RunStatus.COMPLETED:
                from models.supervisor_v2 import ActionType  # noqa: PLC0415

                trajectory_responses = [
                    {"agent_name": step.agent_name, "message": step.response_text}
                    for entry in result.trajectory.entries
                    if entry.action.action == ActionType.DELEGATE
                    for step in entry.results
                    if step.success and step.response_text
                ]
                is_debate = bool(
                    room and isinstance(room.extend_info, dict)
                    and room.extend_info.get("debateMode", False)
                ) if room else False

                # Only emit a summary when the supervisor explicitly chose
                # SYNTHESIZE (synthesis_text is set).  When the supervisor chose
                # DONE it means individual agent responses are sufficient.
                if result.synthesis_text is not None:
                    await self._emit_unified_summary(
                        room_id,
                        user_message_id,
                        synthesis_text=result.synthesis_text,
                        trajectory_responses=trajectory_responses,
                        is_debate=is_debate,
                        working_already_emitted=True,
                    )
                elif len(trajectory_responses) >= 2:
                    await self._emit_deterministic_digest(
                        room_id,
                        user_message_id,
                        agent_count=len(trajectory_responses),
                    )
                # Emit turn_completed event
                if getattr(self, '_turn_event_appender', None):
                    try:
                        await self._turn_event_appender.append(
                            room_id, user_message_id, "turn_completed",
                            {"duration_ms": 0},
                        )
                    except Exception:
                        pass
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.COMPLETED,
                    message_id=user_message_id,
                    lifecycle_message_id=user_message_id,
                )

            case RunStatus.PAUSED:
                pass

            case RunStatus.AWAITING_INPUT:
                pass  # Continuation already saved; HITLService emits the SSE event.
                      # Token stays alive — resume path creates/reuses it.

            case RunStatus.CLARIFYING:
                if room is None:
                    room = await self.database_service.get_room_by_room_id(room_id)
                if room:
                    if room.extend_info is None:
                        room.extend_info = {}
                    room.extend_info["pending_clarification_message_id"] = (
                        user_message_id
                    )
                    await self.database_service.update_room_by_room_id(
                        room_id, room
                    )
                if result.clarification_question:
                    try:
                        await self.room_coordinator_service.emit_synthesis_message(
                            room_id=room_id,
                            room_user_message_id=user_message_id,
                            synthesis_text=result.clarification_question,
                            coordinator_agent_id=CoordinatorAgentId.SUPERVISOR_CLARIFY,
                        )
                    except Exception as e:
                        logger.error(
                            "RoomMessageCenter: V2 clarification emission failed: %s",
                            e,
                            exc_info=True,
                        )
                # Emit turn_completed event (CLARIFYING is a soft complete)
                if getattr(self, '_turn_event_appender', None):
                    try:
                        await self._turn_event_appender.append(
                            room_id, user_message_id, "turn_completed",
                            {"duration_ms": 0},
                        )
                    except Exception:
                        pass
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.COMPLETED,
                    message_id=user_message_id,
                    lifecycle_message_id=user_message_id,
                    record_lifecycle=False,
                )

            case RunStatus.CANCELED:
                canceled_parent_ids: list[str] = []
                for entry in result.trajectory.entries:
                    for step_result in entry.results:
                        if step_result.agent_message_id:
                            canceled_parent_ids.append(step_result.agent_message_id)
                            await self.database_service.cancel_descendants(
                                step_result.agent_message_id
                            )
                if canceled_parent_ids:
                    await self.database_service.cancel_agent_messages_by_ids(
                        canceled_parent_ids
                    )
                # Emit turn_canceled event
                if getattr(self, '_turn_event_appender', None):
                    try:
                        await self._turn_event_appender.append(
                            room_id, user_message_id, "turn_canceled",
                            {},
                        )
                    except Exception:
                        pass
                await self._notify_all_non_terminal_tasks_failed(
                    room_id, user_message_id
                )
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.CANCELED,
                    message_id=user_message_id,
                    lifecycle_message_id=user_message_id,
                )
                self.sse_manager.clear_cancellation(user_message_id)

            case RunStatus.FAILED:
                failed_parent_ids: list[str] = []
                for entry in result.trajectory.entries:
                    for step_result in entry.results:
                        if step_result.agent_message_id:
                            failed_parent_ids.append(step_result.agent_message_id)
                            await self.database_service.cancel_descendants(
                                step_result.agent_message_id
                            )
                if failed_parent_ids:
                    await self.database_service.cancel_agent_messages_by_ids(
                        failed_parent_ids
                    )
                # Emit turn_failed event
                if getattr(self, '_turn_event_appender', None):
                    try:
                        await self._turn_event_appender.append(
                            room_id, user_message_id, "turn_failed",
                            {"reason": "V2 supervisor execution failed", "code": "error"},
                        )
                    except Exception:
                        pass
                await self._notify_all_non_terminal_tasks_failed(
                    room_id, user_message_id
                )
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.FAILED,
                    message_id=user_message_id,
                    lifecycle_message_id=user_message_id,
                    details="V2 supervisor execution failed",
                )

        # Terminal run state is persisted via run_command_handler / runs; no room mirror write.

        # Clean up cancellation token for all terminal statuses.
        # PAUSED and AWAITING_INPUT runs keep their token alive — the
        # webhook/HITL resume path will create/reuse it.
        if result.status not in (RunStatus.PAUSED, RunStatus.AWAITING_INPUT):
            self.sse_manager.remove_token(user_message_id)

    async def _run_v2_terminal_post_loop_integration(
        self,
        result: SupervisorRunResult,
        room_id: str,
    ) -> None:
        # --- Post-loop integration (§11.3): synthesis, room summary, compaction ---
        terminal_statuses = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED)
        if result.status in terminal_statuses:
            # Add synthesis text to room memory history
            if result.status == RunStatus.COMPLETED and result.synthesis_text:
                try:
                    synthesis_turn_id = await self.room_memory_service.add_synthesis_to_history(
                        room_id=room_id,
                        synthesis_text=result.synthesis_text,
                        trajectory=result.trajectory,
                    )
                    if synthesis_turn_id:
                        # Summary update and compaction are now safe to run
                        # in any order — they write to disjoint MongoDB fields
                        # after the Layer A atomic-operator migration.
                        await self._update_room_summary_safe(
                            room_id, result.synthesis_text, synthesis_turn_id
                        )
                except Exception as e:
                    logger.warning(
                        "RoomMessageCenter: Failed to add synthesis to history: %s", e
                    )

            # Inline await: compaction MUST run while per-room lock is held (§6.9).
            # A fire-and-forget task would race with the next message's writes.
            await self._trigger_compaction_safe(room_id)

    # ------------------------------------------------------------------
    # Post-loop helpers
    # ------------------------------------------------------------------

    async def _update_room_summary_safe(
        self, room_id: str, synthesis_text: str, synthesis_turn_id: str | None = None
    ) -> None:
        """Wrapper for room summary update (§9). Awaited before compaction."""
        try:
            await self.room_memory_service.update_room_summary(
                room_id=room_id,
                synthesis_text=synthesis_text,
                synthesis_turn_id=synthesis_turn_id,
            )
        except Exception as e:
            logger.warning(
                "RoomMessageCenter: Background room summary update failed for %s: %s",
                room_id, e,
            )

    async def _trigger_compaction_safe(self, room_id: str) -> None:
        """Wrapper for compaction trigger (§6.5). Awaited within per-room lock."""
        try:
            if self.compaction_service is not None:
                await self.compaction_service.compact_if_needed(room_id)
        except Exception as e:
            logger.warning(
                "RoomMessageCenter: Background compaction failed for %s: %s",
                room_id, e,
            )

    # ------------------------------------------------------------------
    # Webhook resume (thin wrapper around QueueExecutor)
    # ------------------------------------------------------------------

    async def resume_queue_from_continuation(
        self,
        message_id: str,
        task_result_text: str | None = None,
        *,
        failed: bool = False,
    ) -> bool:
        """Resume queue processing after a push notification task completes.

        Delegates the actual queue mechanics to ``QueueExecutor`` (V1) or
        ``SupervisorExecutor`` (V2) depending on the continuation data.

        For V2 supervisor rooms, the continuation data contains
        ``supervisor_v2: True``. The V2 resume path reconstructs the
        trajectory, refreshes the agent registry, and resumes the adaptive
        loop via ``_resume_supervisor_v2``.

        Args:
            message_id: The agent message ID whose continuation to resume.
            task_result_text: Text result from the completed task (None on failure).
            failed: If True, the step that triggered the resume failed. The
                orchestrator should treat this as an error rather than a
                successful response.

        Returns ``True`` if the queue was resumed successfully.
        """
        if failed and task_result_text is None:
            task_result_text = ""

        # Peek at the continuation data to detect V2 before QueueExecutor
        # consumes it (get_and_clear is destructive). The V2 flag is checked
        # first; if present, we handle it here instead of delegating to the
        # V1 QueueExecutor path.
        continuation = (
            await self.database_service.get_and_clear_continuation_on_message(
                message_id
            )
        )
        # Also check user messages (HITL_SUPERVISOR stores continuations there)
        if not continuation:
            continuation = (
                await self.database_service.get_and_clear_continuation_on_user_message(
                    message_id
                )
            )
        if not continuation:
            logger.debug(
                "RoomMessageCenter: No continuation found for message %s",
                message_id,
            )
            return False

        # ----- Per-room lock: serialise resume within the same room -----
        room_id = continuation.get("room_id")
        lock_acquired_at: float | None = None
        if room_id:
            lock_owner = await self._acquire_room_lock(room_id)
            lock_acquired_at = time.monotonic()
            if lock_owner is None:
                logger.error(
                    "RoomMessageCenter: Timed out waiting for room lock on %s "
                    "during resume (message %s). Re-saving continuation.",
                    room_id,
                    message_id,
                )
                # Re-save so the continuation isn't lost.
                # HITL_SUPERVISOR continuations are stored on user messages,
                # not agent messages — use the correct collection.
                from models.hitl import InterruptKind
                interrupt_kind_raw = continuation.get("interrupt_kind")
                if interrupt_kind_raw == InterruptKind.HITL_SUPERVISOR.value:
                    await self.database_service.save_continuation_on_user_message(
                        message_id, continuation
                    )
                else:
                    await self.database_service.save_continuation_on_message(
                        message_id, continuation
                    )
                return False
        else:
            lock_owner = None
            logger.warning(
                "RoomMessageCenter: continuation for message %s has no room_id — "
                "cannot acquire per-room lock; proceeding without serialisation",
                message_id,
            )

        try:
            return await self._resume_continuation_locked(
                continuation, message_id, task_result_text
            )
        finally:
            if room_id is not None:
                await self._release_room_lock(room_id, lock_owner, acquired_at=lock_acquired_at)

    async def _resume_continuation_locked(
        self,
        continuation: dict,
        message_id: str,
        task_result_text: str | None,
    ) -> bool:
        """Inner resume path — caller MUST hold the per-room lock (if available)."""

        if continuation.get("supervisor_v2"):
            # Re-save continuation before attempting resume so a process
            # crash mid-resume doesn't permanently lose the execution state.
            # Use the correct collection based on interrupt_kind.
            interrupt_kind = continuation.get("interrupt_kind", "push_notification")
            if interrupt_kind == "hitl_supervisor":
                await self.database_service.save_continuation_on_user_message(
                    message_id, continuation
                )
            else:
                await self.database_service.save_continuation_on_message(
                    message_id, continuation
                )
            try:
                resume_status = await self._resume_supervisor_v2(
                    continuation, message_id, task_result_text
                )
                # Only clear the old continuation if the supervisor loop
                # actually finished.  When it re-interrupted (e.g. a second
                # HITL CLARIFY), SupervisorExecutor saved a fresh continuation
                # that must NOT be cleared.
                if resume_status not in (
                    RunStatus.AWAITING_INPUT,
                    RunStatus.PAUSED,
                ):
                    if interrupt_kind == "hitl_supervisor":
                        await self.database_service.get_and_clear_continuation_on_user_message(
                            message_id
                        )
                    else:
                        await self.database_service.get_and_clear_continuation_on_message(
                            message_id
                        )
                return resume_status != RunStatus.FAILED
            except Exception:
                logger.exception(
                    "RoomMessageCenter: V2 resume failed — continuation preserved "
                    "for message %s so it can be retried",
                    message_id,
                )
                return False

        # V1 path: re-save the continuation so QueueExecutor can read it
        # (we already consumed it with get_and_clear above).
        await self.database_service.save_continuation_on_message(
            message_id, continuation
        )

        result = await self.queue_executor.resume_from_continuation(
            message_id,
            task_result_text,
            before_terminal_failure=self._notify_all_non_terminal_tasks_failed,
        )

        if not result.success:
            return False

        if result.needs_completion and result.room_id and result.user_message_id:
            room = await self.database_service.get_room_by_room_id(result.room_id)
            is_debate = bool(
                room and isinstance(room.extend_info, dict)
                and room.extend_info.get("debateMode", False)
            )
            await self._emit_unified_summary(
                result.room_id, result.user_message_id, is_debate=is_debate
            )
            if getattr(self, '_turn_event_appender', None):
                try:
                    await self._turn_event_appender.append(
                        result.room_id,
                        result.user_message_id,
                        "turn_completed",
                        {"duration_ms": 0},
                    )
                except Exception:
                    pass
            await self._emit_processing_status(
                room_id=result.room_id,
                status=SSEProcessingStatus.COMPLETED,
                message_id=result.user_message_id,
                lifecycle_message_id=result.user_message_id,
            )
            await self._log_room_memory_stats(result.room_id)

        return True

    # ------------------------------------------------------------------
    # Unified summary emission
    # ------------------------------------------------------------------

    async def _emit_summary_working(
        self,
        room_id: str,
        user_message_id: str,
        summary_message_id: str,
        summary_client_request_id: str | None,
    ) -> None:
        """Show the HYBRO AI / Summary Agent card in 'working' state."""
        try:
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.PROCESSING,
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
                record_lifecycle=False,
                details="Compiling summary\u2026",
            )
        except Exception:
            logger.debug("SSE stage notification failed (summary)", exc_info=True)
        await self.sse_manager.send_task_submitted(
            room_id=room_id,
            message_id=summary_message_id,
            task_id=summary_message_id,
            agent_name="HYBRO AI",
            agent_id=CoordinatorAgentId.SUMMARY,
            status="working",
            related_message_id=user_message_id,
            task_content="Summarizing agent responses\u2026",
            client_request_id=summary_client_request_id,
        )

    async def _stream_summary_content(
        self,
        room_id: str,
        summary_message_id: str,
        token_stream,
        summary_client_request_id: str | None,
    ) -> str:
        """Stream synthesis tokens via artifact_update; return accumulated text."""
        return await stream_summary_to_sse(
            self.sse_manager,
            room_id=room_id,
            message_id=summary_message_id,
            agent_id=CoordinatorAgentId.SUMMARY,
            token_stream=token_stream,
            client_request_id=summary_client_request_id,
        )

    async def _emit_deterministic_digest(
        self,
        room_id: str,
        user_message_id: str,
        *,
        agent_count: int,
    ) -> None:
        """Persist a non-LLM summary stub when supervisor chose DONE (2+ agents)."""
        summary_message_id = f"summary-{user_message_id}"
        content = (
            f"{agent_count} agent{'s' if agent_count != 1 else ''} responded. "
            "Expand below to read each answer."
        )

        try:
            user_message = await self.database_service.get_room_user_message_by_message_id(
                user_message_id
            )
            user_id = user_message.user_id if user_message else None
            summary_client_request_id = (
                user_message.client_request_id if user_message else None
            )

            from models.room import MessageContent, RoomAgentMessage

            summary_task = build_completed_text_task(
                task_id=summary_message_id,
                text=content,
                context_id=summary_message_id,
            )

            summary_agent_message = RoomAgentMessage(
                room_id=room_id,
                message_id=summary_message_id,
                agent_id=CoordinatorAgentId.SUMMARY,
                related_message_id=user_message_id,
                user_id=user_id,
                client_request_id=summary_client_request_id,
                message_content=MessageContent(message_task=summary_task),
                message_created_at=utcnow(),
                extend_info={
                    "is_coordinator_summary": True,
                    "source_user_message_id": user_message_id,
                    "summary_origin": "deterministic",
                },
                task_content=content,
            )

            await self.database_service.upsert_room_agent_message(summary_agent_message)

            await self.sse_manager.send_agent_response(
                room_id,
                summary_message_id,
                CoordinatorAgentId.SUMMARY,
                content,
                related_message_id=user_message_id,
                client_request_id=summary_client_request_id,
            )
        except Exception as exc:
            logger.error(
                "RoomMessageCenter: _emit_deterministic_digest failed for room %s "
                "user message %s: %s",
                room_id,
                user_message_id,
                exc,
                exc_info=True,
            )

    async def _emit_unified_summary(
        self,
        room_id: str,
        user_message_id: str,
        *,
        synthesis_text: str | None = None,
        trajectory_responses: list[dict[str, str]] | None = None,
        is_debate: bool = False,
        working_already_emitted: bool = False,
    ) -> None:
        """Emit a single unified summary message for a user message turn.

        Routing logic:
        - If synthesis_text is provided (supervisor path), use it directly.
          Supervisor synthesis is streamed via artifact_update before this call.
        - Otherwise, collect agent responses and stream OpenAI summary generation.
        - Deterministic message_id ensures at most one summary per turn.
        """
        summary_message_id = f"summary-{user_message_id}"
        summary_client_request_id: str | None = None

        try:
            user_message = await self.database_service.get_room_user_message_by_message_id(
                user_message_id
            )
            user_id = user_message.user_id if user_message else None
            summary_client_request_id = (
                user_message.client_request_id if user_message else None
            )
            user_question_text: str | None = (
                user_message.message_content.message_text
                if user_message
                and user_message.message_content
                and isinstance(user_message.message_content.message_text, str)
                else None
            )

            # 1. Determine content — check agent count BEFORE emitting placeholder
            if synthesis_text is not None and synthesis_text.strip():
                # When the supervisor synthesized from fewer than 2 agent
                # responses, the individual task_update SSE already delivered
                # the agent's content — skip the redundant summary to avoid
                # duplicate content in the UI.  This mirrors the < 2 guard
                # on the non-synthesis (coordinator) path below.
                if trajectory_responses is not None and len(trajectory_responses) < 2:
                    return

                if not working_already_emitted:
                    await self._emit_summary_working(
                        room_id, user_message_id, summary_message_id,
                        summary_client_request_id,
                    )
                content = synthesis_text
                origin = "supervisor"
            else:
                # Collect agent responses
                if trajectory_responses:
                    agent_responses = trajectory_responses
                else:
                    agent_messages = await self.room_coordinator_service._collect_agent_messages_for_user_message(
                        user_message_id
                    )
                    agent_responses = []
                    for msg in agent_messages:
                        # Skip synthetic coordinator messages
                        if (
                            msg.extend_info
                            and isinstance(msg.extend_info, dict)
                            and msg.extend_info.get("is_coordinator_summary")
                        ) or msg.agent_id in (
                            "debate_summary", "non_debate_summary", "summary",
                            "supervisor_synthesis", "supervisor_error", "supervisor_clarify",
                        ):
                            continue
                        task = msg.message_content and msg.message_content.message_task
                        if (
                            task
                            and task.status
                            and task.status.state != CommonTaskState.COMPLETED
                        ):
                            continue
                        from common.utils.a2a_helpers import extract_agent_text_from_room_message
                        text = extract_agent_text_from_room_message(msg)
                        if text and msg.agent_id:
                            agent_name = await self.database_service.get_agent_name_by_agent_id(
                                msg.agent_id
                            )
                            agent_responses.append({
                                "agent_name": agent_name or msg.agent_id,
                                "message": text,
                            })

                # Skip summary entirely when fewer than 2 agents responded
                if len(agent_responses) < 2:
                    return

                await self._emit_summary_working(
                    room_id, user_message_id, summary_message_id,
                    summary_client_request_id,
                )

                mode = "debate" if is_debate else "non_debate"
                content = await self._stream_summary_content(
                    room_id,
                    summary_message_id,
                    self.openai_service.summarize_agent_responses_stream(
                        agent_responses,
                        mode=mode,
                        user_question=user_question_text,
                    ),
                    summary_client_request_id,
                )
                origin = "coordinator"

                if not content:
                    await self.sse_manager.send_task_update(
                        room_id, summary_message_id, "failed",
                        agent_id=CoordinatorAgentId.SUMMARY,
                        error="Summary generation returned empty",
                        client_request_id=summary_client_request_id,
                    )
                    return

            # 2. Build and persist
            from models.room import MessageContent, RoomAgentMessage

            summary_task = build_completed_text_task(
                task_id=summary_message_id,
                text=content,
                context_id=summary_message_id,
            )

            summary_agent_message = RoomAgentMessage(
                room_id=room_id,
                message_id=summary_message_id,
                agent_id=CoordinatorAgentId.SUMMARY,
                related_message_id=user_message_id,
                user_id=user_id,
                client_request_id=summary_client_request_id,
                message_content=MessageContent(message_task=summary_task),
                message_created_at=utcnow(),
                extend_info={
                    "is_coordinator_summary": True,
                    "source_user_message_id": user_message_id,
                    "summary_type": "debate" if is_debate else "non_debate",
                    "summary_origin": origin,
                },
                task_content=content,
            )

            await self.database_service.upsert_room_agent_message(summary_agent_message)

            # 4. Emit final SSE
            await self.sse_manager.send_agent_response(
                room_id,
                summary_message_id,
                CoordinatorAgentId.SUMMARY,
                content,
                related_message_id=user_message_id,
                client_request_id=summary_client_request_id,
            )

        except Exception as exc:
            logger.error(
                "RoomMessageCenter: _emit_unified_summary failed for room %s "
                "user message %s: %s",
                room_id, user_message_id, exc, exc_info=True,
            )
            try:
                await self.sse_manager.send_task_update(
                    room_id=room_id,
                    message_id=summary_message_id,
                    status="failed",
                    agent_id=CoordinatorAgentId.SUMMARY,
                    client_request_id=summary_client_request_id,
                )
            except Exception:
                pass
    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    async def _log_room_memory_stats(self, room_id: str) -> None:
        """Log room memory stats after processing (debug/monitoring only)."""
        room_memory = await self.database_service.get_room_memory_by_room_id(room_id)
        if room_memory and room_memory.memory_content:
            stats = get_context_stats(room_memory.memory_content)
            logger.info(
                "RoomMessageCenter: Room %s memory - %d turns, summary=%s, chars=%d",
                room_id,
                stats.get("history_turns", 0),
                "yes" if stats.get("has_summary") else "no",
                stats.get("total_chars", 0),
            )


class BoundRoomMessageCenterProxy:
    def __init__(self) -> None:
        self._runtime: RoomMessageCenter | None = None

    def bind(self, runtime: RoomMessageCenter) -> None:
        self._runtime = runtime

    def _require_runtime(self) -> RoomMessageCenter:
        if self._runtime is None:
            raise RuntimeError("RoomMessageCenter has not been bound at startup")
        return self._runtime

    def __getattr__(self, name: str) -> Any:
        return getattr(self._require_runtime(), name)


room_message_center = BoundRoomMessageCenterProxy()
