"""Relay Service — manages hub connections and message relay.

Responsibilities:
- Hub registration and ownership validation
- SSE connection pool (in-memory asyncio.Queue per hub_id)
- Event routing: push user_message events to hub queues
- Publish processing: receive hub events, verify auth + room ownership,
  update RoomAgentMessages, broadcast via SSEManager, resume orchestration
- Heartbeat & offline queue (max N, TTL)
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncGenerator, Awaitable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from a2a.types import AgentCard
from common.dto.agent import HubAgentDescriptor
from common.utils.logger import get_logger
from common.utils.time import utcnow
from config.settings import settings
from jobs.constants import RELAY_HEARTBEAT_MONITOR
from models.hub import (
    Hub,
    HubAgentSync,
    HubPublishRequest,
    HubStatus,
    RelayToHubEvent,
)

if TYPE_CHECKING:
    from database.mongodb import MongoDB
    from infrastructure.leader_election import LeaderElection
    from infrastructure.relay_streams import RelayStreamService
    from models.api_key import APIKey
    from services.database_service import DatabaseService
    from services.sse_services import SSEManager

logger = get_logger(__name__)


class _OfflineQueueEntry:
    __slots__ = ("event", "enqueued_at")

    def __init__(self, event: RelayToHubEvent) -> None:
        self.event = event
        self.enqueued_at = time.monotonic()


class RelayService:
    """Core relay service (singleton)."""

    def __init__(
        self,
        *,
        mongo: MongoDB,
        database_service: DatabaseService,
        sse_manager: SSEManager,
    ) -> None:
        self._mongo = mongo
        self._db = database_service
        self._sse = sse_manager

        # hub_id -> asyncio.Queue for live SSE streams
        self._hub_queues: dict[str, asyncio.Queue] = {}

        # hub_id -> deque[_OfflineQueueEntry]
        self._offline_queues: dict[str, deque[_OfflineQueueEntry]] = {}

        # hub_id -> monotonic timestamp of last hub-initiated heartbeat
        self._last_hub_heartbeat: dict[str, float] = {}

        # hub_id -> monotonic timestamp when _disconnect_hub was called;
        # used to distinguish transient blips from genuine offline.
        self._hub_disconnected_at: dict[str, float] = {}

        self._heartbeat_task: asyncio.Task | None = None
        self._shutdown = False

        # Set eagerly by init_relay_service(); must not be None at request time.
        self._relay_transport: Any | None = None

        self._streams: RelayStreamService | None = None
        # For Redis Streams path: local disconnect signaling
        self._hub_disconnect_events: dict[str, asyncio.Event] = {}

        self._leader: LeaderElection | None = None
        self._agent_registry_writer = None

    def set_leader_election(self, leader: LeaderElection | None) -> None:
        """Attach a LeaderElection instance for distributed leader gating."""
        self._leader = leader

    def bind_agent_registry_writer(self, writer) -> None:
        self._agent_registry_writer = writer

    def _require_agent_registry_writer(self):
        if self._agent_registry_writer is None:
            raise RuntimeError(
                "AgentRegistryWriter is not bound; hub agent writes are unavailable"
            )
        return self._agent_registry_writer

    def set_relay_transport(self, transport: Any) -> None:
        """Wire up the RelayTransport so publish events can be delegated."""
        self._relay_transport = transport

    @property
    def relay_transport(self) -> Any | None:
        """Public read accessor for the eagerly-initialised transport."""
        return self._relay_transport

    def set_stream_service(self, streams: RelayStreamService) -> None:
        """Attach Redis Streams for durable hub relay."""
        self._streams = streams
        logger.info("RelayService: Redis Streams attached for hub relay")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._shutdown = False
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("RelayService heartbeat loop started")

    async def stop(self) -> None:
        self._shutdown = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("RelayService stopped")

    # ------------------------------------------------------------------
    # Hub registration
    # ------------------------------------------------------------------

    async def register_hub(self, hub_id: str, api_key: APIKey) -> Hub:
        hub = Hub(
            hub_id=hub_id,
            user_id=api_key.user_id,
            registered_at=utcnow(),
        )
        await self._mongo.upsert_hub(hub.model_dump(mode="json"))
        logger.info("Hub %s registered for user %s", hub_id, api_key.user_id)
        return hub

    # ------------------------------------------------------------------
    # SSE connection
    # ------------------------------------------------------------------

    async def connect_hub(
        self, hub_id: str, api_key: APIKey, last_event_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Long-lived SSE generator for a hub.

        On connect: marks hub online, delivers offline queue, then yields
        events as they arrive.  On disconnect: marks hub offline.
        """
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc or hub_doc["user_id"] != api_key.user_id:
            raise PermissionError("Hub not owned by this API key")

        connection_id = str(uuid4())
        await self._mongo.update_hub_status(
            hub_id,
            is_online=True,
            last_connected_at=utcnow(),
            connection_id=connection_id,
        )

        if self._streams:
            # --- Redis Streams path ---
            await self._streams.record_heartbeat(hub_id)

            # Signal any stale local connection on this instance
            old_event = self._hub_disconnect_events.get(hub_id)
            if old_event is not None:
                old_event.set()

            disconnect = asyncio.Event()
            self._hub_disconnect_events[hub_id] = disconnect

            yield {"type": "connection_ready"}

            # "$" = only new messages (fresh connection); explicit ID = resume
            start_id = last_event_id or "$"
            try:
                while not self._shutdown and not disconnect.is_set():
                    entries = await self._streams.read_events(
                        hub_id, last_id=start_id,
                        block_ms=settings.relay_heartbeat_interval * 1000,
                    )
                    await self._streams.record_heartbeat(hub_id)
                    if entries:
                        for entry_id, payload in entries:
                            payload["_stream_id"] = entry_id
                            yield payload
                            start_id = entry_id
                    else:
                        yield {"type": "heartbeat", "timestamp": utcnow().isoformat()}
            finally:
                self._hub_disconnect_events.pop(hub_id, None)
                result = await self._mongo.update_hub_status_if_current(
                    hub_id, connection_id=connection_id, is_online=False,
                )
                if not result:
                    logger.info("Hub %s: connection superseded", hub_id)
        else:
            # --- In-memory Queue path (existing code, unchanged) ---
            queue: asyncio.Queue = asyncio.Queue()

            old_queue = self._hub_queues.get(hub_id)
            if old_queue is not None:
                logger.info("Hub %s reconnecting — signaling stale connection", hub_id)
                await old_queue.put({"type": "_disconnect"})

            self._hub_queues[hub_id] = queue
            self._last_hub_heartbeat[hub_id] = time.monotonic()
            self._hub_disconnected_at.pop(hub_id, None)

            yield {"type": "connection_ready"}

            # Flush offline queue
            offline = self._offline_queues.pop(hub_id, deque())
            now = time.monotonic()
            ttl = settings.relay_offline_queue_ttl
            for entry in offline:
                if now - entry.enqueued_at < ttl:
                    yield entry.event.model_dump(mode="json")

            try:
                while not self._shutdown:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=float(settings.relay_heartbeat_interval))
                        if event.get("type") == "_disconnect":
                            break
                        yield event
                    except TimeoutError:
                        yield {"type": "heartbeat", "timestamp": utcnow().isoformat()}
            finally:
                await self._disconnect_hub(hub_id, queue, connection_id)

    async def _disconnect_hub(
        self, hub_id: str, queue: asyncio.Queue, connection_id: str
    ) -> None:
        if self._hub_queues.get(hub_id) is not queue:
            logger.info("Hub %s: stale connection teardown skipped", hub_id)
            return
        self._hub_queues.pop(hub_id, None)
        self._last_hub_heartbeat.pop(hub_id, None)
        self._hub_disconnected_at[hub_id] = time.monotonic()

        # Rescue pending messages from the live queue into offline queue
        # so they survive a reconnect rather than being silently lost.
        oq = self._offline_queues.setdefault(hub_id, deque())
        while not queue.empty():
            try:
                event_dict = queue.get_nowait()
                if isinstance(event_dict, dict) and event_dict.get("type") == "_disconnect":
                    continue
                oq.append(_OfflineQueueEntry(RelayToHubEvent(**event_dict)))
            except (asyncio.QueueEmpty, Exception):
                break

        result = await self._mongo.update_hub_status_if_current(
            hub_id, connection_id=connection_id, is_online=False
        )
        if not result:
            logger.info(
                "Hub %s: connection superseded, skipping agent status update",
                hub_id,
            )
            return
        await self._require_agent_registry_writer().mark_hub_agents_offline(hub_id)
        logger.info("Hub %s disconnected", hub_id)

    # ------------------------------------------------------------------
    # Hub heartbeat (hub-initiated liveness signal)
    # ------------------------------------------------------------------

    async def record_hub_heartbeat(self, hub_id: str, api_key: APIKey) -> None:
        """Record a hub-initiated heartbeat."""
        if self._streams:
            hub_doc = await self._mongo.get_hub(hub_id)
            if not hub_doc or hub_doc["user_id"] != api_key.user_id:
                raise PermissionError("Hub not owned by this API key")
            await self._streams.record_heartbeat(hub_id)
        else:
            if hub_id not in self._hub_queues:
                raise PermissionError(
                    f"Hub {hub_id} is not connected — heartbeat rejected"
                )
            self._last_hub_heartbeat[hub_id] = time.monotonic()

    def _is_hub_connected_locally(self, hub_id: str) -> bool:
        """Process-local check — only valid for connection management on this worker.

        Do NOT use for liveness decisions; use :meth:`is_hub_alive` instead.
        """
        return hub_id in self._hub_disconnect_events or hub_id in self._hub_queues

    async def is_hub_alive(self, hub_id: str) -> bool:
        """Authoritative hub liveness check (multi-worker safe).

        Streams path: Redis TTL key is the single source of truth.
        In-memory path: process-local connection state (single-worker only).

        All real-time liveness queries MUST use this method rather than
        reading MongoDB ``is_online`` directly.
        """
        if self._streams:
            return await self._streams.is_hub_alive(hub_id)
        return self._is_hub_connected_locally(hub_id)

    async def mark_hub_agents_offline(
        self, hub_id: str, connection_id: str | None = None,
    ) -> None:
        """Eagerly correct stale agent status for a disconnected hub.

        When *connection_id* is provided, the offline transition is conditional:
        it only applies if the stored connection still matches, preventing a
        concurrent ``connect_hub`` from being clobbered.
        """
        if connection_id:
            result = await self._mongo.update_hub_status_if_current(
                hub_id, connection_id=connection_id, is_online=False,
            )
            if not result:
                logger.info(
                    "Hub %s: offline mark skipped (connection superseded)", hub_id,
                )
                return
        else:
            await self._mongo.update_hub_status(hub_id, is_online=False)
        await self._require_agent_registry_writer().mark_hub_agents_offline(hub_id)

    # ------------------------------------------------------------------
    # Agent sync
    # ------------------------------------------------------------------

    async def sync_agents(
        self,
        hub_id: str,
        agents: list[HubAgentSync],
        api_key: APIKey,
        *,
        prune_missing: bool = True,
    ) -> list[dict]:
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc or hub_doc["user_id"] != api_key.user_id:
            raise PermissionError("Hub not owned by this API key")

        # Refresh Redis TTL at the start of an authenticated sync so
        # ``is_hub_alive`` is reliable for the activation pass at the end
        # (multi-worker: sync may hit a different worker than the SSE loop).
        if self._streams:
            await self._streams.record_heartbeat(hub_id)

        writer = self._require_agent_registry_writer()
        valid_agents: list[HubAgentSync] = []
        for ag in agents:
            try:
                AgentCard(**ag.agent_card)
            except Exception:
                logger.warning(
                    "Hub %s: skipping agent %s with invalid card: %s",
                    hub_id, ag.local_agent_id, ag.agent_card,
                )
                continue
            valid_agents.append(ag)

        if agents and not valid_agents:
            logger.warning(
                "Hub %s: skipping sync — %d agent(s) in request but none "
                "passed AgentCard validation",
                hub_id,
                len(agents),
            )
            return []

        descriptors = [
            HubAgentDescriptor(
                hub_id=hub_id,
                agent_id=ag.local_agent_id,
                name=ag.name,
                url=ag.agent_card.get("url"),
                capabilities=list(ag.capabilities or []),
                raw_card=dict(ag.agent_card or {}),
            )
            for ag in valid_agents
        ]
        synced = await writer.sync_hub_agents(
            hub_id,
            api_key.user_id,
            descriptors,
            prune_missing=prune_missing,
        )
        return [
            {
                "agent_id": item.agent_id,
                "local_agent_id": item.descriptor.agent_id if item.descriptor else None,
            }
            for item in synced
        ]

    # ------------------------------------------------------------------
    # Push event to hub
    # ------------------------------------------------------------------

    async def push_to_hub(self, hub_id: str, event: RelayToHubEvent) -> bool:
        """Push an event to a connected hub, or queue for offline delivery.

        Returns True if delivered to the live SSE queue, False otherwise.
        During the grace period after disconnect, messages are queued for
        offline delivery.  After the grace period expires, returns False
        without queuing so the caller can reject.
        """
        if self._streams:
            if not await self._streams.is_hub_alive(hub_id):
                await self._fail_offline_message(event, error_text="Agent is offline")
                return False
            result = await self._streams.push_event(hub_id, event.model_dump(mode="json"))
            return result is not None
        else:
            # --- Existing in-memory queue logic (unchanged) ---
            queue = self._hub_queues.get(hub_id)
            if queue is not None:
                await queue.put(event.model_dump(mode="json"))
                return True

            await self.mark_hub_agents_offline(hub_id)

            disconnected_at = self._hub_disconnected_at.get(hub_id)
            if disconnected_at is not None:
                elapsed = time.monotonic() - disconnected_at
                if elapsed > settings.relay_offline_grace_period:
                    logger.info(
                        "Hub %s offline for %.0fs (> grace %ds) — rejecting message",
                        hub_id, elapsed, settings.relay_offline_grace_period,
                    )
                    await self._fail_offline_message(
                        event,
                        error_text="Agent is offline — hub has been unreachable",
                    )
                    return False

            oq = self._offline_queues.setdefault(hub_id, deque())
            if len(oq) >= settings.relay_offline_queue_max:
                logger.warning(
                    "Offline queue for hub %s is full (%d); dropping oldest",
                    hub_id,
                    len(oq),
                )
                oldest = oq.popleft()
                await self._fail_offline_message(oldest.event)

            oq.append(_OfflineQueueEntry(event))
            logger.info(
                "Hub %s offline — queued event (queue size: %d)", hub_id, len(oq)
            )
            return False

    async def _fail_offline_message(
        self, event: RelayToHubEvent, error_text: str | None = None,
    ) -> None:
        """Mark a RoomAgentMessage as failed when delivery is impossible."""
        if not event.agent_message_id:
            return
        if error_text is None:
            error_text = "Hub agent message expired (offline queue overflow)"
        msg = await self._db.get_room_agent_message_by_message_id(
            event.agent_message_id
        )
        if msg:
            if msg.message_content is None:
                from models.room import MessageContent
                msg.message_content = MessageContent()
            msg.message_content.message_text = error_text
            try:
                if msg.message_content.message_task:
                    task = msg.message_content.message_task
                    from a2a.types import Message as A2AMessage
                    from a2a.types import Role, TaskState, TaskStatus, TextPart
                    task.status = TaskStatus(
                        state=TaskState.failed,
                        message=A2AMessage(
                            role=Role.agent,
                            parts=[TextPart(text=error_text)],
                            message_id=str(uuid4()),
                        ),
                    )
            except Exception:
                logger.warning(
                    "Failed to update A2A task status for message %s",
                    event.agent_message_id,
                    exc_info=True,
                )
            await self._db.update_room_agent_message_by_message_id(
                event.agent_message_id, msg
            )
        if event.room_id:
            await self._sse.send_error(
                event.room_id,
                error_text,
                message_id=event.agent_message_id,
            )

    # ------------------------------------------------------------------
    # Publish (hub -> cloud)
    # ------------------------------------------------------------------

    async def process_publish(
        self,
        hub_id: str,
        request: HubPublishRequest,
        api_key: APIKey,
    ) -> None:
        """Process events published by a hub daemon."""
        # 1. Verify hub ownership via API key
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc:
            raise PermissionError("Unknown hub")
        if hub_doc["user_id"] != api_key.user_id:
            raise PermissionError("Hub not owned by this API key")

        # 2. Verify room ownership
        room = await self._db.get_room_by_room_id(request.room_id)
        if not room:
            raise ValueError(f"Room {request.room_id} not found")
        if hub_doc["user_id"] != room.room_owner_id:
            raise PermissionError(
                "Hub owner does not match room owner"
            )

        # 3. Process events
        for ev in request.events:
            await self._process_single_publish_event(
                ev.type, ev.agent_message_id, ev.data, request.room_id,
                hub_id,
            )

    async def _process_single_publish_event(
        self,
        event_type: str,
        agent_message_id: str,
        data: dict,
        room_id: str,
        hub_id: str,
    ) -> None:
        """Delegate to RelayTransport for event normalization and handling."""
        if self._relay_transport is None:
            raise RuntimeError(
                f"RelayTransport not set — cannot process publish event {event_type}. "
                "This indicates init_relay_service() was not called at startup."
            )
        await self._relay_transport.handle_publish_event(
            event_type, agent_message_id, data, room_id, hub_id,
        )

    # ------------------------------------------------------------------
    # Relay task operations (cancel + HITL reply)
    # ------------------------------------------------------------------

    async def cancel_relay_task(
        self, hub_id: str, agent_message_id: str, local_agent_id: str,
        task_id: str | None = None,
    ) -> bool:
        """Push a cancel_task event to the hub for an in-flight task."""
        event = RelayToHubEvent(
            type="cancel_task",
            agent_message_id=agent_message_id,
            local_agent_id=local_agent_id,
            task_id=task_id,
        )
        return await self.push_to_hub(hub_id, event)

    async def reply_to_relay_task(
        self,
        hub_id: str,
        agent_message_id: str,
        local_agent_id: str,
        reply_text: str,
        room_id: str,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> bool:
        """Push a user_reply event to the hub for a HITL interaction."""
        event = RelayToHubEvent(
            type="user_reply",
            room_id=room_id,
            agent_message_id=agent_message_id,
            local_agent_id=local_agent_id,
            reply_text=reply_text,
            task_id=task_id,
            context_id=context_id,
        )
        return await self.push_to_hub(hub_id, event)

    # ------------------------------------------------------------------
    # Hub status
    # ------------------------------------------------------------------

    async def get_hub_status(self, user_id: str) -> list[HubStatus]:
        hubs = await self._mongo.get_hubs_by_user(user_id)
        result: list[HubStatus] = []
        for h in hubs:
            hub_id = h["hub_id"]
            actually_online = await self.is_hub_alive(hub_id)

            active, inactive = await self._mongo.count_hub_agents(hub_id)
            result.append(
                HubStatus(
                    hub_id=hub_id,
                    is_online=actually_online,
                    last_connected_at=h.get("last_connected_at"),
                    agent_count=active + inactive,
                    active_agent_count=active,
                    inactive_agent_count=inactive,
                )
            )
        return result

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Periodically check for unresponsive hubs and sweep expired offline entries."""
        stale_threshold = (
            settings.relay_heartbeat_interval
            * settings.relay_hub_agent_heartbeat_miss_limit
        )
        while not self._shutdown:
            try:
                await asyncio.sleep(settings.relay_heartbeat_interval)
                await self._run_heartbeat_iteration(stale_threshold)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in heartbeat loop")

    async def _run_heartbeat_iteration(self, stale_threshold: float) -> None:
        """Run a single heartbeat iteration, gated by leader election if available.

        Uses acquire/release (not hold()) because the check is fast relative
        to TTL (interval * 2). If profiling shows checks exceeding TTL,
        switch to leader.hold() with renewal.
        """
        if self._leader:
            ttl = settings.relay_heartbeat_interval * 2
            acquired = await self._leader.try_acquire(RELAY_HEARTBEAT_MONITOR, ttl)
            if not acquired:
                return  # another instance is the leader
            try:
                await self._do_heartbeat_check(stale_threshold)
                await self.sweep_offline_queues()
            finally:
                await self._leader.release(RELAY_HEARTBEAT_MONITOR)
        else:
            await self._do_heartbeat_check(stale_threshold)
            await self.sweep_offline_queues()

    async def _do_heartbeat_check(self, stale_threshold: float) -> None:
        """Check for unresponsive hubs and self-heal stale states.

        In-memory path: checks _hub_queues for stale heartbeats.
        Redis Streams path: two passes —
          1. Hubs marked online in MongoDB whose Redis key expired → mark offline
             and signal SSE disconnect (best-effort, process-local).
          2. Hubs marked offline in MongoDB whose Redis key is alive → re-mark
             online (self-heal from transient failures).
        """
        if self._streams:
            # Pass 1: detect expired hubs and mark offline
            stale_hubs = await self._mongo.hubs_collection.find(
                {"is_online": True}, {"hub_id": 1, "connection_id": 1}
            ).to_list(length=None)
            for doc in stale_hubs:
                hub_id = doc["hub_id"]
                if not await self._streams.is_hub_alive(hub_id):
                    logger.warning(
                        "Hub %s heartbeat expired in Redis — marking offline",
                        hub_id,
                    )
                    await self.mark_hub_agents_offline(
                        hub_id, connection_id=doc.get("connection_id"),
                    )
                    disconnect_event = self._hub_disconnect_events.get(hub_id)
                    if disconnect_event is not None:
                        disconnect_event.set()

            # Pass 2: self-heal hubs that recovered
            recovering_hubs = await self._mongo.hubs_collection.find(
                {"is_online": False}, {"hub_id": 1}
            ).to_list(length=100)
            for doc in recovering_hubs:
                hub_id = doc["hub_id"]
                if await self._streams.is_hub_alive(hub_id):
                    logger.info(
                        "Hub %s recovered — Redis heartbeat alive, re-marking online",
                        hub_id,
                    )
                    await self._mongo.update_hub_status(hub_id, is_online=True)
        else:
            now = time.monotonic()
            for hub_id in list(self._hub_queues.keys()):
                last = self._last_hub_heartbeat.get(hub_id)
                if last is not None and (now - last) > stale_threshold:
                    logger.warning(
                        "Hub %s has not sent a heartbeat for %.0fs — disconnecting",
                        hub_id,
                        now - last,
                    )
                    q = self._hub_queues.get(hub_id)
                    if q:
                        await q.put({"type": "_disconnect"})

    # ------------------------------------------------------------------
    # Offline queue TTL sweep
    # ------------------------------------------------------------------

    async def sweep_offline_queues(self) -> None:
        """Remove expired entries from offline queues.

        Called periodically (e.g. from heartbeat loop or a separate job).
        """
        now = time.monotonic()
        ttl = settings.relay_offline_queue_ttl
        for hub_id, oq in list(self._offline_queues.items()):
            while oq and (now - oq[0].enqueued_at) >= ttl:
                expired = oq.popleft()
                await self._fail_offline_message(expired.event)
            if not oq:
                del self._offline_queues[hub_id]


# ---------------------------------------------------------------------------
# Module-level singleton (lazy init — call init_relay_service() at startup)
# ---------------------------------------------------------------------------

relay_service: RelayService | None = None


class RelayHubLivenessReader:
    def __init__(self, relay: RelayService) -> None:
        self._relay = relay

    def is_hub_online(self, hub_id: str) -> bool | Awaitable[bool]:
        if self._relay._streams is None:
            return self._relay._is_hub_connected_locally(hub_id)
        return self._relay.is_hub_alive(hub_id)

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        hub = await self._relay._mongo.get_hub(hub_id)
        return hub.get("user_id") if hub else None


def init_relay_service(
    *,
    mongo: MongoDB,
    database_service: DatabaseService,
    sse_manager: SSEManager,
    room_message_center: object,
) -> RelayService:
    global relay_service
    svc = RelayService(
        mongo=mongo,
        database_service=database_service,
        sse_manager=sse_manager,
    )

    from modules.agent_response_handler import AgentResponseHandler
    from modules.transports.relay import RelayTransport

    handler = AgentResponseHandler(
        db=database_service, sse=sse_manager, room_message_center=room_message_center,
    )
    transport = RelayTransport(
        response_handler=handler,
        relay_service=svc,
        db=database_service,
        sse_manager=sse_manager,
    )
    svc.set_relay_transport(transport)

    relay_service = svc
    return svc
