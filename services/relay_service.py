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
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
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

        # hub_id -> missed heartbeats counter
        self._heartbeat_misses: dict[str, int] = {}

        self._heartbeat_task: asyncio.Task | None = None
        self._shutdown = False

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
        await self._mongo.set_hub_agents_online_status(
            hub_id, True, connection_id=connection_id
        )

        queue: asyncio.Queue = asyncio.Queue()

        old_queue = self._hub_queues.get(hub_id)
        if old_queue is not None:
            logger.info("Hub %s reconnecting — signaling stale connection", hub_id)
            await old_queue.put({"type": "_disconnect"})

        self._hub_queues[hub_id] = queue
        self._heartbeat_misses[hub_id] = 0

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
                    self._heartbeat_misses[hub_id] = 0
                    yield event
                except TimeoutError:
                    self._heartbeat_misses[hub_id] = 0
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
        self._heartbeat_misses.pop(hub_id, None)
        result = await self._mongo.update_hub_status_if_current(
            hub_id, connection_id=connection_id, is_online=False
        )
        if not result:
            logger.info(
                "Hub %s: connection superseded, skipping agent status update",
                hub_id,
            )
            return
        await self._mongo.set_hub_agents_online_status(
            hub_id, False, connection_id=connection_id
        )
        logger.info("Hub %s disconnected", hub_id)

    # ------------------------------------------------------------------
    # Agent sync
    # ------------------------------------------------------------------

    async def sync_agents(
        self, hub_id: str, agents: list[HubAgentSync], api_key: APIKey
    ) -> list[dict]:
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc or hub_doc["user_id"] != api_key.user_id:
            raise PermissionError("Hub not owned by this API key")

        user_id = api_key.user_id
        gateway_base = settings.gateway_base_url

        synced: list[dict] = []
        to_index: list[tuple[str, str]] = []
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
                        "hub_owner_id": user_id,
                        "local_agent_id": ag.local_agent_id,
                        "is_hub_online": hub_id in self._hub_queues,
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
                    "hub_owner_id": user_id,
                    "local_agent_id": ag.local_agent_id,
                    "is_hub_online": hub_id in self._hub_queues,
                    "provider_id": user_id,
                    "agent_card": ag.agent_card,
                    "normalized_url": normalized,
                    "agent_status": "active",
                    "is_public": True,
                }
                stored_id = await self._mongo.upsert_hub_agent(
                    hub_id, ag.local_agent_id, agent_data
                )

                # Only index on first insert — upsert_hub_agent uses
                # $setOnInsert for agent_id, so a match means re-sync.
                is_new_insert = stored_id == new_agent_id
                description = ag.agent_card.get("description") or ag.description
                if is_new_insert and description:
                    to_index.append((stored_id, description))

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
            task = asyncio.create_task(self._index_new_agents(hub_id, to_index))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        # Prune agents that the hub no longer reports.
        # Skip when the payload is empty to guard against accidental wipes.
        if synced:
            synced_ids = [item["agent_id"] for item in synced]

            await self._mongo.agents_collection.update_many(
                {
                    "hub_id": hub_id,
                    "source": "hub",
                    "agent_id": {"$nin": synced_ids},
                },
                {"$set": {"agent_status": "inactive", "is_hub_online": False}},
            )

            await self._mongo.agents_collection.update_many(
                {
                    "hub_id": hub_id,
                    "source": {"$ne": "hub"},
                    "agent_id": {"$nin": synced_ids},
                },
                {
                    "$unset": {
                        "hub_id": "",
                        "local_agent_id": "",
                        "hub_owner_id": "",
                        "is_hub_online": "",
                        "hub_connection_id": "",
                    },
                },
            )

        logger.info("Hub %s synced %d agents", hub_id, len(synced))
        return synced

    async def _index_new_agents(
        self, hub_id: str, to_index: list[tuple[str, str]]
    ) -> None:
        """Embed descriptions and upsert to Pinecone in the background.

        Runs as a fire-and-forget task so sync_agents() returns immediately.
        Embeddings are fetched in parallel; the Pinecone upsert is offloaded
        to a thread to avoid blocking the event loop.
        """
        try:
            embed_tasks = [
                self._db.ai_service.get_embedding(desc)
                for _, desc in to_index
            ]
            embeddings = await asyncio.gather(
                *embed_tasks, return_exceptions=True
            )

            vectors: list[dict] = []
            failed: list[str] = []
            for (agent_id, _), emb in zip(to_index, embeddings, strict=True):
                if isinstance(emb, BaseException):
                    failed.append(agent_id)
                    continue
                vectors.append({
                    "id": agent_id,
                    "values": emb,
                    "metadata": {"type": "a2a_agent", "agent_id": agent_id},
                })

            if vectors:
                await asyncio.to_thread(self._db.pinecone.upsert, vectors)

            if failed:
                logger.warning(
                    "Hub %s: failed to index %d/%d agents in Pinecone: %s",
                    hub_id, len(failed), len(to_index), failed,
                )
            else:
                logger.info(
                    "Hub %s: indexed %d new agents in Pinecone",
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
        """Push an event to a connected hub, or queue for offline delivery."""
        queue = self._hub_queues.get(hub_id)
        if queue is not None:
            await queue.put(event.model_dump(mode="json"))
            return True

        # Offline: enqueue
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

    async def _fail_offline_message(self, event: RelayToHubEvent) -> None:
        """Mark a RoomAgentMessage as failed when its offline queue entry is evicted."""
        if not event.agent_message_id:
            return
        error_text = "Hub agent message expired (offline queue overflow)"
        msg = await self._db.get_room_agent_message_by_message_id(
            event.agent_message_id
        )
        if msg:
            if msg.message_content is None:
                from models.room import MessageContent
                msg.message_content = MessageContent()
            msg.message_content.message_text = error_text
            if msg.message_content.message_task:
                task = msg.message_content.message_task
                from a2a.types import Message as A2AMessage
                from a2a.types import Role, TaskState, TaskStatus, TextPart
                task.status = TaskStatus(
                    state=TaskState.failed,
                    message=A2AMessage(
                        role=Role.agent,
                        parts=[TextPart(text=error_text)],
                    ),
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
                ev.type, ev.agent_message_id, ev.data, request.room_id
            )

    async def _process_single_publish_event(
        self,
        event_type: str,
        agent_message_id: str,
        data: dict,
        room_id: str,
    ) -> None:
        msg = await self._db.get_room_agent_message_by_message_id(
            agent_message_id
        )
        if not msg:
            logger.warning(
                "Publish event for unknown agent_message_id %s", agent_message_id
            )
            return

        if msg.room_id != room_id:
            logger.warning(
                "agent_message_id %s belongs to room %s, not %s",
                agent_message_id, msg.room_id, room_id,
            )
            return

        if event_type == "task_submitted":
            await self._sse.send_task_submitted(
                room_id=room_id,
                message_id=agent_message_id,
                task_id=data.get("task_id", ""),
                agent_name=data.get("agent_name", ""),
                agent_id=msg.agent_id,
                status="working",
                related_message_id=msg.related_message_id,
            )

        elif event_type == "agent_token":
            await self._sse.send_agent_token(
                room_id=room_id,
                message_id=agent_message_id,
                agent_id=msg.agent_id or "",
                token=data.get("token", ""),
            )

        elif event_type == "agent_response":
            response_text = data.get("content", "")
            parts = data.get("parts")
            # Update the pre-created RoomAgentMessage
            if msg.message_content:
                msg.message_content.message_text = response_text
                # Transition task status to completed so DB hydration renders
                # the message as an agent-bubble instead of a task-status card.
                task = msg.message_content.message_task
                if task and task.status:
                    from a2a.types import TaskState, TaskStatus
                    from services.a2a_constants import is_terminal_state

                    if not is_terminal_state(task.status.state):
                        task.status = TaskStatus(state=TaskState.completed)
            await self._db.update_room_agent_message_by_message_id(
                agent_message_id, msg
            )
            await self._sse.send_agent_response(
                room_id=room_id,
                message_id=agent_message_id,
                agent_id=msg.agent_id or "",
                content=response_text,
                related_message_id=msg.related_message_id,
                parts=parts,
            )
            # Resume orchestration
            await self._resume_orchestration(agent_message_id, response_text)

        elif event_type == "processing_status":
            await self._sse.send_processing_status(
                room_id=room_id,
                status=data.get("status", "completed"),
                message_id=data.get("user_message_id"),
                details=data.get("details"),
            )

    async def _resume_orchestration(
        self, agent_message_id: str, response_text: str
    ) -> None:
        """Resume paused queue/supervisor orchestration after hub publishes a response."""
        from modules.RoomMessageCenter import room_message_center

        try:
            resumed = await room_message_center.resume_queue_from_continuation(
                message_id=agent_message_id,
                task_result_text=response_text,
            )
            if resumed:
                logger.info(
                    "Orchestration resumed for agent_message %s",
                    agent_message_id,
                )
        except Exception:
            logger.exception(
                "Failed to resume orchestration for agent_message %s",
                agent_message_id,
            )

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
        while not self._shutdown:
            try:
                await asyncio.sleep(settings.relay_heartbeat_interval)
                for hub_id in list(self._hub_queues.keys()):
                    misses = self._heartbeat_misses.get(hub_id, 0) + 1
                    self._heartbeat_misses[hub_id] = misses
                    if misses >= settings.relay_hub_agent_heartbeat_miss_limit:
                        logger.warning(
                            "Hub %s missed %d heartbeats — disconnecting",
                            hub_id,
                            misses,
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
) -> RelayService:
    global relay_service
    if not settings.relay_connection_token_secret:
        logger.warning(
            "RELAY_CONNECTION_TOKEN_SECRET is empty — hub /publish "
            "authentication will reject all requests until a secret is set"
        )
    relay_service = RelayService(
        mongo=mongo,
        database_service=database_service,
        sse_manager=sse_manager,
    )
    return relay_service
