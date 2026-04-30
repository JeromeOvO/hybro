from __future__ import annotations

import asyncio
import json
import random
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from cachetools import TTLCache

from common.utils.cancellation import CancellationToken
from common.utils.logger import get_logger
from common.utils.time import utcnow
from config.settings import settings
from infrastructure.event_broker import EventBroker
from services.a2a_constants import PROCESSING_DONE_STATUSES, SSEProcessingStatus
from services.database_service import db_service
from services.run_command_handler import run_command_handler, run_event_sse_enabled
from services.run_projector import sync_room_processing_mirror

if TYPE_CHECKING:
    from infrastructure.redis_service import RedisService


def _enum_value(v: Any) -> Any:
    """Extract the .value from an Enum member (e.g. TaskState), or return as-is."""
    return v.value if isinstance(v, Enum) else v


logger = get_logger(__name__)


class SSEConnection:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.connection_id = str(uuid4())
        self.queue: asyncio.Queue = asyncio.Queue()
        self.connected_at = utcnow()
        self.is_active = True

    async def send_message(self, message_type: str, data: Any):
        """send message to connection"""
        if not self.is_active:
            return False

        try:
            message = {
                "type": message_type,
                "timestamp": utcnow().isoformat(),
                "room_id": self.room_id,
                "data": data,
            }
            await self.queue.put(json.dumps(message))
            return True
        except Exception as e:
            logger.error(
                f"Failed to send message to connection {self.connection_id}: {e}"
            )
            self.is_active = False
            return False

    async def get_message(self, timeout: float = 30.0) -> str | None:
        """get message from queue"""
        try:
            message = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            return message
        except TimeoutError:
            # send heartbeat (return directly, don't also queue it)
            heartbeat = {
                "type": "heartbeat",
                "timestamp": utcnow().isoformat(),
                "room_id": self.room_id,
            }
            return json.dumps(heartbeat)

    def close(self):
        """close the connection"""
        self.is_active = False


class SSEManager:
    def __init__(self):
        # room_id -> {connection_id: connection}
        self.room_connections: dict[str, dict[str, SSEConnection]] = {}
        self.lock = asyncio.Lock()

        # Message cancellation tracking — TTL cache auto-evicts entries that
        # are never cleared by a processing checkpoint (e.g. message already
        # finished or was never started).  maxsize is generous; TTL is the
        # primary eviction mechanism.
        self.cancelled_messages: TTLCache[str, bool] = TTLCache(
            maxsize=10_000, ttl=3600
        )
        self._db_collection = None
        self._change_stream_task = None
        self._shutdown_flag = False
        self._change_stream_connected = False

        # Deduplication cache for terminal processing statuses.
        # Prevents double-sends (e.g. CANCELED then COMPLETED) from reaching
        # the frontend.  Key: "room_id:message_id", value: first terminal
        # status sent.  5-minute TTL is well beyond any processing duration.
        self._terminal_status_sent: TTLCache[str, str] = TTLCache(
            maxsize=10_000, ttl=300
        )

        # CancellationToken store — keyed by message_id.  Tokens are created
        # when processing starts and looked up by the cancel endpoint /
        # change-stream watcher to signal cancellation instantly.
        self._cancellation_tokens: TTLCache[str, CancellationToken] = TTLCache(
            maxsize=10_000, ttl=3600
        )

        # Cross-instance event broker (Redis Pub/Sub or future MQ)
        self._instance_id: str = str(uuid4())
        self._broker: EventBroker | None = None
        self._broker_disconnect_warned: bool = False

        # Shared Redis key-value service (cancellation/dedup L2 cache)
        self._redis: RedisService | None = None

        # Draining flag — reject new connections during shutdown
        self._draining: bool = False

    async def _resolve_client_request_id(
        self, message_id: str | None, provided_client_request_id: str | None
    ) -> str | None:
        """Resolve best-effort client_request_id for SSE correlation."""
        if provided_client_request_id:
            return provided_client_request_id
        if not message_id:
            return None
        import inspect

        resolver = getattr(db_service, "resolve_client_request_id_for_message_id", None)
        if not callable(resolver):
            return None
        # unittest.mock.AsyncMock is a coroutine function but returns MagicMock by default;
        # only accept real string IDs for JSON-safe SSE payloads.
        if inspect.iscoroutinefunction(resolver):
            raw = await resolver(message_id)
        else:
            raw = resolver(message_id)
            if inspect.isawaitable(raw):
                raw = await raw
        if isinstance(raw, str) and raw.strip():
            return raw
        return None

    # ------------------------------------------------------------------
    # Event broker lifecycle
    # ------------------------------------------------------------------

    async def start_event_broker(self, broker: EventBroker) -> None:
        """Attach and start cross-instance event broker."""
        self._broker = broker
        # Register handlers for each message kind
        self._broker.set_handler("sse_event", self._on_sse_event)
        self._broker.set_handler("cancellation", self._on_cancellation_event)
        await self._broker.start()
        logger.info("Event broker attached (instance_id=%s)", self._instance_id)

    async def stop_event_broker(self) -> None:
        """Stop cross-instance event broker."""
        if self._broker:
            await self._broker.stop()
            self._broker = None
            logger.info("Event broker stopped")

    @property
    def broker_connected(self) -> bool:
        """Whether the event broker is connected (for /health endpoint)."""
        return self._broker is not None and self._broker.is_connected

    async def start_redis_service(self, redis_service: RedisService) -> None:
        """Attach RedisService for shared cancellation and dedup state."""
        self._redis = redis_service
        logger.info("SSEManager: RedisService attached for shared state")

    async def stop_redis_service(self) -> None:
        """Detach RedisService."""
        self._redis = None

    @property
    def redis_connected(self) -> bool:
        """Whether RedisService is connected (for /health endpoint)."""
        return self._redis is not None and self._redis.is_connected

    def set_draining(self, flag: bool) -> None:
        """Set draining mode. When draining, new connections are rejected."""
        self._draining = flag
        if flag:
            logger.info("SSEManager entering drain mode — rejecting new connections")

    # ------------------------------------------------------------------
    # Broker message handlers (incoming from other instances)
    # ------------------------------------------------------------------

    async def _on_sse_event(self, payload: dict[str, Any]) -> None:
        """Handle incoming SSE event from broker (other instances)."""
        if payload.get("origin") == self._instance_id:
            return  # Skip self — we already delivered locally in broadcast_to_room
        try:
            await self._deliver_to_local_connections(
                payload["room_id"], payload["type"], payload["data"]
            )
        except KeyError as e:
            logger.warning("Malformed sse_event from broker (missing %s), dropping", e)

    async def _on_cancellation_event(self, payload: dict[str, Any]) -> None:
        """Handle incoming cancellation from broker (other instances)."""
        if payload.get("origin") == self._instance_id:
            return  # Skip self — we already cancelled locally
        message_id = payload.get("message_id")
        if message_id:
            self.cancelled_messages[message_id] = True
            token = self._cancellation_tokens.get(message_id)
            if token is not None:
                token.cancel()
            # Persist to Redis L2 (ensures late-joining instances see it)
            if self._redis:
                try:
                    await self._redis.set_nx(
                        f"{settings.redis_cancel_key_prefix}{message_id}", "1", ex=3600,
                    )
                except Exception:
                    pass
            logger.info("Received cancellation via broker: %s", message_id)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def add_connection(self, room_id: str) -> SSEConnection:
        """add connection"""
        if self._draining:
            raise ConnectionRefusedError("Server is draining — rejecting new SSE connections")

        first_for_room = False
        async with self.lock:
            if room_id not in self.room_connections:
                self.room_connections[room_id] = {}
                first_for_room = True

            connection = SSEConnection(room_id)
            self.room_connections[room_id][connection.connection_id] = connection

            logger.info(
                f"SSE connection {connection.connection_id} added to room {room_id}"
            )

        # Subscribe to room's broker channel (outside lock to avoid holding lock during I/O)
        if first_for_room and self._broker:
            channel = f"{settings.redis_sse_channel_prefix}{room_id}"
            await self._broker.subscribe(channel)

        return connection

    async def remove_connection(self, room_id: str, connection_id: str):
        """remove connection"""
        room_empty = False
        async with self.lock:
            if (
                room_id in self.room_connections
                and connection_id in self.room_connections[room_id]
            ):
                connection = self.room_connections[room_id][connection_id]
                connection.close()
                del self.room_connections[room_id][connection_id]

                if not self.room_connections[room_id]:
                    del self.room_connections[room_id]
                    room_empty = True

                logger.info(
                    f"SSE connection {connection_id} removed from room {room_id}"
                )

        # Unsubscribe from room's broker channel (outside lock)
        if room_empty and self._broker:
            channel = f"{settings.redis_sse_channel_prefix}{room_id}"
            await self._broker.unsubscribe(channel)

    async def broadcast_to_room(self, room_id: str, message_type: str, data: Any):
        """Broadcast message to room — publishes to broker for cross-instance fan-out, then delivers locally."""
        if self._broker and self._broker.is_connected:
            self._broker_disconnect_warned = False
            try:
                channel = f"{settings.redis_sse_channel_prefix}{room_id}"
                await self._broker.publish(channel, {
                    "kind": "sse_event",
                    "origin": self._instance_id,
                    "room_id": room_id,
                    "type": message_type,
                    "data": data,
                })
            except Exception as e:
                logger.error(
                    "Cross-instance delivery failed for room %s: %s "
                    "(clients on other instances will NOT receive this event)",
                    room_id, e,
                )
        elif self._broker and not self._broker.is_connected:
            if not self._broker_disconnect_warned:
                self._broker_disconnect_warned = True
                logger.error(
                    "Event broker disconnected — events delivered locally only "
                    "(cross-instance delivery unavailable until reconnect)",
                )

        # Always deliver to local connections
        await self._deliver_to_local_connections(room_id, message_type, data)

    async def _deliver_to_local_connections(self, room_id: str, message_type: str, data: Any):
        """Deliver event to local SSE connections only (no broker publish).

        Snapshots connections under lock, then delivers outside the lock to
        avoid holding it during async I/O (send_message awaits).
        """
        # Snapshot under lock — fast, no I/O
        async with self.lock:
            room_conns = self.room_connections.get(room_id)
            if not room_conns:
                return  # No local connections — silent (normal for cross-instance)
            snapshot = list(room_conns.items())

        # Deliver outside lock — I/O-bound, does not block other rooms
        disconnected_ids: list[str] = []
        for connection_id, connection in snapshot:
            success = await connection.send_message(message_type, data)
            if not success:
                disconnected_ids.append(connection_id)

        # Re-acquire lock to clean up disconnected connections
        if disconnected_ids:
            async with self.lock:
                room_conns = self.room_connections.get(room_id)
                if room_conns:
                    for connection_id in disconnected_ids:
                        room_conns.pop(connection_id, None)

        if len(snapshot) - len(disconnected_ids) > 0:
            logger.info(
                f"SSE local delivery [{message_type}] to {len(snapshot) - len(disconnected_ids)} connection(s) in room {room_id}"
            )

    async def send_user_message(
        self, room_id: str, message_id: str, user_id: str, content: str
    ):
        """send user message"""
        data = {
            "message_id": message_id,
            "user_id": user_id,
            "content": content,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "user_message", data)

    async def send_agent_response(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        content: str,
        related_message_id: str = None,
        parts: list[dict] | None = None,
        client_request_id: str | None = None,
    ):
        """send agent response"""
        resolved_client_request_id = await self._resolve_client_request_id(
            message_id, client_request_id
        )
        data = {
            "message_id": message_id,
            "agent_id": agent_id,
            "content": content,
            "related_message_id": related_message_id,
            "timestamp": utcnow().isoformat(),
        }
        if resolved_client_request_id:
            data["client_request_id"] = resolved_client_request_id
        if parts:
            data["parts"] = parts
        await self.broadcast_to_room(room_id, "agent_response", data)

    async def send_error(self, room_id: str, error: str, message_id: str = None):
        """
        Send error event to room.

        Args:
            room_id: The room ID
            error: Error message
            message_id: Optional message ID related to the error
        """
        data = {
            "error": error,
            "message_id": message_id,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "error", data)

    async def send_rate_limit_error(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        reason: str,
        retry_after_seconds: int | None = None,
        user_requests_used: int = 0,
        user_requests_limit: int | None = None,
        system_requests_used: int = 0,
        system_requests_limit: int | None = None,
    ):
        """
        Send rate limit error event to room with detailed information.

        Args:
            room_id: The room ID
            message_id: The message ID that triggered the rate limit
            agent_id: The agent ID that was rate limited
            reason: Human-readable error message
            retry_after_seconds: Seconds until the user can retry
            user_requests_used: Number of requests made by this user
            user_requests_limit: Maximum requests allowed per user
            system_requests_used: Total requests to this agent
            system_requests_limit: Maximum total requests allowed
        """
        data = {
            "error": reason,
            "error_type": "rate_limit_exceeded",
            "message_id": message_id,
            "agent_id": agent_id,
            "retry_after_seconds": retry_after_seconds,
            "user_requests_used": user_requests_used,
            "user_requests_limit": user_requests_limit,
            "system_requests_used": system_requests_used,
            "system_requests_limit": system_requests_limit,
            "timestamp": utcnow().isoformat(),
        }
        await self.broadcast_to_room(room_id, "error", data)

    async def send_artifact_update(
        self,
        room_id: str,
        message_id: str,
        agent_id: str,
        artifact: Any,
        append: bool = False,
        last_chunk: bool = False,
        client_request_id: str | None = None,
    ):
        """
        Send artifact update event from A2A agent streaming.

        This is used when agents stream artifacts (files, data, documents)
        incrementally during task execution. Following A2A protocol section 7.2.3.

        Args:
            room_id: The room ID
            message_id: The message being generated
            agent_id: The agent sending the artifact
            artifact: The artifact data (dict from A2A TaskArtifactUpdateEvent)
            append: Whether to append to existing artifact
            last_chunk: Whether this is the final chunk
        """
        resolved_client_request_id = await self._resolve_client_request_id(
            message_id, client_request_id
        )
        data = {
            "message_id": message_id,
            "agent_id": agent_id,
            "artifact": artifact,
            "append": append,
            "last_chunk": last_chunk,
            "timestamp": utcnow().isoformat(),
        }
        if resolved_client_request_id:
            data["client_request_id"] = resolved_client_request_id
        await self.broadcast_to_room(room_id, "artifact_update", data)

    async def send_processing_status(
        self, room_id: str, status: str, message_id: str = None, details: str = None,
        client_request_id: str | None = None,
        agents: list[dict] | None = None,
    ):
        """Send processing status and persist to room for page refresh recovery.

        Args:
            room_id: The room ID
            status: An SSEProcessingStatus value or A2A TaskState string
            message_id: The user message ID being processed
            details: Optional details about the status
            client_request_id: Pass-through correlation ID from the frontend request.
                Included in the SSE payload so the frontend can map temp→real message IDs.
            agents: Optional list of agent dicts with agent_id and agent_name,
                used during Delegating phase to show agent names in the rail.
        """
        # Deduplicate terminal statuses — once a terminal status has been
        # sent for a (room, message) pair, suppress any subsequent terminal
        # status.  This is the safety net that makes double-send bugs harmless.
        if status in PROCESSING_DONE_STATUSES and message_id:
            dedup_key = f"{room_id}:{message_id}"
            # L1 fast check
            already_sent = self._terminal_status_sent.get(dedup_key)
            if already_sent is not None:
                logger.warning(
                    "Suppressing duplicate terminal status %s for %s (already sent %s, L1 hit)",
                    status,
                    dedup_key,
                    already_sent,
                )
                return
            # L2 Redis check (cross-instance dedup)
            if self._redis:
                try:
                    was_first = await self._redis.set_nx(
                        f"{settings.redis_terminal_key_prefix}{dedup_key}", status, ex=300,
                    )
                    if not was_first:
                        logger.warning(
                            "Suppressing duplicate terminal status %s for %s (Redis hit)",
                            status,
                            dedup_key,
                        )
                        self._terminal_status_sent[dedup_key] = status  # populate L1
                        return
                except Exception as e:
                    logger.warning("Redis terminal dedup check failed: %s", e)
            self._terminal_status_sent[dedup_key] = status  # L1 cache

        # Persist run lifecycle, then project room processing mirror from runs.
        last_run_event_payload: dict | None = None
        last_run_event_payload = await run_command_handler.record_processing_status(
            room_id=room_id,
            status=status,
            message_id=message_id,
            client_request_id=client_request_id,
            details=details,
        )
        await sync_room_processing_mirror(room_id)

        if run_event_sse_enabled() and last_run_event_payload:
            await self.broadcast_to_room(
                room_id,
                "run_event",
                {
                    "event_id": last_run_event_payload.get("event_id"),
                    "run_id": last_run_event_payload.get("run_id"),
                    "seq": last_run_event_payload.get("seq"),
                    "type": last_run_event_payload.get("type"),
                    "payload": last_run_event_payload.get("payload") or {},
                    "correlation_id": client_request_id,
                },
            )

        # Send SSE event to connected clients
        resolved_client_request_id = await self._resolve_client_request_id(
            message_id, client_request_id
        )
        data = {
            "status": status,
            "message_id": message_id,
            "details": details,
            "timestamp": utcnow().isoformat(),
        }
        if resolved_client_request_id:
            data["client_request_id"] = resolved_client_request_id
        if agents is not None:
            data["agents"] = agents
        await self.broadcast_to_room(room_id, "processing_status", data)

    async def send_task_submitted(
        self,
        room_id: str,
        message_id: str,
        task_id: str,
        agent_name: str,
        agent_id: str | None = None,
        status: Any = "working",
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        client_request_id: str | None = None,
    ):
        """
        Send task submitted event for long-running tasks.

        Args:
            room_id: The room ID
            message_id: The message ID (used for task tracking and frontend message identification)
            task_id: The agent's task ID
            agent_name: Name of the agent processing the task
            status: Initial status — TaskState enum or string (serialised automatically)
            created_at: Task creation timestamp (for consistent ordering)
            step_number: Current step number in the workflow (1-indexed)
            total_steps: Total number of steps in the workflow
            task_content: The task description/content being processed
        """
        resolved_client_request_id = await self._resolve_client_request_id(
            message_id, client_request_id
        )
        data = {
            "message_id": message_id,
            "task_id": _enum_value(task_id),
            "agent_name": agent_name,
            "agent_id": agent_id,
            "status": _enum_value(status),
            "related_message_id": related_message_id,
            "created_at": created_at or utcnow().isoformat(),
            "step_number": step_number,
            "total_steps": total_steps,
            "task_content": task_content,
            "timestamp": utcnow().isoformat(),
        }
        if resolved_client_request_id:
            data["client_request_id"] = resolved_client_request_id
        await self.broadcast_to_room(room_id, "task_submitted", data)

    async def send_task_update(
        self,
        room_id: str,
        message_id: str,
        status: Any,
        content: str | None = None,
        error: str | None = None,
        requires_input: bool = False,
        requires_auth: bool = False,
        status_message: str | None = None,
        agent_name: str | None = None,
        agent_id: str | None = None,
        related_message_id: str | None = None,
        created_at: str | None = None,
        step_number: int | None = None,
        total_steps: int | None = None,
        task_content: str | None = None,
        parts: list[dict] | None = None,
        client_request_id: str | None = None,
    ):
        """
        Send task update event when task state changes.
        """
        resolved_client_request_id = await self._resolve_client_request_id(
            message_id, client_request_id
        )
        data = {
            "message_id": message_id,
            "status": _enum_value(status),
            "content": content,
            "error": error,
            "requires_input": requires_input,
            "requires_auth": requires_auth,
            "status_message": status_message,
            "agent_name": agent_name,
            "agent_id": agent_id,
            "related_message_id": related_message_id,
            "created_at": created_at,
            "step_number": step_number,
            "total_steps": total_steps,
            "task_content": task_content,
            "timestamp": utcnow().isoformat(),
        }
        if resolved_client_request_id:
            data["client_request_id"] = resolved_client_request_id
        if parts:
            data["parts"] = parts
        await self.broadcast_to_room(room_id, "task_update", data)

    def get_room_status(self, room_id: str) -> dict:
        """get room status"""
        if room_id not in self.room_connections:
            return {
                "room_id": room_id,
                "active_connections": 0,
                "status": "no_connections",
            }

        return {
            "room_id": room_id,
            "active_connections": len(self.room_connections[room_id]),
            "status": "active",
        }

    # ============== Message Cancellation Methods ==============

    async def start_change_stream_watcher(self, db_collection):
        """
        Start watching MongoDB change stream for cancellation events.
        Should be called on application startup.

        Args:
            db_collection: MongoDB collection for cancelled_messages
        """
        self._db_collection = db_collection
        self._change_stream_task = asyncio.create_task(self._watch_cancellations())
        logger.info("Change stream watcher started for message cancellations")

    def _handle_change_event(self, change: dict) -> None:
        """Process a single change-stream event (non-async, pure in-memory).

        Adds the message to the cancelled set and signals the corresponding
        ``CancellationToken`` if one exists.
        """
        message_id = change["fullDocument"]["message_id"]
        self.cancelled_messages[message_id] = True
        token = self._cancellation_tokens.get(message_id)
        if token is not None:
            token.cancel()
        logger.info(f"Received cancellation via change stream: {message_id}")

    async def _consume_change_stream(self, change_stream, resume_token):
        """Iterate over a change stream cursor, processing each event.

        Returns the last resume token seen (may be the same as *resume_token*
        if no events were received before the cursor closed).
        """
        if resume_token is not None:
            logger.info("Reconnected to cancellation change stream (resumed)")
        else:
            logger.info("Connected to cancellation change stream")

        async for change in change_stream:
            if self._shutdown_flag:
                break
            try:
                self._handle_change_event(change)
            except KeyError as e:
                logger.error(f"Invalid change stream document: {e}")

            # Persist the resume token after each event so that a
            # reconnection doesn't replay it.
            resume_token = change.get("_id")

        return resume_token

    async def _watch_cancellations(self):
        """Background task that watches MongoDB change stream for cancellation inserts.

        Resilience features (Issue 17):
        - **Exponential backoff** with jitter on connection failures.
        - **Resume token** — after each event the resume token is saved so that
          reconnections resume from the last successfully processed event instead
          of replaying from "now".
        - **Health flag** — ``_change_stream_connected`` is kept up-to-date so
          the ``/health`` endpoint can report whether cross-instance cancellation
          propagation is operational.
        """
        resume_token: dict | None = None
        backoff_delay = settings.cs_backoff_base
        consecutive_failures = 0

        while not self._shutdown_flag:
            try:
                pipeline = [{"$match": {"operationType": "insert"}}]
                watch_kwargs: dict[str, Any] = {}
                if resume_token is not None:
                    watch_kwargs["resume_after"] = resume_token

                try:
                    async with self._db_collection.watch(
                        pipeline, **watch_kwargs
                    ) as change_stream:
                        self._change_stream_connected = True
                        backoff_delay = settings.cs_backoff_base
                        consecutive_failures = 0
                        resume_token = await self._consume_change_stream(
                            change_stream, resume_token
                        )
                finally:
                    self._change_stream_connected = False

            except asyncio.CancelledError:
                logger.info("Change stream watcher cancelled")
                break
            except Exception as e:
                consecutive_failures += 1
                if not self._shutdown_flag:
                    # If the resume token is stale (oplog rolled past it),
                    # MongoDB will reject every reconnect.  After a few
                    # consecutive failures, fall back to "start from now".
                    if consecutive_failures >= 3 and resume_token is not None:
                        logger.warning(
                            "Clearing stale resume token after %d consecutive "
                            "failures — will resume from current oplog position",
                            consecutive_failures,
                        )
                        resume_token = None

                    jitter = backoff_delay * settings.cs_jitter_fraction
                    delay = backoff_delay + random.uniform(-jitter, jitter)
                    logger.warning(
                        "Change stream error: %s. Reconnecting in %.1fs "
                        "(backoff=%.1fs)...",
                        e,
                        delay,
                        backoff_delay,
                    )
                    await asyncio.sleep(delay)
                    backoff_delay = min(
                        backoff_delay * settings.cs_backoff_factor, settings.cs_backoff_max
                    )

    async def stop_change_stream_watcher(self):
        """Stop the change stream watcher. Should be called on application shutdown."""
        self._shutdown_flag = True
        if self._change_stream_task:
            self._change_stream_task.cancel()
            try:
                await self._change_stream_task
            except asyncio.CancelledError:
                logger.info("Change stream watcher stopped")

    @property
    def change_stream_connected(self) -> bool:
        """Whether the change stream watcher is currently connected.

        Returns ``True`` when the watcher has an open change stream cursor and
        is actively listening for cancellation events.  Returns ``False`` when
        the watcher is reconnecting, has not been started, or has been stopped.

        Intended for use by the ``/health`` endpoint so operators can detect
        degraded cross-instance cancellation propagation.
        """
        return self._change_stream_connected

    def cancel_message(self, message_id: str) -> None:
        """
        Mark a message as cancelled (local cache only).
        Actual persistence to MongoDB should be done separately.

        If a ``CancellationToken`` exists for this message_id the internal
        ``asyncio.Event`` is set immediately, unblocking any coroutine in
        ``token.race()`` without waiting for the next polling checkpoint.

        Args:
            message_id: The message ID to cancel
        """
        self.cancelled_messages[message_id] = True
        token = self._cancellation_tokens.get(message_id)
        if token is not None:
            token.cancel()
        logger.info(f"Message {message_id} marked as cancelled in local cache")

    async def cancel_message_and_broadcast(self, message_id: str) -> None:
        """Cancel locally AND broadcast to other instances via broker.

        Called by the cancel endpoint. The sync cancel_message() is kept
        for internal use (change stream handler, etc.) that shouldn't re-broadcast.
        """
        self.cancel_message(message_id)

        # L2 Redis key (durable even if Pub/Sub missed)
        if self._redis:
            try:
                await self._redis.set_nx(
                    f"{settings.redis_cancel_key_prefix}{message_id}", "1", ex=3600,
                )
            except Exception as e:
                logger.warning("Redis cancel key write failed: %s", e)

        if self._broker and self._broker.is_connected:
            self._broker_disconnect_warned = False
            try:
                await self._broker.publish(settings.redis_cancel_channel, {
                    "kind": "cancellation",
                    "origin": self._instance_id,
                    "message_id": message_id,
                })
            except Exception as e:
                logger.error(
                    "Cross-instance cancellation failed for message %s: %s",
                    message_id, e,
                )
        elif self._broker and not self._broker.is_connected:
            if not self._broker_disconnect_warned:
                self._broker_disconnect_warned = True
                logger.error(
                    "Event broker disconnected — events delivered locally only "
                    "(cross-instance delivery unavailable until reconnect)",
                )

    def is_cancelled(self, message_id: str) -> bool:
        """
        Check if a message has been cancelled.
        Uses local cache updated by change stream.

        .. deprecated::
            Prefer ``CancellationToken.is_cancelled`` or ``token.check()``
            which are threaded through ``ProcessingContext``.  This method
            is kept for backward-compatibility during the migration.

        Args:
            message_id: The message ID to check

        Returns:
            True if message is cancelled, False otherwise
        """
        return message_id in self.cancelled_messages

    async def check_cancelled(self, message_id: str) -> bool:
        """Async cancellation check: L1 TTLCache -> Redis L2.

        Use this for non-hot-path checks. Hot-path code should use
        ``message_id in self.cancelled_messages`` (L1 only) which is
        populated by broker handlers and cancel_message callers.
        """
        if message_id in self.cancelled_messages:
            return True
        if self._redis:
            try:
                if await self._redis.exists(f"{settings.redis_cancel_key_prefix}{message_id}"):
                    self.cancelled_messages[message_id] = True  # populate L1
                    return True
            except Exception:
                pass
        return False

    def clear_cancellation(self, message_id: str) -> None:
        """
        Clear cancellation flag for a message.
        Should be called after workflow completes to clean up memory.

        Args:
            message_id: The message ID to clear
        """
        self.cancelled_messages.pop(message_id, None)
        self._cancellation_tokens.pop(message_id, None)
        logger.debug(f"Cleared cancellation flag for message {message_id}")

    # ------------------------------------------------------------------
    # CancellationToken management (A-3)
    # ------------------------------------------------------------------

    def create_token(self, message_id: str) -> CancellationToken:
        """Create and store a ``CancellationToken`` for *message_id*.

        If the message was already marked as cancelled (e.g. the cancel
        request arrived before processing started), the token is pre-
        signalled so the very first ``token.check()`` raises immediately.

        Returns the newly created token.
        """
        token = CancellationToken(message_id=message_id)
        # Pre-signal if the message was cancelled before the token existed
        if message_id in self.cancelled_messages:
            token.cancel()
        self._cancellation_tokens[message_id] = token
        return token

    def get_token(self, message_id: str) -> CancellationToken | None:
        """Return the ``CancellationToken`` for *message_id*, or ``None``."""
        return self._cancellation_tokens.get(message_id)

    def remove_token(self, message_id: str) -> None:
        """Remove (but do not cancel) the token for *message_id*."""
        self._cancellation_tokens.pop(message_id, None)

# global SSE manager instance
sse_manager = SSEManager()
