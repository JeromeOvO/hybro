"""Compatibility surface for legacy relay imports.

Phase 8 keeps this module import-compatible while HubRuntimeBridge owns the
runtime implementation behind the proxy.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace
from typing import Any

from app_shell.delivery_runtime import SSEManager
from app_shell.redis_runtime import AppShellLeaderElection, AppShellRelayStreamService
from common.dto import (
    HubCancelCommand,
    HubDispatchCommand,
    HubDispatchResult,
    HubReplyCommand,
)
from common.utils.logger import get_logger
from hub_runtime_bridge.adapters.legacy_lifecycle import LegacyHubLifecycleAdapter
from hub_runtime_bridge.adapters.legacy_publish import (
    LegacyHubPublishAuthorizationReader,
    LegacyRelayCancellationReader,
)
from hub_runtime_bridge.config import HubRuntimeBridgeConfig
from hub_runtime_bridge.facade import HubFacade
from models.api_key import APIKey
from models.hub import Hub, HubAgentSync, HubPublishRequest, HubStatus, RelayToHubEvent

logger = get_logger(__name__)


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
            publish_authorization_reader=LegacyHubPublishAuthorizationReader(self._db),
            cancellation_reader=LegacyRelayCancellationReader(self._db),
            offline_failure_port=self._offline_failure_port,
        )
        self._lifecycle = LegacyHubLifecycleAdapter(
            mongo=self._mongo,
            db=self._db,
            facade=self._facade,
            get_agent_registry_writer=lambda: self._agent_registry_writer,
            require_agent_registry_writer=self._require_agent_registry_writer,
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
        return await self._lifecycle.register_hub(hub_id, api_key)

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        return await self._lifecycle.get_hub_owner_id(hub_id)

    async def connect_hub(
        self, hub_id: str, api_key: APIKey, last_event_id: str | None = None
    ) -> AsyncGenerator[dict, None]:
        async for event in self._lifecycle.connect_hub(
            hub_id,
            api_key,
            last_event_id=last_event_id,
        ):
            yield event

    async def _disconnect_hub(
        self, hub_id: str, queue: asyncio.Queue, connection_id: str
    ) -> None:
        del queue
        await self._lifecycle.disconnect_hub(hub_id, connection_id)

    async def record_hub_heartbeat(self, hub_id: str, api_key: APIKey) -> None:
        await self._lifecycle.record_hub_heartbeat(hub_id, api_key)

    async def is_hub_alive(self, hub_id: str) -> bool:
        return await self._facade.is_hub_online(hub_id)

    def _is_hub_connected_locally(self, hub_id: str) -> bool:
        return self._facade.is_hub_connected_locally(hub_id)

    def is_hub_alive_cached(self, hub_id: str) -> bool:
        return self._facade.is_hub_online_cached(hub_id)

    async def mark_hub_agents_offline(
        self, hub_id: str, connection_id: str | None = None
    ) -> None:
        await self._lifecycle.mark_hub_agents_offline(hub_id, connection_id)

    async def sync_agents(
        self,
        hub_id: str,
        agents: list[HubAgentSync],
        api_key: APIKey,
        *,
        prune_missing: bool = True,
    ) -> list[dict]:
        self._lifecycle._mongo = self._mongo
        self._lifecycle._db = self._db
        self._lifecycle._facade = self._facade
        return await self._lifecycle.sync_agents(
            hub_id,
            agents,
            api_key,
            prune_missing=prune_missing,
        )

    async def push_to_hub(self, hub_id: str, event: RelayToHubEvent) -> bool:
        return await self._facade.push_legacy_event_to_hub(
            hub_id,
            event.model_dump(mode="json"),
            mark_agents_offline=self._agent_registry_writer is not None,
        )

    async def process_publish(
        self, hub_id: str, request: HubPublishRequest, api_key: APIKey
    ) -> None:
        await self._lifecycle.process_publish(hub_id, request, api_key)

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
        return await self._lifecycle.get_hub_status(user_id)

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
