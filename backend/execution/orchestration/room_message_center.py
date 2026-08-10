from __future__ import annotations

import asyncio
import sys
import time
from collections import deque
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from a2a_adapter.task_status import build_completed_text_task
from common.a2a_constants import CommonTaskState, SSEProcessingStatus, is_terminal_state
from common.dto import MemorySearchResult, RoomMessageSummary
from common.eventing import InternalEventPublisher
from common.message_commit_events import publish_message_committed
from common.observability import traced_create_task
from common.protocols import (
    CompactionPort,
    ContextAssemblyPort,
    MemorySearchPort,
    RoomDistributedLock,
)
from common.utils.cancellation import CancellationError
from common.utils.context_utils import get_context_stats
from common.utils.logger import get_logger
from common.utils.summary_streaming import stream_summary_to_sse
from common.utils.time import utcnow
from execution.dispatch.agent_dispatcher import AgentDispatcher
from execution.dispatch.agent_message_processor import AgentMessageProcessor
from execution.dispatch.response_handler import AgentResponseHandler
from execution.dispatch.transports.direct import DirectTransport
from execution.orchestration.planner import RoomSupervisorPlannerAdapter
from execution.orchestration.queue_executor import QueueExecutor, QueueResult
from execution.orchestration.run_store import InMemoryOrchestrationRunStore
from execution.orchestration.supervisor_executor import SupervisorExecutor
from execution.ports import (
    A2ATransportPort,
    CancellationControlPort,
    CoordinatorSynthesisPort,
    ExecutionDeliveryPort,
    HITLCoordinator,
    HITLReaderPort,
    NotificationServicePort,
    RateLimitPort,
    RemoteTaskReaderPort,
    RoomContinuationStore,
    RoomMemoryPort,
    RoomMemoryReader,
    RoomMessageReader,
    RoomMessageWriter,
    RoomReader,
    RoomRuntimePort,
    RoomTaskStateStore,
    RoomWriter,
    TaskNotificationStorePort,
)
from execution.shutdown import is_graceful_shutdown_cancellation
from execution.state.task_state_manager import TaskStateManager
from llm_gateway.errors import LLMServiceNotBoundError
from models.orchestration import (
    TERMINAL_ORCHESTRATION_STATUSES,
    OrchestrationStatus,
)
from models.request import OrchestrationRequest
from models.response import OrchestrationResponse
from models.room import CoordinatorAgentId, RoomAgentMessage
from models.supervisor import (
    ActionType,
    AgentProfile,
    RoomConfig,
    RunStatus,
    SupervisorRunResult,
    SupervisorTrajectory,
)


class _UnboundRoomMessageCenterStore:
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name) from None

        def _missing_dependency(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(
                "RoomMessageCenter store dependency has not been bound"
            ) from None

        return _missing_dependency


a2a_transport = None
agent_resolver_service = None
default_store = _UnboundRoomMessageCenterStore()
room_memory = None
task_notifier = None
rate_limit_service = None
coordinator = None
summary_service = None
room_runtime = None
room_supervisor_service = None
orchestration_run_store = InMemoryOrchestrationRunStore()
orchestration_planner = None
delivery = None
internal_event_publisher = None
remote_task_reader = None
context_assembly = None
memory_search = None
context_compaction = None
build_turn_content = None
SupervisorPlanningError = RuntimeError

logger = get_logger(__name__)


def _room_message_summary_from_item(item) -> RoomMessageSummary:
    if isinstance(item, RoomMessageSummary):
        return item
    return RoomMessageSummary(
        agent_id=item.get("agent_id"),
        agent_name=item.get("agent_name", "Unknown Agent"),
        message=item.get("message", ""),
    )


class _RoomMessageCenterSettings:
    orphan_threshold_minutes = 2


settings = _RoomMessageCenterSettings()


class RoomLockBackendUnavailable(RuntimeError):
    """Raised when the distributed lock backend cannot fence room processing."""


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
    streaming/sync responses, queue management, commit-event publishing,
    and synthesis/summary memory updates."""

    def __init__(
        self,
        *,
        room_runtime: RoomRuntimePort,
        message_reader: RoomMessageReader,
        message_writer: RoomMessageWriter,
        task_state_store: RoomTaskStateStore,
        continuation_store: RoomContinuationStore,
        agent_lookup: RoomReader,
        agent_group_reader: RoomReader,
        room_reader: RoomReader,
        room_writer: RoomWriter,
        memory_reader: RoomMemoryReader,
        memory_writer,
        hitl_reader: HITLReaderPort,
        delivery: ExecutionDeliveryPort,
        cancellation_control: CancellationControlPort,
        coordinator: CoordinatorSynthesisPort,
        task_notifier: NotificationServicePort,
        task_notification_store: TaskNotificationStorePort,
        agent_resolver_service,
        a2a_transport: A2ATransportPort,
        remote_task_reader: RemoteTaskReaderPort,
        room_memory: RoomMemoryPort,
        rate_limit_service: RateLimitPort | None = None,
        room_supervisor_service,
        hitl_coordinator: HITLCoordinator,
        task_notifications,
        summary_service=None,
        task_notification_impl=None,
        agent_health_service=None,
        room_files=None,
        capability_issue_service=None,
        context_assembly: ContextAssemblyPort | None = None,
        memory_search: MemorySearchPort | None = None,
        context_compaction: CompactionPort | None = None,
        build_turn_content_func=None,
        supervisor_planning_error_cls=RuntimeError,
        orphan_threshold_minutes: int | None = None,
        orchestration_run_store=None,
        orchestration_planner=None,
        orchestration_resource_provider=None,
        cloud_health_cache_ttl: float = 30.0,
        cloud_health_check_timeout: float = 5.0,
        internal_event_publisher: InternalEventPublisher | None = None,
    ):
        if internal_event_publisher is None:
            raise RuntimeError(
                "RoomMessageCenter internal_event_publisher dependency is required"
            )
        self.room_runtime = room_runtime
        self.message_reader = message_reader
        self.message_writer = message_writer
        self.task_state_store = task_state_store
        self.continuation_store = continuation_store
        self.agent_lookup = agent_lookup
        self.agent_group_reader = agent_group_reader
        self.room_reader = room_reader
        self.room_writer = room_writer
        self.memory_reader = memory_reader
        self.memory_writer = memory_writer
        self.hitl_reader = hitl_reader
        self.delivery = delivery
        if cancellation_control is None:
            raise RuntimeError("RoomMessageCenter cancellation_control is required")
        self.cancellation_control = cancellation_control
        self.internal_event_publisher = internal_event_publisher
        self.coordinator = coordinator
        self.summary_service = summary_service
        self.task_notifications = task_notifications
        self.task_notification_store = task_notification_store
        self.room_memory = room_memory
        self.hitl_coordinator = hitl_coordinator
        self.context_assembly = context_assembly
        self.memory_search = memory_search
        self.context_compaction = context_compaction
        self.build_turn_content = build_turn_content_func
        self.supervisor_planning_error_cls = supervisor_planning_error_cls
        self.orphan_threshold_minutes = (
            settings.orphan_threshold_minutes
            if orphan_threshold_minutes is None
            else orphan_threshold_minutes
        )
        self.orchestration_run_store = (
            orchestration_run_store
            if orchestration_run_store is not None
            else InMemoryOrchestrationRunStore()
        )
        self.orchestration_planner = (
            orchestration_planner
            if orchestration_planner is not None
            else RoomSupervisorPlannerAdapter(
                supervisor_service=room_supervisor_service
            )
        )
        self.tsm = TaskStateManager(self.room_runtime, task_notifier)
        self.agent_dispatcher = AgentDispatcher(
            agent_resolver=agent_resolver_service,
            message_writer=self.message_writer,
            agent_lookup=self.agent_lookup,
            agent_group_reader=self.agent_group_reader,
        )
        # Shared result handler used by all transports
        client_request_resolver = SimpleNamespace(
            resolve_client_request_id_for_message_id=(
                self.task_state_store.resolve_client_request_id_for_message_id
            ),
            resolve_client_request_id_for_agent_message=(
                self.task_state_store.resolve_client_request_id_for_agent_message
            ),
            get_room_agent_message_by_message_id=(
                self.message_reader.get_room_agent_message_by_message_id
            ),
        )
        self.agent_response_handler = AgentResponseHandler(
            message_writer=self.message_writer,
            task_writer=self.message_writer,
            continuation_store=self.continuation_store,
            client_request_resolver=client_request_resolver,
            room_reader=self.room_reader,
            hitl_reader=self.hitl_reader,
            delivery=self.delivery,
            room_message_center=self,
            hitl_coordinator=hitl_coordinator,
            task_notifier=task_notifier,
            task_notification_store=self.task_notification_store,
            task_notification_impl=task_notification_impl,
            room_files=room_files,
        )

        # DirectTransport contains all streaming/sync response processing
        self.direct_transport = DirectTransport(
            response_handler=self.agent_response_handler,
            tsm=self.tsm,
            a2a_transport=a2a_transport,
            remote_task_reader=remote_task_reader,
            delivery=self.delivery,
            message_reader=self.message_reader,
            artifact_store=self.message_writer,
            task_updater=self.task_state_store,
            capability_issue_service=capability_issue_service,
        )

        # Relay service + dispatch middleware are initialized eagerly in
        # init_relay_service().  AgentMessageProcessor resolves the singleton
        # lazily on first use and builds the outbound transport in Execution.
        self.agent_message_processor = AgentMessageProcessor(
            delivery=self.delivery,
            room_runtime=self.room_runtime,
            room_memory_reader=self.memory_reader,
            task_tracker=self.task_state_store,
            transports={"direct": self.direct_transport},
            health_service=agent_health_service,
            cloud_health_cache_ttl=cloud_health_cache_ttl,
            cloud_health_check_timeout=cloud_health_check_timeout,
        )
        self.queue_executor = QueueExecutor(
            tsm=self.tsm,
            delivery=self.delivery,
            cancellation_control=self.cancellation_control,
            room_runtime=self.room_runtime,
            internal_event_publisher=internal_event_publisher,
            message_reader=self.message_reader,
            message_writer=self.message_writer,
            task_state_store=self.task_state_store,
            continuation_store=self.continuation_store,
            agent_lookup=self.agent_lookup,
            room_reader=self.room_reader,
            memory_reader=self.memory_reader,
            rate_limit_service=rate_limit_service,
            agent_dispatcher=self.agent_dispatcher,
            agent_message_processor=self.agent_message_processor,
            response_handler=self.agent_response_handler,
            hitl_coordinator=hitl_coordinator,
        )
        self.supervisor_executor = SupervisorExecutor(
            supervisor_service=room_supervisor_service,
            room_runtime=self.room_runtime,
            tsm=self.tsm,
            delivery=self.delivery,
            message_reader=self.message_reader,
            message_writer=self.message_writer,
            task_state_store=self.task_state_store,
            continuation_store=self.continuation_store,
            internal_event_publisher=internal_event_publisher,
            rate_limit_service=rate_limit_service,
            agent_dispatcher=self.agent_dispatcher,
            agent_message_processor=self.agent_message_processor,
            hitl_coordinator=hitl_coordinator,
            orchestration_run_store=self.orchestration_run_store,
            orchestration_planner=self.orchestration_planner,
            orchestration_resource_provider=orchestration_resource_provider,
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

    def _release_cancellation_token(self, message_id: str, token) -> None:
        if token is not None:
            self.cancellation_control.release_token(message_id, token)

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

    async def _publish_agent_message_committed(
        self,
        *,
        room_id: str,
        message_id: str | None,
        agent_id: str | None,
        agent_name: str,
        was_successful: bool,
    ) -> None:
        if not message_id:
            return
        if self.internal_event_publisher is None:
            raise RuntimeError("RoomMessageCenter internal_event_publisher not bound")
        await publish_message_committed(
            self.internal_event_publisher,
            room_id=room_id,
            message_id=message_id,
            message_type="agent",
            agent_id=agent_id,
            agent_name=agent_name,
            was_successful=was_successful,
        )

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
        system_message_id: str | None = None,
        turn_event_enabled: bool = False,
    ) -> dict[str, Any] | None:
        if getattr(self, "_processing_status_emitter", None) is None:
            raise RuntimeError(
                "RoomMessageCenter execution event dependencies not bound"
            )
        status_value = status.value if hasattr(status, "value") else str(status)
        return await self._processing_status_emitter(
            room_id=room_id,
            status=status,
            message_id=message_id,
            lifecycle_message_id=lifecycle_message_id or message_id,
            record_lifecycle=record_lifecycle,
            client_request_id=client_request_id,
            details=(
                details
                if isinstance(details, dict)
                else {"message": details}
                if isinstance(details, str)
                else None
            ),
            error_message=(
                details
                if isinstance(details, str)
                and status_value in {"failed", "canceled", "rejected", "error"}
                else details.get("message")
                if isinstance(details, dict)
                and isinstance(details.get("message"), str)
                and status_value in {"failed", "canceled", "rejected", "error"}
                else None
            ),
            agents=agents,
            system_message_id=system_message_id,
            turn_event_enabled=turn_event_enabled,
        )

    async def _persist_turn_completion_kind(
        self,
        user_message_id: str,
        turn_completion_kind: str,
    ) -> None:
        """Persist turn_completion_kind on user message extend_info (best-effort).

        Must be called BEFORE emitting the COMPLETED SSE so that the
        truth-check / reconcile path can always find the value in the DB.
        """
        try:
            user_msg = await self.message_reader.get_room_user_message_by_message_id(
                user_message_id
            )
            if user_msg:
                if not isinstance(user_msg.extend_info, dict):
                    user_msg.extend_info = {}
                user_msg.extend_info["turn_completion_kind"] = turn_completion_kind
                await self.message_writer.update_room_user_message_by_message_id(
                    user_message_id, user_msg
                )
        except Exception:
            logger.warning(
                "RoomMessageCenter: failed to persist turn_completion_kind "
                "for user message %s",
                user_message_id,
                exc_info=True,
            )

    async def _load_agent_messages_for_user_message(
        self,
        root_user_message_id: str,
    ) -> list[RoomAgentMessage]:
        messages: list[RoomAgentMessage] = []
        queried_related_ids: set[str] = set()
        seen_message_ids: set[str] = set()
        queue = deque([root_user_message_id])

        while queue:
            related_message_id = queue.popleft()
            if related_message_id in queried_related_ids:
                continue
            queried_related_ids.add(related_message_id)

            related_messages = (
                await self.message_reader.get_room_agent_messages_by_related_message_id(
                    related_message_id
                )
            )
            for msg in related_messages:
                message_id = msg.message_id
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
                messages.append(msg)
                queue.append(message_id)

        return messages

    def bind_facade(self, facade) -> None:
        self._room_facade = facade
        self._room_bound = True

    def bind_context_memory(
        self,
        *,
        context_assembly: ContextAssemblyPort,
        memory_search: MemorySearchPort,
    ) -> None:
        self.context_assembly = context_assembly
        self.memory_search = memory_search

    def _require_room_facade(self):
        if (
            not getattr(self, "_room_bound", False)
            or getattr(self, "_room_facade", None) is None
        ):
            raise RuntimeError(
                "RoomMessageCenter.bind_facade() not called - startup incomplete"
            )
        return self._room_facade

    @staticmethod
    def _assembled_context_text(assembled) -> str:
        metadata = getattr(assembled, "metadata", {}) or {}
        return metadata.get("context", "")

    @staticmethod
    def _trajectory_for_supervisor_result(
        result: SupervisorRunResult,
    ) -> SupervisorTrajectory | None:
        if result.trajectory is not None:
            return result.trajectory
        if result.run_state is None:
            return None
        return SupervisorExecutor._trajectory_from_state(result.run_state)

    @classmethod
    def _trajectory_responses_from_supervisor_result(
        cls,
        result: SupervisorRunResult,
    ) -> list[dict]:
        trajectory = cls._trajectory_for_supervisor_result(result)
        if trajectory is None:
            return []
        return [
            {
                "agent_id": step.agent_id,
                "agent_name": step.agent_name or step.agent_id,
                "message": step.response_text,
            }
            for entry in trajectory.entries
            if entry.action.action == ActionType.DELEGATE
            for step in entry.results
            if step.success and step.response_text
        ]

    async def _refresh_supervisor_conversation_context(
        self,
        *,
        room_id: str,
        room_memory,
        room_agent_set: dict,
        message_text: str,
    ) -> tuple[str | None, float | None]:
        context_assembly = self.context_assembly
        if room_memory is None or context_assembly is None:
            return None, None

        agent_dicts = [
            {"agent_id": aid, "agent_name": aname}
            for aid, aname in (room_agent_set or {}).items()
        ]
        memory_search_results: list[MemorySearchResult] | None = None
        try:
            if self.memory_search is not None:
                results = await self.memory_search.search_memory(
                    room_id=room_id,
                    query=message_text,
                )
                if results:
                    memory_search_results = results
        except Exception as search_err:
            logger.debug("supervisor_resume: memory search skipped: %s", search_err)

        assembled = context_assembly.assemble_supervisor_context_from_memory(
            room_memory,
            message_text,
            agent_registry=agent_dicts,
            max_turns=5,
            memory_search_results=memory_search_results,
        )
        metadata = getattr(assembled, "metadata", {}) or {}
        return self._assembled_context_text(assembled), metadata.get("occupancy_pct")

    def set_room_distributed_lock(self, room_lock: RoomDistributedLock | None) -> None:
        self._room_distributed_lock = room_lock
        # Obsolete turn-event wiring remains disabled; message/task SSE is
        # the single delivery source of truth.

    def set_redis_service(self, redis_service: RoomDistributedLock | None) -> None:
        self.set_room_distributed_lock(redis_service)
        # Obsolete turn-event wiring remains disabled; message/task SSE is
        # the single delivery source of truth.

    # -- Distributed room lock ---------------------------------------------

    _ROOM_LOCK_PREFIX = "room:lock:"

    # Lua script for safe release: only delete the key if we still own it.
    _RELEASE_LOCK_LUA = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"

    async def _acquire_distributed_lock(
        self,
        room_id: str,
        owner: str,
        ttl: int = ROOM_LOCK_HOLD_TTL_SECONDS,
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
        self,
        room_id: str,
        timeout: float = ROOM_LOCK_TIMEOUT_SECONDS,
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
        distributed_acquired = False

        loop = asyncio.get_event_loop()
        t0 = loop.time()
        elapsed = 0.0

        # --- Distributed lock (polling with back-off) ---------------------
        if use_distributed:
            poll_interval = 0.5
            redis_errors = 0
            while elapsed < timeout:
                result = await self._acquire_distributed_lock(
                    room_id, owner, ttl=ROOM_LOCK_HOLD_TTL_SECONDS
                )
                if result is True:
                    distributed_acquired = True
                    if elapsed > 1.0:
                        logger.info(
                            "Distributed lock acquired for room %s (owner=%s, waited=%.1fs, ttl=%ds)",
                            room_id,
                            owner[:8],
                            elapsed,
                            ROOM_LOCK_HOLD_TTL_SECONDS,
                        )
                    else:
                        logger.debug(
                            "Distributed lock acquired for room %s (owner=%s, waited=%.1fs)",
                            room_id,
                            owner[:8],
                            elapsed,
                        )
                    break
                if result is None:
                    redis_errors += 1
                    logger.error(
                        "Redis room lock unavailable for room %s; failing closed",
                        room_id,
                    )
                    raise RoomLockBackendUnavailable(
                        "distributed room lock backend is unavailable"
                    )
                else:
                    redis_errors = 0
                await asyncio.sleep(poll_interval)
                elapsed = loop.time() - t0
                poll_interval = min(poll_interval * 1.5, 5.0)
            else:
                logger.warning(
                    "Distributed lock timeout for room %s after %.1fs (owner=%s)",
                    room_id,
                    elapsed,
                    owner[:8],
                )
                return None  # timed out waiting for distributed lock

        # --- Local asyncio lock (intra-process fairness) ------------------
        local_lock = self._get_local_lock(room_id)
        try:
            remaining = max(0.1, timeout - elapsed)
            await asyncio.wait_for(local_lock.acquire(), timeout=remaining)
        except TimeoutError:
            if distributed_acquired:
                await self._release_distributed_lock(room_id, owner)
            return None
        except asyncio.CancelledError:
            if distributed_acquired:
                await asyncio.shield(self._release_distributed_lock(room_id, owner))
            raise

        return owner

    async def _renew_room_lock(self, room_id: str, owner: str) -> None:
        lock = getattr(self, "_room_distributed_lock", None)
        if lock is None:
            await asyncio.Future()
            return
        interval = max(1.0, ROOM_LOCK_HOLD_TTL_SECONDS / 3)
        retry_delay = 1.0
        next_delay = interval
        while True:
            await asyncio.sleep(next_delay)
            renewed = await lock.renew(
                room_id,
                owner,
                ROOM_LOCK_HOLD_TTL_SECONDS,
            )
            if renewed is True:
                retry_delay = 1.0
                next_delay = interval
                continue
            if renewed is False:
                raise RuntimeError(f"lost distributed room lock for room {room_id}")
            logger.warning(
                "Redis room lock renewal unavailable for room %s; retrying in %.1fs",
                room_id,
                retry_delay,
            )
            next_delay = retry_delay
            retry_delay = min(retry_delay * 2, 30.0)

    async def _release_room_lock(
        self,
        room_id: str,
        owner: str | None,
        *,
        acquired_at: float | None = None,
    ) -> None:
        """Release both the local asyncio lock and the distributed Redis lock."""
        if acquired_at is not None:
            held_seconds = time.monotonic() - acquired_at
            if held_seconds > ROOM_LOCK_HOLD_TTL_SECONDS * 0.8:
                logger.warning(
                    "Room %s held lock for %.0fs — approaching TTL of %ds. "
                    "Consider investigating slow processing or adding lock renewal.",
                    room_id,
                    held_seconds,
                    ROOM_LOCK_HOLD_TTL_SECONDS,
                )
            elif held_seconds > 60:
                logger.info(
                    "Room %s held lock for %.0fs",
                    room_id,
                    held_seconds,
                )
        local_lock = self._room_locks.get(room_id)
        if local_lock is not None and local_lock.locked():
            local_lock.release()
        if owner is not None:
            await self._release_distributed_lock(room_id, owner)
            logger.debug(
                "Distributed lock released for room %s (owner=%s)",
                room_id,
                owner[:8],
            )

    def _get_local_lock(self, room_id: str) -> asyncio.Lock:
        """Return (or create) the process-local asyncio.Lock for *room_id*."""
        lock = self._room_locks.get(room_id)
        if lock is None:
            lock = asyncio.Lock()
            self._room_locks[room_id] = lock
        return lock

    async def _heartbeat_processing_claim(self, message_id: str) -> None:
        """Keep a live turn newer than the orphan-recovery cutoff."""

        interval_seconds = max(1.0, self.orphan_threshold_minutes * 30.0)
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                refreshed = await self.message_writer.refresh_processing_claim(
                    message_id
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Failed to heartbeat processing claim for message %s",
                    message_id,
                    exc_info=True,
                )
                continue
            if not refreshed:
                logger.warning(
                    "Processing-claim heartbeat did not refresh message %s",
                    message_id,
                )

    async def _claim_user_message(self, request: OrchestrationRequest) -> bool:
        if getattr(request, "reuse_processing_claim", False):
            # HITL pauses intentionally retain the processing claim across the
            # user-input boundary. The response resumes the same logical run,
            # so refresh that claim instead of waiting for the orphan timeout.
            return await self.message_writer.refresh_processing_claim(
                request.room_user_message_id
            )
        if request.is_recovery:
            stale_threshold = utcnow() - timedelta(
                minutes=getattr(
                    self,
                    "orphan_threshold_minutes",
                    settings.orphan_threshold_minutes,
                )
            )
            return await self.message_writer.claim_or_reclaim_user_message(
                request.room_user_message_id, stale_threshold
            )
        return await self.message_writer.claim_user_message_for_processing(
            request.room_user_message_id
        )

    # ------------------------------------------------------------------

    async def process_room_user_message(
        self,
        request: OrchestrationRequest,
    ) -> OrchestrationResponse:
        """
        Process a room user message by executing all related agent messages in sequence.

        This method:
        1. Gets room memory context
        2. Queries all agent messages related to the user message
        3. Processes each agent message in order using streaming
        4. Publishes agent commit events so ContextMemory can project responses
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

        # Idempotency guard (SDR 2.5). A losing caller never acquires or
        # releases the registry token owned by the admitted execution.
        claimed = await self._claim_user_message(request)

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
        owned_token = self.cancellation_control.create_token(room_user_message_id)
        try:
            # Hydrate an L1 miss from Redis before the first synchronous
            # cancellation checkpoint. check_cancelled signals the active token.
            await self.cancellation_control.check_cancelled(room_user_message_id)
        except BaseException:
            self._release_cancellation_token(room_user_message_id, owned_token)
            raise

        # ----- Per-room lock: serialise all processing within a room -----
        try:
            lock_owner = await self._acquire_room_lock(room_id)
        except RoomLockBackendUnavailable:
            self._release_cancellation_token(room_user_message_id, owned_token)
            details = (
                "Room processing is temporarily unavailable — please retry shortly"
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details=details,
                system_message_id=f"sys-{room_user_message_id}",
            )
            await self.message_writer.unclaim_user_message(room_user_message_id)
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error=details,
                status_code=503,
            )
        except BaseException:
            self._release_cancellation_token(room_user_message_id, owned_token)
            raise
        lock_acquired_at = time.monotonic()
        if lock_owner is None:
            logger.error(
                "RoomMessageCenter: Timed out waiting for room lock on %s "
                "(message %s). Another processing run may be stuck.",
                room_id,
                room_user_message_id,
            )
            self._release_cancellation_token(room_user_message_id, owned_token)
            # Commit the public winner and its recovery intent before cleanup.
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="Room is busy processing another message — please retry shortly",
                system_message_id=f"sys-{room_user_message_id}",
            )
            # Release the claim so the message can be retried (by user or
            # stale-recovery) instead of staying permanently orphaned.
            await self.message_writer.unclaim_user_message(room_user_message_id)
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
        try:
            await self.message_writer.refresh_processing_claim(room_user_message_id)
            claim_heartbeat = traced_create_task(
                self._heartbeat_processing_claim(room_user_message_id),
                name=f"processing-claim-heartbeat:{room_user_message_id}",
            )
            lock_renewal = traced_create_task(
                self._renew_room_lock(room_id, lock_owner),
                name=f"room-lock-renewal:{room_user_message_id}",
            )
        except BaseException:
            self._release_cancellation_token(room_user_message_id, owned_token)
            try:
                await self._release_room_lock(
                    room_id, lock_owner, acquired_at=lock_acquired_at
                )
            except Exception:
                logger.warning("failed to release room lock", exc_info=True)
            raise

        # Busy / cancel targeting use `runs` + `active_runs` (not rooms.processing_message_id).

        try:
            body_task = traced_create_task(
                self._process_room_user_message_locked(
                    request,
                    room_id,
                    room_user_message_id,
                    token=owned_token,
                ),
                name=f"room-message-body:{room_user_message_id}",
            )
            done, _ = await asyncio.wait(
                {body_task, lock_renewal},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lock_renewal in done:
                body_task.cancel()
                try:
                    await body_task
                except asyncio.CancelledError:
                    pass
                await lock_renewal
                raise RuntimeError("distributed room lock renewal stopped")
            return await body_task
        except asyncio.CancelledError as exc:
            self._release_cancellation_token(room_user_message_id, owned_token)
            if is_graceful_shutdown_cancellation(exc):
                logger.info(
                    "RoomMessageCenter: processing interrupted by graceful shutdown "
                    "for message %s; leaving durable and public state recoverable",
                    room_user_message_id,
                )
                raise
            logger.info(
                "RoomMessageCenter: processing task cancelled for message %s",
                room_user_message_id,
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.CANCELED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                system_message_id=f"sys-{room_user_message_id}",
            )
            raise
        except Exception:
            self._release_cancellation_token(room_user_message_id, owned_token)
            raise
        finally:
            body_error = sys.exc_info()[1]
            cleanup_error: BaseException | None = None
            claim_heartbeat.cancel()
            lock_renewal.cancel()
            try:
                await claim_heartbeat
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                cleanup_error = exc
            try:
                await lock_renewal
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            try:
                await self._release_room_lock(
                    room_id, lock_owner, acquired_at=lock_acquired_at
                )
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                self._release_cancellation_token(room_user_message_id, owned_token)
                if body_error is None:
                    raise cleanup_error

    async def _process_room_user_message_locked(
        self,
        request: OrchestrationRequest,
        room_id: str,
        room_user_message_id: str,
        *,
        token,
    ) -> OrchestrationResponse:
        """Inner processing path — caller MUST hold the per-room lock."""

        # Get user_id from the user message for rate limiting.
        # Fall back to the request-level user_id (from auth) if the stored
        # message is missing or has no user_id.
        user_message = await self.message_reader.get_room_user_message_by_message_id(
            room_user_message_id
        )
        if user_message and self._turn_event_appender:
            try:
                already_started = await self.message_writer.turn_exists(
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
        user_id = (user_message.user_id if user_message else None) or request.user_id

        # Extract quoted context via TurnContext (QUOTE_REPLY: quote_id + snippet or legacy)
        quoted_text: str | None = None
        if user_message:
            from execution.orchestration.turn_context import (
                TurnQuoteMissingError,
                load_turn_context,
            )

            try:
                _tc = await load_turn_context(self.message_reader, user_message)
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
                self._release_cancellation_token(room_user_message_id, token)
                return OrchestrationResponse(
                    room_id=room_id,
                    success=False,
                    error=str(e),
                    status_code=400,
                )

        # --- Supervisor branch ---
        is_supervisor = user_message and self._is_supervisor_envelope(
            user_message.extend_info
        )
        if is_supervisor:
            return await self._process_supervisor(
                user_message=user_message,
                room_id=room_id,
                room_user_message_id=room_user_message_id,
                user_id=user_id,
                quoted_text=quoted_text,
                token=token,
            )

        # --- QueueExecutor path (non-supervisor routing and mentions) ---
        # Query pre-created agent messages. Non-supervisor room-default
        # routing and explicit mention flows create RoomAgentMessage records
        # during Phase 1 (send_message_to_room). The supervisor loop does NOT
        # pre-create agent messages — it generates them dynamically — so this
        # query is only reached for non-supervisor messages.
        query_response = (
            await self.room_runtime.inquiry_agent_messages_by_related_message_id(
                room_user_message_id
            )
        )
        if not query_response.success:
            self._release_cancellation_token(room_user_message_id, token)
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error=query_response.error,
                status_code=500,
            )

        has_pending_agent_messages = bool(query_response.message_list)

        # Safety net: supervisor-enabled room but no supervisor flag and no
        # pre-created agent messages (e.g. @mentions).  This catches genuine
        # bugs where _prepare_for_supervisor was skipped.
        if not has_pending_agent_messages and user_message:
            room = await self.room_reader.get_room_by_room_id(room_id)
            if (
                room
                and isinstance(room.extend_info, dict)
                and room.extend_info.get("use_supervisor")
            ):
                logger.error(
                    "RoomMessageCenter: Room %s has use_supervisor=True but user "
                    "message %s lacks supervisor flag and has no pre-created "
                    "agent messages. Failing instead of silently doing nothing.",
                    room_id,
                    room_user_message_id,
                )
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.FAILED,
                    message_id=room_user_message_id,
                    lifecycle_message_id=room_user_message_id,
                    details="Supervisor-enabled room missing supervisor preparation data",
                    system_message_id=f"sys-{room_user_message_id}",
                )
                self._release_cancellation_token(room_user_message_id, token)
                return OrchestrationResponse(
                    room_id=room_id,
                    success=False,
                    error="Supervisor data not prepared for this message",
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
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.CANCELED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                system_message_id=f"sys-{room_user_message_id}",
            )
            # Durable winner-owned projection performs descendant cleanup.
            message_queue.clear()
            self._release_cancellation_token(room_user_message_id, token)
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
            # Durable root wins before any child task cleanup/projection.
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details={
                    "message": (
                        getattr(queue_processing_result, "error_message", None)
                        or "Failed to process agent messages"
                    ),
                    "code": (
                        getattr(queue_processing_result, "error_code", None)
                        or "agent_processing_failed"
                    ),
                },
                system_message_id=getattr(
                    queue_processing_result,
                    "system_message_id",
                    f"sys-{room_user_message_id}",
                ),
                turn_event_enabled=bool(getattr(self, "_turn_event_appender", None)),
            )
            self._release_cancellation_token(room_user_message_id, token)
            return OrchestrationResponse(
                success=False,
                error="Failed to process agent messages",
                status_code=500,
            )

        if queue_processing_result.result == QueueResult.PAUSED:
            # Continuation persistence owns resume state; active tokens are rebuilt
            # and Redis-hydrated by the resume path.
            self._release_cancellation_token(room_user_message_id, token)
            return OrchestrationResponse(
                room_id=room_id, success=True, error=None, status_code=200
            )

        if queue_processing_result.result == QueueResult.CANCELED:
            self._release_cancellation_token(room_user_message_id, token)
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        # QueueResult.COMPLETED — cancellation still owns the race until the
        # durable completed transition and terminal projection succeed.
        if token.is_cancelled:
            await self._emit_completion_race_cancellation(
                room_id, room_user_message_id, token
            )
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )
        summary_coro = self._emit_unified_summary(room_id, room_user_message_id)
        try:
            turn_completion_kind, _ = await token.race(summary_coro)
        except CancellationError:
            await self._emit_completion_race_cancellation(
                room_id, room_user_message_id, token
            )
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )
        turn_completion_kind = turn_completion_kind or "deterministic"

        if token.is_cancelled:
            await self._emit_completion_race_cancellation(
                room_id, room_user_message_id, token
            )
            return OrchestrationResponse(
                success=True,
                error="Processing cancelled by user",
                status_code=200,
            )

        # Persist turn_completion_kind before the COMPLETED SSE so the
        # truth-check / reconcile path can always find the value in the DB.
        await self._persist_turn_completion_kind(
            room_user_message_id,
            turn_completion_kind,
        )

        # Claim the durable root terminal before publishing the child completed
        # task, completion metadata, or turn_completed journal entry. A terminal
        # CAS winner suppresses all downstream completion projections.
        completion_payload = await self._emit_processing_status(
            room_id=room_id,
            status=SSEProcessingStatus.COMPLETED,
            message_id=room_user_message_id,
            lifecycle_message_id=room_user_message_id,
            details={
                "turn_completion_kind": turn_completion_kind,
                "turn_phase": "terminal",
            },
            system_message_id=getattr(
                queue_processing_result,
                "system_message_id",
                f"sys-{room_user_message_id}",
            ),
            turn_event_enabled=bool(getattr(self, "_turn_event_appender", None)),
        )
        if completion_payload is None:
            await self.cancellation_control.check_cancelled(room_user_message_id)
            if token.is_cancelled:
                await self._emit_completion_race_cancellation(
                    room_id, room_user_message_id, token
                )
            self._release_cancellation_token(room_user_message_id, token)
            return OrchestrationResponse(
                room_id=room_id,
                success=True,
                error=("Processing cancelled by user" if token.is_cancelled else None),
                status_code=200,
            )

        # Log room memory stats (debug/monitoring)
        await self._log_room_memory_stats(room_id)
        self._release_cancellation_token(room_user_message_id, token)

        return OrchestrationResponse(
            room_id=room_id, success=True, error=None, status_code=200
        )

    async def _emit_completion_race_cancellation(
        self,
        room_id: str,
        user_message_id: str,
        token,
    ) -> None:
        await self._emit_processing_status(
            room_id=room_id,
            status=SSEProcessingStatus.CANCELED,
            message_id=user_message_id,
            lifecycle_message_id=user_message_id,
            system_message_id=f"sys-{user_message_id}",
            turn_event_enabled=bool(getattr(self, "_turn_event_appender", None)),
        )
        self._release_cancellation_token(user_message_id, token)

    async def _notify_all_non_terminal_tasks_failed(
        self,
        room_id: str,
        user_message_id: str,
    ) -> None:
        """Safety net: send ``task_update`` SSE for every agent message under
        *user_message_id* whose task is still in a non-terminal state.

        Called after the durable room-level terminal winner is committed so
        individual task bubbles remain downstream projections of that winner.
        The idempotency check inside
        ``notify_task_update`` ensures messages already notified as terminal
        are skipped (no double-notification).
        """
        try:
            agent_messages = (
                await self.message_reader.get_room_agent_messages_by_related_message_id(
                    user_message_id
                )
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

            task = msg.message_content.message_task if msg.message_content else None
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
    # Supervisor adaptive loop
    # ------------------------------------------------------------------

    @staticmethod
    def _is_supervisor_envelope(extend_info: Any) -> bool:
        if not isinstance(extend_info, dict) or not extend_info.get("orchestration"):
            return False
        run_id = extend_info.get("orchestration_run_id")
        candidate_agent_ids = extend_info.get("candidate_agent_ids")
        return (
            isinstance(run_id, str)
            and bool(run_id.strip())
            and isinstance(candidate_agent_ids, list)
            and all(
                isinstance(agent_id, str) and bool(agent_id.strip())
                for agent_id in candidate_agent_ids
            )
        )

    async def _build_supervisor_inputs(
        self,
        extend: dict[str, Any],
        room_id: str,
        message_text: str,
    ) -> tuple[list[AgentProfile], RoomConfig, str | None]:
        candidate_agent_ids = extend.get("candidate_agent_ids")
        if not isinstance(candidate_agent_ids, list):
            raise ValueError("orchestration envelope missing candidate_agent_ids")

        agent_registry: list[AgentProfile] = []
        profiles_by_id: dict[str, AgentProfile] = {}
        for raw_agent_id in candidate_agent_ids:
            if not isinstance(raw_agent_id, str) or not raw_agent_id.strip():
                raise ValueError(
                    "orchestration envelope has invalid candidate_agent_ids"
                )
            agent_id = raw_agent_id.strip()
            if agent_id in profiles_by_id:
                continue
            agent = await self.agent_lookup.get_agent_by_agent_id(agent_id)
            if agent is None:
                raise ValueError(f"orchestration candidate agent not found: {agent_id}")
            profile = AgentProfile.from_agent(agent)
            profiles_by_id[agent_id] = profile
            agent_registry.append(profile)

        mentioned_agent_ids = extend.get("mentioned_agent_ids")
        explicit_mentions: list[dict[str, str]] = []
        if isinstance(mentioned_agent_ids, list):
            for raw_agent_id in mentioned_agent_ids:
                if not isinstance(raw_agent_id, str):
                    continue
                agent_id = raw_agent_id.strip()
                profile = profiles_by_id.get(agent_id)
                if profile is None:
                    continue
                explicit_mentions.append(
                    {
                        "agent_id": profile.agent_id,
                        "agent_name": profile.agent_name,
                        "mention_text": f"<@{profile.agent_id}|{profile.agent_name}>",
                    }
                )

        room_config = RoomConfig(
            room_agent_set={
                profile.agent_id: profile.agent_name for profile in agent_registry
            },
            explicit_mentions=explicit_mentions,
        )
        conversation_context = None
        try:
            room_memory = await self.memory_reader.get_room_memory_by_room_id(room_id)
            if room_memory and self.context_assembly is not None:
                (
                    conversation_context,
                    _occupancy,
                ) = await self._refresh_supervisor_conversation_context(
                    room_id=room_id,
                    room_memory=room_memory,
                    room_agent_set=room_config.room_agent_set,
                    message_text=message_text,
                )
        except Exception as e:
            logger.warning(
                "RoomMessageCenter: failed to build supervisor context for %s: %s",
                room_id,
                e,
            )
        return agent_registry, room_config, conversation_context

    async def _supervisor_system_message_id(self, run_id: str) -> str | None:
        try:
            state = await self.orchestration_run_store.get_run(run_id)
        except Exception:
            logger.warning(
                "Failed to read supervisor system message for terminal projection",
                exc_info=True,
            )
            return None
        if state is None:
            return None
        return state.system_agent_message_id or state.summary_message_id

    async def _process_supervisor(
        self,
        user_message,
        room_id: str,
        room_user_message_id: str,
        user_id: str | None,
        quoted_text: str | None,
        token,
    ) -> OrchestrationResponse:
        """Execute the single durable supervisor orchestration path."""
        extend = user_message.extend_info
        build_turn_content = self.build_turn_content or (
            lambda text, _attachments: text
        )
        message_text = build_turn_content(
            user_message.message_content.message_text or "",
            user_message.message_content.attachments,
        )
        try:
            if not self._is_supervisor_envelope(extend):
                raise ValueError("durable orchestration envelope is missing")
            (
                agent_registry,
                room_config,
                conversation_context,
            ) = await self._build_supervisor_inputs(
                extend,
                room_id,
                message_text,
            )
        except (TypeError, ValueError) as exc:
            logger.error(
                "RoomMessageCenter: supervisor envelope is invalid: %s",
                exc,
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="supervisor data corrupted or incomplete",
                system_message_id=await self._supervisor_system_message_id(
                    room_user_message_id
                ),
                turn_event_enabled=bool(getattr(self, "_turn_event_appender", None)),
            )
            self._release_cancellation_token(room_user_message_id, token)
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error=f"supervisor data corrupted: {exc}",
                status_code=500,
            )

        try:
            logger.info(
                "room_message_supervisor_started room_id=%s user_message_id=%s "
                "client_request_id=%s agent_count=%d quoted=%s",
                room_id,
                room_user_message_id,
                getattr(user_message, "client_request_id", None),
                len(agent_registry),
                bool(quoted_text),
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
                user_message=user_message,
            )
            logger.info(
                "room_message_supervisor_completed room_id=%s user_message_id=%s "
                "client_request_id=%s status=%s success=%s error=%s",
                room_id,
                room_user_message_id,
                getattr(user_message, "client_request_id", None),
                getattr(result.status, "value", result.status),
                result.status not in (RunStatus.FAILED, RunStatus.CANCELED),
                getattr(result, "error", None),
            )
        except self.supervisor_planning_error_cls:
            logger.error(
                "RoomMessageCenter: Supervisor planning failed for %s",
                room_user_message_id,
            )
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="Supervisor planning failed",
                system_message_id=await self._supervisor_system_message_id(
                    room_user_message_id
                ),
                turn_event_enabled=bool(getattr(self, "_turn_event_appender", None)),
            )
            try:
                await self.coordinator.emit_synthesis_message(
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
            self._release_cancellation_token(room_user_message_id, token)
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
            self._release_cancellation_token(room_user_message_id, token)
            await self._emit_processing_status(
                room_id=room_id,
                status=SSEProcessingStatus.FAILED,
                message_id=room_user_message_id,
                lifecycle_message_id=room_user_message_id,
                details="Supervisor execution failed unexpectedly",
                system_message_id=await self._supervisor_system_message_id(
                    room_user_message_id
                ),
                turn_event_enabled=bool(getattr(self, "_turn_event_appender", None)),
            )
            return OrchestrationResponse(
                room_id=room_id,
                success=False,
                error="Supervisor execution failed unexpectedly",
                status_code=500,
            )

        await self._handle_supervisor_run_result(
            result=result,
            room_id=room_id,
            user_message_id=room_user_message_id,
            token=token,
            user_message=user_message,
        )
        await self._log_room_memory_stats(room_id)

        is_failure = result.status == RunStatus.FAILED
        return OrchestrationResponse(
            room_id=room_id,
            success=not is_failure,
            error="Supervisor execution failed" if is_failure else None,
            status_code=500 if is_failure else 200,
        )

        # ------------------------------------------------------------------
        # Supervisor resume (push notification webhook)
        # ------------------------------------------------------------------

    async def _handle_supervisor_run_result(
        self,
        result: SupervisorRunResult,
        room_id: str,
        user_message_id: str,
        token=None,
        room=None,
        user_message=None,
    ) -> None:
        """Project durable orchestration state and emit terminal delivery."""
        result_run_state = result.run_state
        durable_status = (
            result_run_state.status if result_run_state is not None else None
        )
        durable_result_status = {
            OrchestrationStatus.COMPLETED: RunStatus.COMPLETED,
            OrchestrationStatus.CANCELED: RunStatus.CANCELED,
            OrchestrationStatus.FAILED: RunStatus.FAILED,
            OrchestrationStatus.BUDGET_EXHAUSTED: RunStatus.FAILED,
        }.get(durable_status)
        if durable_result_status is not None:
            result.status = durable_result_status
        elif (
            result.status == RunStatus.COMPLETED
            and token is not None
            and token.is_cancelled
        ):
            result.status = RunStatus.CANCELED
        completion_cancellable = durable_status != OrchestrationStatus.COMPLETED
        orchestration_status = (
            getattr(result_run_state.status, "value", result_run_state.status)
            if result_run_state is not None
            else getattr(result.status, "value", result.status)
        )
        result_trajectory = self._trajectory_for_supervisor_result(result)
        system_message_id = (
            getattr(result_run_state, "system_agent_message_id", None)
            if result_run_state is not None
            else None
        ) or (
            result_trajectory.system_agent_message_id
            if result_trajectory is not None
            else None
        )
        if user_message is None:
            user_message = (
                await self.message_reader.get_room_user_message_by_message_id(
                    user_message_id
                )
            )
        if user_message and result.status in (
            RunStatus.COMPLETED,
            RunStatus.AWAITING_INPUT,
            RunStatus.FAILED,
            RunStatus.CANCELED,
            RunStatus.PAUSED,
        ):
            if not isinstance(user_message.extend_info, dict):
                user_message.extend_info = {}
            user_message.extend_info["orchestration_status"] = orchestration_status
            if (
                result_run_state is not None
                and result_run_state.status in TERMINAL_ORCHESTRATION_STATUSES
            ):
                user_message.processing_claimed_at = None
            if result_run_state is not None:
                user_message.extend_info["orchestration_run_id"] = (
                    result_run_state.run_id
                )
                if result_run_state.candidate_scope is not None:
                    user_message.extend_info["candidate_scope_snapshot_id"] = (
                        result_run_state.candidate_scope.snapshot_id
                    )
                    user_message.extend_info["candidate_scope_source"] = (
                        result_run_state.candidate_scope.source
                    )
                if result_run_state.client_request_id:
                    user_message.extend_info["client_request_id"] = (
                        result_run_state.client_request_id
                    )
            terminal_summary = result.terminal_summary
            if terminal_summary is None and result_run_state is not None:
                terminal_summary = result_run_state.terminal_summary
            if terminal_summary is not None:
                user_message.extend_info["terminal_summary"] = terminal_summary
            else:
                user_message.extend_info.pop("terminal_summary", None)
            await self.message_writer.update_room_user_message_by_message_id(
                user_message_id, user_message
            )

        match result.status:
            case RunStatus.COMPLETED:
                if completion_cancellable and token is not None and token.is_cancelled:
                    await self._emit_completion_race_cancellation(
                        room_id, user_message_id, token
                    )
                    return

                # Supervisor owns finalization. Never write or emit a second summary.
                turn_completion_kind = (
                    "synthesis"
                    if result.synthesis_text is not None
                    else "deterministic"
                )

                if completion_cancellable and token is not None and token.is_cancelled:
                    await self._emit_completion_race_cancellation(
                        room_id, user_message_id, token
                    )
                    return

                # Persist turn_completion_kind before the COMPLETED SSE.
                await self._persist_turn_completion_kind(
                    user_message_id,
                    turn_completion_kind,
                )

                completion_payload = await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.COMPLETED,
                    message_id=user_message_id,
                    lifecycle_message_id=user_message_id,
                    details={
                        "turn_completion_kind": turn_completion_kind,
                        "turn_phase": "terminal",
                    },
                    system_message_id=system_message_id,
                    turn_event_enabled=bool(
                        getattr(self, "_turn_event_appender", None)
                    ),
                )
                if completion_payload is None:
                    if completion_cancellable and token is not None:
                        await self.cancellation_control.check_cancelled(user_message_id)
                    if (
                        completion_cancellable
                        and token is not None
                        and token.is_cancelled
                    ):
                        await self._emit_completion_race_cancellation(
                            room_id, user_message_id, token
                        )
                    return
            case RunStatus.PAUSED:
                pass

            case RunStatus.AWAITING_INPUT:
                pass  # Continuation already saved; HITLService emits the SSE event.
                # Token stays alive — resume path creates/reuses it.

            case RunStatus.CANCELED:
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.CANCELED,
                    message_id=user_message_id,
                    lifecycle_message_id=user_message_id,
                    system_message_id=system_message_id,
                    turn_event_enabled=bool(
                        getattr(self, "_turn_event_appender", None)
                    ),
                )

            case RunStatus.FAILED:
                terminal_summary = result.terminal_summary
                failure_reason = (
                    result.terminal_reason
                    or (
                        terminal_summary.get("reason")
                        if terminal_summary is not None
                        else None
                    )
                    or "supervisor execution failed"
                )
                failure_code = (
                    terminal_summary.get("code")
                    if terminal_summary is not None
                    else "error"
                )
                await self._emit_processing_status(
                    room_id=room_id,
                    status=SSEProcessingStatus.FAILED,
                    message_id=user_message_id,
                    lifecycle_message_id=user_message_id,
                    details={
                        "message": failure_reason,
                        "code": failure_code,
                        **(
                            {"terminal_summary": terminal_summary}
                            if terminal_summary is not None
                            else {}
                        ),
                    },
                    system_message_id=system_message_id,
                    turn_event_enabled=bool(
                        getattr(self, "_turn_event_appender", None)
                    ),
                )

        if getattr(result.status, "value", result.status) in {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELED.value,
        }:
            try:
                await self._run_supervisor_terminal_post_loop_integration(
                    result, room_id
                )
            except Exception:
                logger.warning(
                    "Supervisor terminal post-loop integration failed after root commit",
                    exc_info=True,
                )

        # Terminal run state is persisted via run_command_handler / runs; no room mirror write.

        # Continuations hold durable resume state. Active tokens must never be
        # retained across PAUSED/AWAITING_INPUT; resume recreates and hydrates them.
        self._release_cancellation_token(user_message_id, token)

    async def _run_supervisor_terminal_post_loop_integration(
        self,
        result: SupervisorRunResult,
        room_id: str,
    ) -> None:
        # --- Post-loop integration (§11.3): synthesis, room summary, compaction ---
        terminal_statuses = {
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELED.value,
        }
        if getattr(result.status, "value", result.status) in terminal_statuses:
            trajectory = self._trajectory_for_supervisor_result(result)
            # Add synthesis text to room memory history
            if (
                result.status == RunStatus.COMPLETED
                and result.synthesis_text
                and trajectory is not None
            ):
                try:
                    synthesis_turn_id = await self.room_memory.add_synthesis_to_history(
                        room_id=room_id,
                        synthesis_text=result.synthesis_text,
                        trajectory=trajectory,
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
            await self.room_memory.update_room_summary(
                room_id=room_id,
                synthesis_text=synthesis_text,
                synthesis_turn_id=synthesis_turn_id,
            )
        except Exception as e:
            logger.warning(
                "RoomMessageCenter: Background room summary update failed for %s: %s",
                room_id,
                e,
            )

    async def _trigger_compaction_safe(self, room_id: str) -> None:
        """Wrapper for compaction trigger (§6.5). Awaited within per-room lock."""
        try:
            if self.context_compaction is not None:
                await self.context_compaction.compact_if_needed(room_id)
        except Exception as e:
            logger.warning(
                "RoomMessageCenter: Background compaction failed for %s: %s",
                room_id,
                e,
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

        Delegates queue continuation mechanics to ``QueueExecutor``. Durable
        supervisor orchestration resumes independently from run state.

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

        # Peek at the continuation data non-destructively to get the room_id
        # before acquiring the lock.
        continuation = (
            await self.continuation_store.get_pending_continuation_on_message(
                message_id
            )
        )
        if continuation is None:
            return await self._resume_durable_orchestration_from_agent_message(
                message_id
            )
        if not isinstance(continuation, dict):
            claimed = (
                await self.continuation_store.get_and_clear_continuation_on_message(
                    message_id
                )
            )
            if claimed is None:
                return False
            await self.queue_executor._restore_invalid_continuation(
                message_id,
                claimed,
                reason="continuation payload must be an object",
            )
            return False
        if not continuation:
            return await self._resume_durable_orchestration_from_agent_message(
                message_id
            )

        # ----- Per-room lock: serialise resume within the same room -----
        room_id = continuation.get("room_id")
        lock_acquired_at: float | None = None
        if room_id:
            try:
                lock_owner = await self._acquire_room_lock(room_id)
            except RoomLockBackendUnavailable:
                logger.error(
                    "RoomMessageCenter: distributed room lock backend unavailable "
                    "during resume for room %s (message %s)",
                    room_id,
                    message_id,
                )
                return False
            lock_acquired_at = time.monotonic()
            if lock_owner is None:
                logger.error(
                    "RoomMessageCenter: Timed out waiting for room lock on %s "
                    "during resume (message %s).",
                    room_id,
                    message_id,
                )
                # Because the initial read was non-destructive, the continuation
                # remains in the DB safely. No need to re-save it.
                return False
        else:
            lock_owner = None
            logger.warning(
                "RoomMessageCenter: continuation for message %s has no room_id — "
                "cannot acquire per-room lock; proceeding without serialisation",
                message_id,
            )

        try:
            # Safely perform the destructive read *inside* the locked section.
            # This prevents concurrent webhooks from reading stale trajectories.
            locked_continuation = (
                await self.continuation_store.get_and_clear_continuation_on_message(
                    message_id
                )
            )
            if not locked_continuation:
                locked_continuation = await self.continuation_store.get_and_clear_continuation_on_user_message(
                    message_id
                )

            if not locked_continuation:
                # E.g. another concurrent resume already processed it.
                return False

            return await self._resume_continuation_locked(
                locked_continuation, message_id, task_result_text
            )
        finally:
            if room_id is not None:
                await self._release_room_lock(
                    room_id, lock_owner, acquired_at=lock_acquired_at
                )

    async def _resume_durable_orchestration_from_agent_message(
        self,
        message_id: str,
    ) -> bool:
        """Recover a durable supervisor run after an async agent callback.

        Supervisor runs checkpoint only ``OrchestrationRunState``. Agent messages
        point directly at the root user message, so a terminal or interactive
        callback can re-enter the canonical loop without a serialized trajectory.
        """
        agent_message = await self.message_reader.get_room_agent_message_by_message_id(
            message_id
        )
        user_message_id = getattr(agent_message, "related_message_id", None)
        if not isinstance(user_message_id, str) or not user_message_id:
            logger.debug(
                "RoomMessageCenter: No continuation or durable run root found for %s",
                message_id,
            )
            return False

        state = await self.orchestration_run_store.get_latest_by_user_message_id(
            user_message_id
        )
        if state is None or state.status in TERMINAL_ORCHESTRATION_STATUSES:
            return False
        if not any(
            intent.planned_agent_message_id == message_id
            for intent in state.dispatch_intents
        ):
            return False

        response = await self.process_room_user_message(
            OrchestrationRequest(
                room_id=state.room_id,
                room_user_message_id=state.user_message_id,
                user_id=getattr(agent_message, "user_id", None),
                is_recovery=True,
                reuse_processing_claim=True,
                client_request_id=state.client_request_id,
            )
        )
        return response.success

    async def _resume_continuation_locked(
        self,
        continuation: dict,
        message_id: str,
        task_result_text: str | None,
    ) -> bool:
        """Inner resume path — caller MUST hold the per-room lock (if available)."""

        # Queue path: re-save the continuation so QueueExecutor can read it
        # (we already consumed it with get_and_clear above).
        restored = await self.continuation_store.save_continuation_on_message(
            message_id, continuation
        )
        if not restored:
            await self.queue_executor._restore_invalid_continuation(
                message_id,
                continuation,
                reason="continuation handoff restore failed",
            )
            return False

        result = await self.queue_executor.resume_from_continuation(
            message_id,
            task_result_text,
        )

        if not result.success:
            return False

        if result.needs_completion and result.room_id and result.user_message_id:
            completion_token = getattr(result, "token", None)
            if completion_token is None:
                completion_token = self.cancellation_control.create_token(
                    result.user_message_id
                )
            try:
                await self.cancellation_control.check_cancelled(result.user_message_id)
                if completion_token.is_cancelled:
                    await self._emit_completion_race_cancellation(
                        result.room_id,
                        result.user_message_id,
                        completion_token,
                    )
                    return True

                try:
                    summary_result = await completion_token.race(
                        self._emit_unified_summary(
                            result.room_id,
                            result.user_message_id,
                        )
                    )
                except CancellationError:
                    await self._emit_completion_race_cancellation(
                        result.room_id,
                        result.user_message_id,
                        completion_token,
                    )
                    return True
                turn_completion_kind, _ = summary_result or (
                    "deterministic",
                    None,
                )
                if completion_token.is_cancelled:
                    await self._emit_completion_race_cancellation(
                        result.room_id,
                        result.user_message_id,
                        completion_token,
                    )
                    return True

                # Persist turn_completion_kind before the COMPLETED SSE.
                await self._persist_turn_completion_kind(
                    result.user_message_id,
                    turn_completion_kind or "deterministic",
                )

                completion_payload = await self._emit_processing_status(
                    room_id=result.room_id,
                    status=SSEProcessingStatus.COMPLETED,
                    message_id=result.user_message_id,
                    lifecycle_message_id=result.user_message_id,
                    details={
                        "turn_completion_kind": turn_completion_kind,
                        "turn_phase": "terminal",
                    },
                    system_message_id=f"sys-{result.user_message_id}",
                    turn_event_enabled=bool(
                        getattr(self, "_turn_event_appender", None)
                    ),
                )
                if completion_payload is None:
                    await self.cancellation_control.check_cancelled(
                        result.user_message_id
                    )
                    if completion_token.is_cancelled:
                        await self._emit_completion_race_cancellation(
                            result.room_id,
                            result.user_message_id,
                            completion_token,
                        )
                    return True

                await self._log_room_memory_stats(result.room_id)
            finally:
                self._release_cancellation_token(
                    result.user_message_id, completion_token
                )

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
                details={
                    "turn_phase": "synthesizing",
                    "message": "Compiling summary\u2026",
                },
            )
        except Exception:
            logger.debug("SSE stage notification failed (summary)", exc_info=True)
        await self.delivery.send_task_submitted(
            room_id=room_id,
            message_id=summary_message_id,
            task_id=summary_message_id,
            agent_name="HYBRO AI",
            agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
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
            self.delivery,
            room_id=room_id,
            message_id=summary_message_id,
            agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
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
            user_message = (
                await self.message_reader.get_room_user_message_by_message_id(
                    user_message_id
                )
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
                agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
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
            )

            await self.message_writer.upsert_room_agent_message(summary_agent_message)

            await self.delivery.send_agent_response(
                room_id,
                summary_message_id,
                CoordinatorAgentId.SYSTEM_HYBRO,
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
        trajectory_responses: list[dict] | None = None,
        working_already_emitted: bool = False,
        skip_db_write: bool = False,
    ) -> tuple[str | None, str | None]:
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
            user_message = (
                await self.message_reader.get_room_user_message_by_message_id(
                    user_message_id
                )
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
                # responses, skip creating a duplicate summary-* message — the
                # answer already lives on system:hybro (sys-*). Still report
                # synthesis completion kind so hydrate/reconnect do not treat
                # the turn as deterministic or still synthesizing.
                if trajectory_responses is not None and len(trajectory_responses) < 2:
                    return "synthesis", None

                if not working_already_emitted:
                    await self._emit_summary_working(
                        room_id,
                        user_message_id,
                        summary_message_id,
                        summary_client_request_id,
                    )
                content = synthesis_text
                origin = "supervisor"
            else:
                # Collect agent responses
                if trajectory_responses:
                    agent_responses = [
                        _room_message_summary_from_item(item)
                        for item in trajectory_responses
                    ]
                else:
                    agent_messages = await self._load_agent_messages_for_user_message(
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
                            CoordinatorAgentId.SYSTEM_HYBRO,
                            CoordinatorAgentId.SYSTEM_CLARIFIER,
                            CoordinatorAgentId.SUPERVISOR_ERROR,
                        ):
                            continue
                        task = msg.message_content and msg.message_content.message_task
                        if (
                            task
                            and task.status
                            and task.status.state != CommonTaskState.COMPLETED
                        ):
                            continue
                        from common.utils.a2a_helpers import (
                            extract_agent_text_from_room_message,
                        )

                        text = extract_agent_text_from_room_message(msg)
                        if text and msg.agent_id:
                            agent_name = (
                                await self.room_reader.get_agent_name_by_agent_id(
                                    msg.agent_id
                                )
                            )
                            agent_responses.append(
                                RoomMessageSummary(
                                    agent_id=msg.agent_id,
                                    agent_name=agent_name or msg.agent_id,
                                    message=text,
                                )
                            )

                # Skip summary entirely when fewer than 2 agents responded
                if len(agent_responses) < 2:
                    return "deterministic", None

                await self._emit_summary_working(
                    room_id,
                    user_message_id,
                    summary_message_id,
                    summary_client_request_id,
                )

                if self.summary_service is None:
                    raise LLMServiceNotBoundError("SummaryLLMService is not bound")
                content = await self._stream_summary_content(
                    room_id,
                    summary_message_id,
                    self.summary_service.summarize_agent_responses_stream(
                        agent_responses,
                        user_question=user_question_text,
                    ),
                    summary_client_request_id,
                )
                origin = "coordinator"

                if not content:
                    await self.delivery.send_task_update(
                        room_id,
                        summary_message_id,
                        "failed",
                        agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                        error="Summary generation returned empty",
                        client_request_id=summary_client_request_id,
                    )
                    return None, None

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
                agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                related_message_id=user_message_id,
                user_id=user_id,
                client_request_id=summary_client_request_id,
                message_content=MessageContent(message_task=summary_task),
                message_created_at=utcnow(),
                extend_info={
                    "is_coordinator_summary": True,
                    "source_user_message_id": user_message_id,
                    "summary_type": "synthesis",
                    "summary_origin": origin,
                },
            )

            await self.message_writer.upsert_room_agent_message(summary_agent_message)

            # 4. Emit final SSE
            await self.delivery.send_agent_response(
                room_id,
                summary_message_id,
                CoordinatorAgentId.SYSTEM_HYBRO,
                content,
                related_message_id=user_message_id,
                client_request_id=summary_client_request_id,
            )
            return "synthesis", content

        except LLMServiceNotBoundError:
            raise
        except Exception as exc:
            logger.error(
                "RoomMessageCenter: _emit_unified_summary failed for room %s "
                "user message %s: %s",
                room_id,
                user_message_id,
                exc,
                exc_info=True,
            )
            try:
                await self.delivery.send_task_update(
                    room_id=room_id,
                    message_id=summary_message_id,
                    status="failed",
                    agent_id=CoordinatorAgentId.SYSTEM_HYBRO,
                    client_request_id=summary_client_request_id,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    async def _log_room_memory_stats(self, room_id: str) -> None:
        """Log room memory stats after processing (debug/monitoring only)."""
        room_memory = await self.memory_reader.get_room_memory_by_room_id(room_id)
        if room_memory:
            stats = get_context_stats(room_memory)
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
