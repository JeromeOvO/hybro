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
import hashlib
import time
from collections import deque
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from common.utils.connection_token import (
    create_connection_token,
    verify_connection_token,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from config.settings import settings
from models.hub import (
    Hub,
    HubAgentSync,
    HubPublishRequest,
    HubStatus,
    RelayToHubEvent,
)
from services.agent_service import normalize_agent_url

if TYPE_CHECKING:
    from database.mongodb import MongoDB
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

        # Background indexing tasks kept alive to prevent GC
        self._background_tasks: set[asyncio.Task] = set()

        # hub_id -> monotonic timestamp of last hub-initiated heartbeat
        self._last_hub_heartbeat: dict[str, float] = {}

        # hub_id -> monotonic timestamp when _disconnect_hub was called;
        # used to distinguish transient blips from genuine offline.
        self._hub_disconnected_at: dict[str, float] = {}

        self._heartbeat_task: asyncio.Task | None = None
        self._shutdown = False

        # Set eagerly by init_relay_service(); must not be None at request time.
        self._relay_transport: Any | None = None

    def set_relay_transport(self, transport: Any) -> None:
        """Wire up the RelayTransport so publish events can be delegated."""
        self._relay_transport = transport

    @property
    def relay_transport(self) -> Any | None:
        """Public read accessor for the eagerly-initialised transport."""
        return self._relay_transport

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
        self, hub_id: str, api_key: APIKey
    ) -> AsyncGenerator[dict, None]:
        """Long-lived SSE generator for a hub.

        On connect: marks hub online, delivers offline queue, then yields
        events as they arrive.  On disconnect: marks hub offline.
        """
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc or hub_doc["user_id"] != api_key.user_id:
            raise PermissionError("Hub not owned by this API key")

        # Issue connection token
        token = create_connection_token(
            hub_id, settings.relay_connection_token_secret
        )
        connection_id = str(uuid4())
        await self._mongo.update_hub_status(
            hub_id,
            is_online=True,
            last_connected_at=utcnow(),
            connection_token=token,
            connection_id=connection_id,
        )

        queue: asyncio.Queue = asyncio.Queue()

        old_queue = self._hub_queues.get(hub_id)
        if old_queue is not None:
            logger.info("Hub %s reconnecting — signaling stale connection", hub_id)
            await old_queue.put({"type": "_disconnect"})

        self._hub_queues[hub_id] = queue
        self._last_hub_heartbeat[hub_id] = time.monotonic()
        self._hub_disconnected_at.pop(hub_id, None)

        # Deliver connection token first
        yield {"type": "connection_token", "connection_token": token}

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
        await self._mongo.agents_collection.update_many(
            {"hub_id": hub_id},
            {"$set": {"agent_status": "inactive"}},
        )
        logger.info("Hub %s disconnected", hub_id)

    # ------------------------------------------------------------------
    # Hub heartbeat (hub-initiated liveness signal)
    # ------------------------------------------------------------------

    def record_hub_heartbeat(self, hub_id: str, api_key: APIKey) -> None:
        """Record a hub-initiated heartbeat.  Only accepted for connected hubs."""
        if hub_id not in self._hub_queues:
            raise PermissionError(
                f"Hub {hub_id} is not connected — heartbeat rejected"
            )
        self._last_hub_heartbeat[hub_id] = time.monotonic()

    def is_hub_connected(self, hub_id: str) -> bool:
        """Live check — is this hub currently connected via SSE?"""
        return hub_id in self._hub_queues

    async def mark_hub_agents_offline(self, hub_id: str) -> None:
        """Eagerly correct stale agent status for a disconnected hub."""
        await self._mongo.update_hub_status(hub_id, is_online=False)
        await self._mongo.agents_collection.update_many(
            {"hub_id": hub_id, "agent_status": "active"},
            {"$set": {"agent_status": "inactive"}},
        )

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

        user_id = api_key.user_id
        gateway_base = settings.gateway_base_url

        synced: list[dict] = []
        to_index: list[tuple[str, str, str]] = []
        for ag in agents:
            agent_url = ag.agent_card.get("url", "")
            normalized = normalize_agent_url(agent_url) if agent_url else None

            # Check if an agent with this URL already exists (e.g. registered
            # via the web UI).  If so, enrich it with hub metadata but keep its
            # original source so that direct-call routing continues to work.
            existing = None
            if normalized:
                existing = await self._mongo.agents_collection.find_one(
                    {"normalized_url": normalized, "provider_id": user_id}
                )

            if existing:
                await self._mongo.agents_collection.update_one(
                    {"agent_id": existing["agent_id"]},
                    {"$set": {
                        "hub_id": hub_id,
                        "local_agent_id": ag.local_agent_id,
                        "agent_card": ag.agent_card,
                    }},
                )
                stored_id = existing["agent_id"]
            else:
                new_agent_id = str(uuid4().hex)
                agent_data = {
                    "agent_id": new_agent_id,
                    "source": "hub",
                    "hub_id": hub_id,
                    "local_agent_id": ag.local_agent_id,
                    "provider_id": user_id,
                    "agent_card": ag.agent_card,
                    "normalized_url": normalized,
                    "agent_status": "active",
                    "is_public": True,
                }
                stored_id = await self._mongo.upsert_hub_agent(
                    hub_id, ag.local_agent_id, agent_data
                )

            # Re-index in Pinecone whenever the description changed or was
            # never successfully indexed.  We store a hash of the last-indexed
            # description so that transient Pinecone failures are retried on
            # the next sync and description updates are always picked up.
            description = ag.agent_card.get("description") or ag.description
            if description:
                new_hash = hashlib.sha256(description.encode()).hexdigest()
                doc = await self._mongo.agents_collection.find_one(
                    {"agent_id": stored_id},
                    {"indexed_description_hash": 1},
                )
                old_hash = (doc or {}).get("indexed_description_hash")
                if new_hash != old_hash:
                    to_index.append((stored_id, description, new_hash))

            # Set public_url to the gateway proxy so external consumers can
            # discover agents via the gateway API.  Never overwrite
            # agent_card.url — it must remain the real agent endpoint for
            # internal health checks, probes, and direct-call fallback.
            if gateway_base:
                proxy_url = (
                    f"{gateway_base}/gateway/agents/{stored_id}/message/send"
                )
                await self._mongo.agents_collection.update_one(
                    {"agent_id": stored_id},
                    {"$set": {"public_url": proxy_url}},
                )

            synced.append({
                "agent_id": stored_id,
                "local_agent_id": ag.local_agent_id,
            })

        if to_index:
            task = asyncio.create_task(self._index_agents(hub_id, to_index))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        # Prune agents that the hub no longer reports.
        if prune_missing:
            synced_ids = [item["agent_id"] for item in synced]

            await self._mongo.agents_collection.update_many(
                {
                    "hub_id": hub_id,
                    "source": "hub",
                    "agent_id": {"$nin": synced_ids},
                },
                {"$set": {"agent_status": "inactive"}},
            )

            await self._mongo.agents_collection.update_many(
                {
                    "hub_id": hub_id,
                    "source": {"$ne": "hub"},
                    "agent_id": {"$nin": synced_ids},
                },
                {
                    "$set": {"agent_status": "inactive"},
                    "$unset": {
                        "hub_id": "",
                        "local_agent_id": "",
                    },
                },
            )

        logger.info("Hub %s synced %d agents", hub_id, len(synced))

        # Activate synced agents if this hub is still connected.
        if hub_id in self._hub_queues and synced:
            synced_ids = [item["agent_id"] for item in synced]
            await self._mongo.agents_collection.update_many(
                {"hub_id": hub_id, "agent_id": {"$in": synced_ids}},
                {"$set": {"agent_status": "active"}},
            )

        return synced

    async def _index_agents(
        self, hub_id: str, to_index: list[tuple[str, str, str]]
    ) -> None:
        """Embed descriptions and upsert to Pinecone in the background.

        Each entry in *to_index* is ``(agent_id, description, desc_hash)``.
        After a successful Pinecone upsert the corresponding
        ``indexed_description_hash`` is written back to Mongo so that
        unchanged descriptions are not re-embedded on subsequent syncs and
        transient failures are automatically retried.
        """
        try:
            embed_tasks = [
                self._db.ai_service.get_embedding(desc)
                for _, desc, _ in to_index
            ]
            embeddings = await asyncio.gather(
                *embed_tasks, return_exceptions=True
            )

            vectors: list[dict] = []
            succeeded: list[tuple[str, str]] = []
            failed: list[str] = []
            for (agent_id, _, desc_hash), emb in zip(
                to_index, embeddings, strict=True
            ):
                if isinstance(emb, BaseException):
                    failed.append(agent_id)
                    continue
                vectors.append({
                    "id": agent_id,
                    "values": emb,
                    "metadata": {"type": "a2a_agent", "agent_id": agent_id},
                })
                succeeded.append((agent_id, desc_hash))

            if vectors:
                await asyncio.to_thread(self._db.pinecone.upsert, vectors)

            for agent_id, desc_hash in succeeded:
                await self._mongo.agents_collection.update_one(
                    {"agent_id": agent_id},
                    {"$set": {"indexed_description_hash": desc_hash}},
                )

            if failed:
                logger.warning(
                    "Hub %s: failed to index %d/%d agents in Pinecone: %s",
                    hub_id, len(failed), len(to_index), failed,
                )
            else:
                logger.info(
                    "Hub %s: indexed %d agents in Pinecone",
                    hub_id, len(vectors),
                )
        except Exception:
            logger.exception(
                "Hub %s: Pinecone batch index failed", hub_id
            )

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
        queue = self._hub_queues.get(hub_id)
        if queue is not None:
            await queue.put(event.model_dump(mode="json"))
            return True

        await self.mark_hub_agents_offline(hub_id)

        # If disconnected beyond the grace period, don't queue — let the
        # caller surface an immediate error to the user.
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

        # Within grace period (or hub never connected on this instance) — queue
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
        connection_token: str,
    ) -> None:
        """Process events published by a hub daemon."""
        # 1. Verify JWT
        if not verify_connection_token(
            connection_token, hub_id, settings.relay_connection_token_secret
        ):
            raise PermissionError("Invalid or expired connection token")

        # 2. Verify room ownership
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc:
            raise PermissionError("Unknown hub")

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
            agent_count = await self._mongo.count_hub_agents(h["hub_id"])
            result.append(
                HubStatus(
                    hub_id=h["hub_id"],
                    is_online=h.get("is_online", False),
                    last_connected_at=h.get("last_connected_at"),
                    agent_count=agent_count,
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
                await self.sweep_offline_queues()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in heartbeat loop")

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


def init_relay_service(
    *,
    mongo: MongoDB,
    database_service: DatabaseService,
    sse_manager: SSEManager,
    room_message_center: object,
) -> RelayService:
    global relay_service
    if not settings.relay_connection_token_secret:
        logger.warning(
            "RELAY_CONNECTION_TOKEN_SECRET is empty — hub /publish "
            "authentication will reject all requests until a secret is set"
        )
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
