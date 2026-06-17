"""Compatibility surface for legacy relay imports.

Phase 8 keeps this module import-compatible while HubRuntimeBridge owns the
runtime implementation behind the proxy.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app_shell.delivery_runtime import SSEManager
from app_shell.redis_runtime import AppShellLeaderElection, AppShellRelayStreamService
from common.dto import (
    HubCancelCommand,
    HubDispatchCommand,
    HubDispatchResult,
    HubPublishLineageSnapshot,
    HubReplyCommand,
    OfflineHubFailureCommand,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from hub_runtime_bridge.config import HubRuntimeBridgeConfig
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
        config: HubRuntimeBridgeConfig | None = None,
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
        self._agent_registry_writer = None
        self._publish_handler: Any | None = None
        self._relay_transport: Any | None = None
        self._response_handler: Any | None = None
        self._internal_response_dispatcher: Any | None = None
        self._response_converter = response_converter or _default_hub_response_converter
        self._offline_failure_port = offline_failure_port
        self._config = config or HubRuntimeBridgeConfig()
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
            config=self._config,
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
        self._facade.bind_streams(streams)

    def set_leader_election(self, leader: AppShellLeaderElection | None) -> None:
        self._facade.bind_leader_elector(leader)

    def bind_agent_registry_writer(self, writer) -> None:
        self._agent_registry_writer = writer
        self._facade.bind_agent_registry_writer(writer)

    def _require_agent_registry_writer(self):
        if self._agent_registry_writer is None:
            raise RuntimeError("AgentRegistryWriter is not bound")
        return self._agent_registry_writer

    async def start(self) -> None:
        await self._facade.start()
        await self._facade.start_heartbeat_monitor()

    async def stop(self) -> None:
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
        stream = self._facade.connect_hub_stream(hub_id, last_event_id=last_event_id)
        try:
            async for event in stream:
                yield event
        finally:
            result = await self._mongo.update_hub_status_if_current(
                hub_id, connection_id=connection_id, is_online=False
            )
            if result:
                if self._agent_registry_writer is not None:
                    await self._agent_registry_writer.mark_hub_agents_offline(hub_id)

    async def _disconnect_hub(
        self, hub_id: str, queue: asyncio.Queue, connection_id: str
    ) -> None:
        await self._facade.disconnect_hub(hub_id)
        result = await self._mongo.update_hub_status_if_current(
            hub_id, connection_id=connection_id, is_online=False
        )
        if result:
            await self._require_agent_registry_writer().mark_hub_agents_offline(hub_id)

    async def record_hub_heartbeat(self, hub_id: str, api_key: APIKey) -> None:
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
        await self._facade.record_hub_heartbeat(hub_id, api_key.user_id)

    async def is_hub_alive(self, hub_id: str) -> bool:
        return await self._facade.is_hub_online(hub_id)

    def _is_hub_connected_locally(self, hub_id: str) -> bool:
        return self._facade.is_hub_connected_locally(hub_id)

    def is_hub_alive_cached(self, hub_id: str) -> bool:
        return self._facade.is_hub_online_cached(hub_id)

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
        was_online = await self._facade.is_hub_online(hub_id)
        if not was_online and self._agent_registry_writer is not None:
            await self.mark_hub_agents_offline(hub_id)
        result = await self._facade.push_event_to_hub(
            hub_id, event.model_dump(mode="json")
        )
        if result is False:
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
        return bool(result) and was_online

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
        return await self._facade.cancel_hub_task(
            HubCancelCommand(
                hub_id=hub_id,
                agent_message_id=agent_message_id,
                local_agent_id=local_agent_id,
                task_id=task_id,
            )
        )

    async def cancel_hub_task(self, command: HubCancelCommand) -> bool:
        return await self._facade.cancel_hub_task(command)

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
        return await self._facade.reply_to_hub_task(
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

    async def reply_to_hub_task(self, command: HubReplyCommand) -> bool:
        return await self._facade.reply_to_hub_task(command)

    async def send_to_hub(self, command: HubDispatchCommand) -> HubDispatchResult:
        return await self._facade.send_to_hub(command)

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

    async def _do_heartbeat_check(self, stale_threshold: float) -> None:
        await self._facade.run_heartbeat_iteration()

    async def sweep_offline_queues(self) -> None:
        await self._facade.sweep_offline_queues()


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
    config: HubRuntimeBridgeConfig | None = None,
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
        config=config,
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
