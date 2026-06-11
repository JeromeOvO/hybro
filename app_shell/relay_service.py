"""Compatibility surface for legacy relay imports.

Phase 8 keeps this module import-compatible while HubRuntimeBridge owns the
runtime implementation behind the proxy.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app_shell.delivery_runtime import SSEManager
from app_shell.redis_runtime import AppShellLeaderElection, AppShellRelayStreamService
from common.config.settings import settings
from common.dto import (
    HubCancelCommand,
    HubDispatchCommand,
    HubPublishLineageSnapshot,
    HubReplyCommand,
    OfflineHubFailureCommand,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from hub_runtime_bridge.config import config_from_settings
from hub_runtime_bridge.facade import HubFacade
from models.api_key import APIKey
from models.hub import Hub, HubAgentSync, HubPublishRequest, HubStatus, RelayToHubEvent

logger = get_logger(__name__)


def _get_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class _RelayPublishAuthorizationReader:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def authorize_hub_publish(
        self, *, hub_id: str, owner_id: str, room_id: str, agent_message_id: str
    ) -> HubPublishLineageSnapshot | None:
        msg = await self._db.get_room_agent_message_by_message_id(agent_message_id)
        if not msg or _get_field(msg, "room_id") != room_id:
            return None
        agent_id = _get_field(msg, "agent_id")
        if not agent_id:
            return None
        agent = await self._db.get_agent_by_agent_id(agent_id)
        if not agent or _get_field(agent, "hub_id") != hub_id:
            return None
        related_message_id = _get_field(msg, "related_message_id")
        turn_id = _get_field(msg, "turn_id")
        root_user_message_id = turn_id or await self._resolve_root_user_message_id(
            related_message_id
        )
        lifecycle_message_id = turn_id or root_user_message_id
        return HubPublishLineageSnapshot(
            room_id=room_id,
            room_owner_id=owner_id,
            agent_message_id=agent_message_id,
            agent_id=agent_id,
            agent_hub_id=hub_id,
            related_message_id=related_message_id,
            turn_id=turn_id,
            run_id=_get_field(msg, "run_id"),
            root_user_message_id=root_user_message_id,
            lifecycle_message_id=lifecycle_message_id,
            client_request_id=_get_field(msg, "client_request_id"),
            cancellation_message_ids=[
                item
                for item in [agent_message_id, related_message_id, root_user_message_id]
                if item
            ],
        )

    async def _resolve_root_user_message_id(self, message_id: str | None) -> str | None:
        cursor = message_id
        visited: set[str] = set()
        for _ in range(20):
            if not isinstance(cursor, str) or not cursor or cursor in visited:
                return None
            visited.add(cursor)
            user_lookup = getattr(self._db, "get_room_user_message_by_message_id", None)
            if callable(user_lookup):
                user_msg = user_lookup(cursor)
                if hasattr(user_msg, "__await__"):
                    user_msg = await user_msg
                if _get_field(user_msg, "message_type") == "user":
                    return cursor
            parent_lookup = getattr(
                self._db, "get_room_agent_message_by_message_id", None
            )
            if not callable(parent_lookup):
                return cursor
            parent = parent_lookup(cursor)
            if hasattr(parent, "__await__"):
                parent = await parent
            if parent is None:
                return cursor
            parent_message_id = _get_field(parent, "message_id")
            if parent_message_id and parent_message_id != cursor:
                return cursor
            parent_turn_id = _get_field(parent, "turn_id")
            if isinstance(parent_turn_id, str) and parent_turn_id:
                return parent_turn_id
            cursor = _get_field(parent, "related_message_id")
        return None


class _RelayCancellationReader:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def is_message_cancelled(self, message_id: str) -> bool:
        return bool(await self._db.is_message_cancelled(message_id))


class _LegacyPublishSink:
    def __init__(
        self,
        relay: RelayService,
        *,
        response_converter: Callable[[Any], Any] | None = None,
    ) -> None:
        self._relay = relay
        self._response_converter = response_converter

    async def handle_hub_agent_response(self, event: Any) -> None:
        handler = self._relay._response_handler
        if handler is None:
            return
        converted = (
            self._response_converter(event) if self._response_converter else event
        )
        await handler.handle(converted)


def _default_hub_response_converter(event: Any) -> Any:
    payload = getattr(event, "payload", {}) or {}
    return SimpleNamespace(
        kind=payload.get("kind", "status_update"),
        room_id=getattr(event, "room_id", ""),
        message_id=payload.get("message_id", ""),
        agent_id=getattr(event, "agent_id", ""),
        task_id=getattr(event, "task_id", None),
        turn_id=payload.get("turn_id"),
        text=payload.get("text", "") or "",
        state=payload.get("state"),
        parts=payload.get("parts") if isinstance(payload.get("parts"), list) else None,
        artifacts=payload.get("artifacts")
        if isinstance(payload.get("artifacts"), list)
        else None,
        context_id=payload.get("context_id"),
        error_text=payload.get("error_text"),
        related_message_id=payload.get("related_message_id"),
        user_id=payload.get("user_id"),
        client_request_id=payload.get("client_request_id"),
        lifecycle_message_id=payload.get("lifecycle_message_id"),
        append=bool(payload.get("append", False)),
        last_chunk=bool(payload.get("last_chunk", False)),
        is_final=bool(payload.get("is_final", getattr(event, "is_terminal", False))),
        agent_name=payload.get("agent_name"),
        step_number=payload.get("step_number"),
        total_steps=payload.get("total_steps"),
        skip_persist=bool(payload.get("skip_persist", False)),
        s3_converted=bool(payload.get("s3_converted", False)),
        details=payload.get("details"),
    )


class _OfflineQueueEntry:
    __slots__ = ("event", "enqueued_at")

    def __init__(self, event: RelayToHubEvent) -> None:
        self.event = event
        self.enqueued_at = time.monotonic()


class RelayService:
    def __init__(
        self,
        *,
        mongo: Any,
        db: Any | None = None,
        legacy_store: Any | None = None,
        sse_manager: SSEManager,
        event_publisher: Any | None = None,
        worker_id: str | None = None,
        response_converter: Callable[[Any], Any] | None = None,
        offline_failure_port: Any,
    ) -> None:
        self._mongo = mongo
        self._mongo = (
            mongo
            if mongo is not None
            else getattr(legacy_store, "mongo", None)
            if legacy_store is not None
            else None
        )
        self._db = db if db is not None else legacy_store
        if self._db is None:
            raise ValueError("RelayService requires a mongo-compatible db/service")
        self._sse = sse_manager
        self._hub_queues: dict[str, asyncio.Queue] = {}
        self._offline_queues: dict[str, deque[_OfflineQueueEntry]] = {}
        self._last_hub_heartbeat: dict[str, float] = {}
        self._hub_disconnected_at: dict[str, float] = {}
        self._hub_disconnect_events: dict[str, asyncio.Event] = {}
        self._hub_liveness_cache: dict[str, bool] = {}
        self._shutdown = False
        self._heartbeat_task: asyncio.Task | None = None
        self._streams: AppShellRelayStreamService | None = None
        self._leader: AppShellLeaderElection | None = None
        self._agent_registry_writer = None
        self._publish_handler: Any | None = None
        self._relay_transport: Any | None = None
        self._response_handler: Any | None = None
        self._internal_response_dispatcher: Any | None = None
        self._response_converter = response_converter or _default_hub_response_converter
        self._offline_failure_port = offline_failure_port
        self.agent_call_counter = (
            mongo
            if callable(getattr(mongo, "increment_agent_call_count", None))
            else None
        )
        if self.agent_call_counter is None and callable(
            getattr(self._db, "increment_agent_call_count", None)
        ):
            self.agent_call_counter = self._db
        self._facade = HubFacade(
            mongo=self._mongo,
            config=config_from_settings(settings),
            worker_id=worker_id or "local-worker",
            agent_call_counter=self.agent_call_counter,
            event_publisher=event_publisher,
            publish_authorization_reader=_RelayPublishAuthorizationReader(self._db),
            cancellation_reader=_RelayCancellationReader(self._db),
            offline_failure_port=self._offline_failure_port,
        )

    @property
    def relay_transport(self) -> Any | None:
        return self._publish_handler

    @property
    def task_ownership_store(self) -> Any | None:
        return self._facade.task_ownership_store

    @property
    def ownership_lease_maintainer(self) -> Any | None:
        return self._facade.ownership_maintainer

    @property
    def worker_id(self) -> str:
        return self._facade.worker_id

    @property
    def internal_response_dispatcher(self) -> Any | None:
        return self._internal_response_dispatcher

    def _bind_internal_response_router(self) -> Any:
        router = self._facade.bind_internal_response_sink(
            _LegacyPublishSink(
                self,
                response_converter=self._response_converter,
            )
        )
        self._internal_response_dispatcher = router
        return router

    def set_relay_transport(self, transport: Any) -> None:
        self._publish_handler = transport
        self._relay_transport = transport
        response_handler = getattr(transport, "response_handler", None)
        if response_handler is not None:
            self.bind_response_handler(response_handler)
            return
        self._bind_internal_response_router()

    def bind_response_handler(self, response_handler: Any) -> None:
        self._response_handler = response_handler
        self._bind_internal_response_router()

    def set_stream_service(self, streams: AppShellRelayStreamService) -> None:
        self._streams = streams
        self._facade.bind_streams(streams)

    def set_leader_election(self, leader: AppShellLeaderElection | None) -> None:
        self._leader = leader

    def bind_agent_registry_writer(self, writer) -> None:
        self._agent_registry_writer = writer
        self._facade.bind_agent_registry_writer(writer)

    def _require_agent_registry_writer(self):
        if self._agent_registry_writer is None:
            raise RuntimeError("AgentRegistryWriter is not bound")
        return self._agent_registry_writer

    async def start(self) -> None:
        self._shutdown = False
        await self._facade.start()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        self._shutdown = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self._facade.stop()

    async def register_hub(self, hub_id: str, api_key: APIKey) -> Hub:
        hub = Hub(hub_id=hub_id, user_id=api_key.user_id, registered_at=utcnow())
        await self._mongo.upsert_hub(hub.model_dump(mode="json"))
        return hub

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        hub = await self._mongo.get_hub(hub_id)
        return hub.get("user_id") if hub else None

    async def connect_hub(
        self, hub_id: str, api_key: APIKey, last_event_id: str | None = None
    ) -> AsyncGenerator[dict, None]:
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
            await self._streams.record_heartbeat(hub_id)
            self._hub_liveness_cache[hub_id] = True
            old_event = self._hub_disconnect_events.get(hub_id)
            if old_event is not None:
                old_event.set()
            disconnect = asyncio.Event()
            self._hub_disconnect_events[hub_id] = disconnect
            yield {"type": "connection_ready"}
            start_id = last_event_id or "$"
            try:
                while not self._shutdown and not disconnect.is_set():
                    entries = await self._streams.read_events(
                        hub_id,
                        last_id=start_id,
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
                    hub_id, connection_id=connection_id, is_online=False
                )
                if result:
                    self._hub_liveness_cache[hub_id] = False
            return

        queue: asyncio.Queue = asyncio.Queue()
        old_queue = self._hub_queues.get(hub_id)
        if old_queue is not None:
            await old_queue.put({"type": "_disconnect"})
        self._hub_queues[hub_id] = queue
        self._last_hub_heartbeat[hub_id] = time.monotonic()
        self._hub_disconnected_at.pop(hub_id, None)
        yield {"type": "connection_ready"}

        offline = self._offline_queues.pop(hub_id, deque())
        now = time.monotonic()
        for entry in offline:
            if now - entry.enqueued_at < settings.relay_offline_queue_ttl:
                yield entry.event.model_dump(mode="json")

        try:
            while not self._shutdown:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=float(settings.relay_heartbeat_interval)
                    )
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
            return
        self._hub_queues.pop(hub_id, None)
        await self._preserve_pending_queue_events(hub_id, queue)
        self._last_hub_heartbeat.pop(hub_id, None)
        self._hub_disconnected_at[hub_id] = time.monotonic()
        result = await self._mongo.update_hub_status_if_current(
            hub_id, connection_id=connection_id, is_online=False
        )
        if result:
            await self._require_agent_registry_writer().mark_hub_agents_offline(hub_id)

    async def record_hub_heartbeat(self, hub_id: str, api_key: APIKey) -> None:
        if self._streams:
            hub_doc = await self._mongo.get_hub(hub_id)
            if not hub_doc or hub_doc["user_id"] != api_key.user_id:
                logger.warning(
                    "Hub %s heartbeat rejected: owner_id=%s caller_user_id=%s hub_exists=%s",
                    hub_id,
                    hub_doc.get("user_id") if hub_doc else None,
                    api_key.user_id,
                    hub_doc is not None,
                )
                raise PermissionError("Hub not owned by this API key")
            await self._streams.record_heartbeat(hub_id)
            self._hub_liveness_cache[hub_id] = True
            return
        if hub_id not in self._hub_queues:
            raise PermissionError(f"Hub {hub_id} is not connected")
        self._last_hub_heartbeat[hub_id] = time.monotonic()

    async def is_hub_alive(self, hub_id: str) -> bool:
        if self._streams:
            alive = await self._streams.is_hub_alive(hub_id)
        else:
            alive = hub_id in self._hub_queues
        self._hub_liveness_cache[hub_id] = bool(alive)
        return bool(alive)

    def _is_hub_connected_locally(self, hub_id: str) -> bool:
        return hub_id in self._hub_disconnect_events or hub_id in self._hub_queues

    def is_hub_alive_cached(self, hub_id: str) -> bool:
        if self._streams:
            return self._hub_liveness_cache.get(hub_id, False)
        return hub_id in self._hub_queues

    async def mark_hub_agents_offline(
        self, hub_id: str, connection_id: str | None = None
    ) -> None:
        if connection_id:
            result = await self._mongo.update_hub_status_if_current(
                hub_id, connection_id=connection_id, is_online=False
            )
            if not result:
                return
        else:
            await self._mongo.update_hub_status(hub_id, is_online=False)
        await self._require_agent_registry_writer().mark_hub_agents_offline(hub_id)

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
        self._require_agent_registry_writer()

        return await self._facade.sync_agents(
            hub_id,
            agents,
            hub_doc["user_id"],
            prune_missing=prune_missing,
        )

    async def push_to_hub(self, hub_id: str, event: RelayToHubEvent) -> bool:
        if self._streams:
            alive = await self._streams.is_hub_alive(hub_id)
            self._hub_liveness_cache[hub_id] = alive
            if not alive:
                logger.warning(
                    "Hub %s push rejected: redis_alive=False event_type=%s "
                    "agent_message_id=%s room_id=%s local_agent_id=%s",
                    hub_id,
                    event.type,
                    event.agent_message_id,
                    event.room_id,
                    event.local_agent_id,
                )
                await self._fail_offline_message(event, "Agent is offline")
                return False
            return bool(
                await self._streams.push_event(hub_id, event.model_dump(mode="json"))
            )

        queue = self._hub_queues.get(hub_id)
        if queue is not None:
            await queue.put(event.model_dump(mode="json"))
            return True

        if self._agent_registry_writer is not None:
            await self.mark_hub_agents_offline(hub_id)
        disconnected_at = self._hub_disconnected_at.get(hub_id)
        if disconnected_at is not None:
            elapsed = time.monotonic() - disconnected_at
            if elapsed > settings.relay_offline_grace_period:
                await self._fail_offline_message(
                    event, "Agent is offline — hub has been unreachable"
                )
                return False
        await self._queue_offline_event(hub_id, event)
        return False

    async def _queue_offline_event(self, hub_id: str, event: RelayToHubEvent) -> None:
        offline = self._offline_queues.setdefault(hub_id, deque())
        if len(offline) >= settings.relay_offline_queue_max:
            dropped = offline.popleft()
            await self._fail_offline_message(dropped.event)
        offline.append(_OfflineQueueEntry(event))

    async def _preserve_pending_queue_events(
        self, hub_id: str, queue: asyncio.Queue
    ) -> None:
        offline = self._offline_queues.setdefault(hub_id, deque())
        while not queue.empty():
            payload = queue.get_nowait()
            if not isinstance(payload, dict) or payload.get("type") == "_disconnect":
                continue
            try:
                event = RelayToHubEvent(**payload)
            except Exception as exc:
                logger.warning(
                    "Failed to preserve pending hub event for %s on disconnect: %s",
                    hub_id,
                    exc,
                )
                continue
            if len(offline) >= settings.relay_offline_queue_max:
                dropped = offline.popleft()
                await self._fail_offline_message(dropped.event)
            offline.append(_OfflineQueueEntry(event))

    async def _fail_offline_message(
        self, event: RelayToHubEvent, error_text: str | None = None
    ) -> None:
        await self._offline_failure_port.mark_hub_message_failed(
            OfflineHubFailureCommand(
                room_id=event.room_id,
                agent_message_id=event.agent_message_id,
                agent_id=event.agent_id,
                task_id=event.task_id,
                error_text=error_text
                or "Hub agent message expired (offline queue overflow)",
            )
        )

    async def process_publish(
        self, hub_id: str, request: HubPublishRequest, api_key: APIKey
    ) -> None:
        hub_doc = await self._mongo.get_hub(hub_id)
        if not hub_doc:
            raise PermissionError("Unknown hub")
        if hub_doc["user_id"] != api_key.user_id:
            raise PermissionError("Hub not owned by this API key")
        room = await self._db.get_room_by_room_id(request.room_id)
        if not room:
            raise ValueError(f"Room {request.room_id} not found")
        if hub_doc["user_id"] != room.room_owner_id:
            raise PermissionError("Hub owner does not match room owner")
        await self._facade.publish_from_hub(
            hub_id,
            {
                "room_id": request.room_id,
                "owner_id": hub_doc["user_id"],
                "events": [event.model_dump(mode="json") for event in request.events],
            },
        )

    async def cancel_relay_task(
        self,
        hub_id: str,
        agent_message_id: str,
        local_agent_id: str,
        task_id: str | None = None,
    ) -> bool:
        # Compatibility note: this method cannot delegate directly to HubFacade yet.
        # RelayService still owns legacy in-memory hub queues for non-stream mode.
        result = False
        if self._streams is not None:
            result = await self._facade.cancel_hub_task(
                HubCancelCommand(
                    hub_id=hub_id,
                    agent_message_id=agent_message_id,
                    local_agent_id=local_agent_id,
                    task_id=task_id,
                )
            )
        if result:
            return True
        return await self.push_to_hub(
            hub_id,
            RelayToHubEvent(
                type="cancel_task",
                agent_message_id=agent_message_id,
                local_agent_id=local_agent_id,
                task_id=task_id,
            ),
        )

    async def cancel_hub_task(self, command: HubCancelCommand) -> bool:
        return await self.cancel_relay_task(
            command.hub_id,
            command.agent_message_id,
            command.local_agent_id,
            command.task_id,
        )

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
        # Compatibility note: this method cannot delegate directly to HubFacade yet.
        # RelayService still owns legacy in-memory hub queues for non-stream mode.
        result = False
        if self._streams is not None:
            result = await self._facade.reply_to_hub_task(
                HubReplyCommand(
                    hub_id=hub_id,
                    agent_message_id=agent_message_id,
                    local_agent_id=local_agent_id,
                    room_id=room_id,
                    reply_text=reply_text,
                    task_id=task_id,
                    context_id=context_id,
                )
            )
        if result:
            return True
        return await self.push_to_hub(
            hub_id,
            RelayToHubEvent(
                type="user_reply",
                room_id=room_id,
                agent_message_id=agent_message_id,
                local_agent_id=local_agent_id,
                reply_text=reply_text,
                task_id=task_id,
                context_id=context_id,
            ),
        )

    async def reply_to_hub_task(self, command: HubReplyCommand) -> bool:
        return await self.reply_to_relay_task(
            command.hub_id,
            command.agent_message_id,
            command.local_agent_id,
            command.reply_text,
            command.room_id,
            task_id=command.task_id,
            context_id=command.context_id,
        )

    async def send_to_hub(self, command: HubDispatchCommand):
        # Compatibility note: this method cannot delegate directly to HubFacade yet.
        # RelayService still owns legacy in-memory hub queues for non-stream mode.
        delivered = await self.push_to_hub(
            command.hub_id,
            RelayToHubEvent(
                type="user_message",
                room_id=command.room_id,
                user_message_id=command.user_message_id,
                agent_message_id=command.agent_message_id,
                agent_id=command.agent_id,
                local_agent_id=command.local_agent_id,
                message=command.payload,
                task_id=command.task_id,
            ),
        )
        from common.dto import HubDispatchResult

        return HubDispatchResult(
            hub_id=command.hub_id,
            accepted=delivered,
            task_id=command.task_id,
            error=None if delivered else "hub_offline",
        )

    async def get_hub_status(self, user_id: str) -> list[HubStatus]:
        # Compatibility note: this method preserves the legacy HubStatus response
        # shape and count fields until a behavior-equivalent facade adapter exists.
        hubs = await self._mongo.get_hubs_by_user(user_id)
        result: list[HubStatus] = []
        for hub in hubs:
            hub_id = hub["hub_id"]
            online = await self.is_hub_alive(hub_id)
            active, inactive = await self._mongo.count_hub_agents(hub_id)
            result.append(
                HubStatus(
                    hub_id=hub_id,
                    is_online=online,
                    last_connected_at=hub.get("last_connected_at"),
                    agent_count=active + inactive,
                    active_agent_count=active,
                    inactive_agent_count=inactive,
                )
            )
        return result

    async def _heartbeat_loop(self) -> None:
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
        if self._leader:
            ttl = settings.relay_heartbeat_interval * 2
            acquired = await self._leader.try_acquire("relay_heartbeat_monitor", ttl)
            if not acquired:
                return
            try:
                await self._do_heartbeat_check(stale_threshold)
                await self.sweep_offline_queues()
            finally:
                await self._leader.release("relay_heartbeat_monitor")
            return
        await self._do_heartbeat_check(stale_threshold)
        await self.sweep_offline_queues()

    async def _do_heartbeat_check(self, stale_threshold: float) -> None:
        if self._streams:
            stale_events = await self._facade.sweep_stream_liveness()
            for stale in stale_events:
                disconnect = self._hub_disconnect_events.get(stale.hub_id)
                logger.warning(
                    "Hub %s heartbeat expired: redis_alive=False connection_id=%s "
                    "local_disconnect_event=%s",
                    stale.hub_id,
                    stale.connection_id,
                    disconnect is not None,
                )
                if disconnect is not None:
                    disconnect.set()
            return

        now = time.monotonic()
        for hub_id in list(self._hub_queues):
            last = self._last_hub_heartbeat.get(hub_id)
            if last is not None and (now - last) > stale_threshold:
                queue = self._hub_queues.get(hub_id)
                if queue:
                    await queue.put({"type": "_disconnect"})

    async def sweep_offline_queues(self) -> None:
        now = time.monotonic()
        for hub_id, offline in list(self._offline_queues.items()):
            while (
                offline
                and (now - offline[0].enqueued_at) >= settings.relay_offline_queue_ttl
            ):
                expired = offline.popleft()
                await self._fail_offline_message(expired.event)
            if not offline:
                self._offline_queues.pop(hub_id, None)


relay_service: RelayService | None = None


class RelayHubLivenessReader:
    def __init__(self, relay: RelayService) -> None:
        self._relay = relay

    async def is_hub_online(self, hub_id: str) -> bool:
        return await self._relay.is_hub_alive(hub_id)

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        return await self._relay.get_hub_owner_id(hub_id)


def init_relay_service(
    *,
    mongo: Any,
    db: Any | None = None,
    legacy_store: Any | None = None,
    sse_manager: SSEManager,
    room_message_center: object,
    hitl_coordinator: object | None = None,
    event_publisher: Any | None = None,
    worker_id: str | None = None,
    response_converter: Callable[[Any], Any] | None = None,
    offline_failure_port: Any | None = None,
) -> RelayService:
    resolved_db = db if db is not None else legacy_store
    if resolved_db is None:
        raise ValueError("init_relay_service requires db or legacy_store")
    if offline_failure_port is None:
        raise ValueError("init_relay_service requires offline_failure_port")
    global relay_service
    relay_service = RelayService(
        mongo=mongo,
        db=resolved_db,
        sse_manager=sse_manager,
        event_publisher=event_publisher,
        worker_id=worker_id,
        response_converter=response_converter,
        offline_failure_port=offline_failure_port,
    )
    response_handler = getattr(room_message_center, "agent_response_handler", None)
    if response_handler is None:
        raise ValueError(
            "init_relay_service requires room_message_center.agent_response_handler"
        )
    elif hitl_coordinator is not None:
        response_handler.hitl_coordinator = hitl_coordinator

    relay_service.bind_response_handler(response_handler)

    processor = getattr(room_message_center, "agent_message_processor", None)
    if processor is not None and hasattr(processor, "bind_relay_service"):
        processor.bind_relay_service(relay_service)
    elif processor is not None:
        processor.relay_service = relay_service
    return relay_service


__all__ = [
    "RelayHubLivenessReader",
    "RelayService",
    "init_relay_service",
    "relay_service",
]
