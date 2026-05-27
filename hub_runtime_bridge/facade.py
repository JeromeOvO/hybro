from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from common.dto import (
    HubCancelCommand,
    HubDispatchCommand,
    HubDispatchResult,
    HubInfo,
    OfflineHubFailureCommand,
    HubReplyCommand,
)
from common.utils.time import utcnow
from hub_runtime_bridge.config import HubRuntimeBridgeConfig
from hub_runtime_bridge.deps import HubRuntimeBridgeDeps
from hub_runtime_bridge.hub_response_journal import (
    InMemoryHubResponseJournal,
    MongoHubResponseJournal,
)
from hub_runtime_bridge.repository.mongo import HubMongoRepository
from hub_runtime_bridge.service.agent_sync import HubAgentSyncService
from hub_runtime_bridge.service.hub_connection import HubConnectionService
from hub_runtime_bridge.service.hub_liveness import HubLivenessService
from hub_runtime_bridge.service.hub_publish import HubPublishService
from hub_runtime_bridge.service.hub_relay import HubRelayService
from hub_runtime_bridge.service.hub_response_replay_worker import HubResponseReplayWorker
from hub_runtime_bridge.service.ownership_lease_maintainer import OwnershipLeaseMaintainer
from hub_runtime_bridge.task_ownership import (
    InMemoryHubTaskOwnershipStore,
    MongoHubTaskOwnershipStore,
)
from hub_runtime_bridge.transport.offline_queue import OfflineQueue


class HubFacade:
    def __init__(self, deps: HubRuntimeBridgeDeps | None = None, **legacy_deps: Any) -> None:
        if deps is None:
            config = legacy_deps.pop("config", None) or HubRuntimeBridgeConfig()
            mongo = legacy_deps.get("mongo")
            repository = HubMongoRepository(mongo) if mongo is not None else None
            journal = (
                MongoHubResponseJournal(
                    mongo, claim_ttl_seconds=config.journal_claim_ttl_seconds
                )
                if mongo is not None
                else InMemoryHubResponseJournal(
                    claim_ttl_seconds=config.journal_claim_ttl_seconds
                )
            )
            ownership_store = (
                MongoHubTaskOwnershipStore(
                    mongo, lease_ttl_seconds=config.ownership_lease_ttl_seconds
                )
                if mongo is not None
                else InMemoryHubTaskOwnershipStore(
                    lease_ttl_seconds=config.ownership_lease_ttl_seconds
                )
            )
            deps = HubRuntimeBridgeDeps(
                config=config,
                hub_repository=repository,
                hub_response_journal=journal,
                task_ownership_store=ownership_store,
                worker_id=legacy_deps.get("worker_id", "local-worker"),
                agent_registry_writer=legacy_deps.get("agent_registry_writer"),
                hub_agent_status_reader=legacy_deps.get("hub_agent_status_reader"),
                agent_call_counter=legacy_deps.get("agent_call_counter"),
                room_ownership_reader=legacy_deps.get("room_ownership_reader"),
                publish_authorization_reader=legacy_deps.get(
                    "publish_authorization_reader"
                ),
                cancellation_reader=legacy_deps.get("cancellation_reader"),
                event_publisher=legacy_deps.get("event_publisher"),
                streams=legacy_deps.get("streams"),
                offline_failure_port=legacy_deps.get("offline_failure_port"),
            )
        self.deps = deps
        self._queues: dict[str, asyncio.Queue] = {}
        self._offline_queues: dict[str, OfflineQueue] = {}
        self._liveness_cache: dict[str, bool] = {}
        self._dispatcher = None
        self._replay_worker: HubResponseReplayWorker | None = None
        self.ownership_lease_maintainer = (
            OwnershipLeaseMaintainer(
                task_runner=deps.task_runner,
                ownership_store=deps.task_ownership_store,
                worker_id=deps.worker_id,
                interval_seconds=max(1.0, deps.config.ownership_lease_ttl_seconds / 2),
            )
            if deps.task_ownership_store
            else None
        )

        self._liveness = HubLivenessService(
            repository=deps.hub_repository,
            streams=deps.streams,
            local_is_connected=lambda hub_id: hub_id in self._queues,
        )
        self._connection = (
            HubConnectionService(
                repository=deps.hub_repository,
                liveness_reader=self._liveness,
                status_reader=deps.hub_agent_status_reader,
            )
            if deps.hub_repository
            else None
        )
        self._relay = HubRelayService(
            push_event=self._push_event_dict,
            offline_failure_port=deps.offline_failure_port,
            call_counter=deps.agent_call_counter,
        )
        self._sync = (
            HubAgentSyncService(writer=deps.agent_registry_writer, streams=deps.streams)
            if deps.agent_registry_writer
            else None
        )
        self._publish = HubPublishService(
            journal=deps.hub_response_journal,
            event_publisher=deps.event_publisher,
            publish_authorization_reader=deps.publish_authorization_reader,
            cancellation_reader=deps.cancellation_reader,
            worker_id=deps.worker_id,
        )

    async def start(self) -> None:
        if self.deps.hub_response_journal:
            await self.deps.hub_response_journal.ensure_indexes()
        if self.deps.task_ownership_store:
            await self.deps.task_ownership_store.ensure_indexes()
        if self.ownership_lease_maintainer:
            await self.ownership_lease_maintainer.start()
        if self.deps.hub_response_journal and self._dispatcher:
            self._replay_worker = HubResponseReplayWorker(
                journal=self.deps.hub_response_journal,
                dispatcher=self._dispatcher,
                worker_id=self.deps.worker_id,
                batch_size=self.deps.config.replay_batch_size,
                interval_seconds=self.deps.config.replay_interval_seconds,
                task_runner=self.deps.task_runner,
                ownership_store=self.deps.task_ownership_store,
            )
            await self._replay_worker.start()

    async def stop(self) -> None:
        if self._replay_worker:
            await self._replay_worker.stop()
            self._replay_worker = None
        if self.ownership_lease_maintainer:
            await self.ownership_lease_maintainer.stop()
        for queue in list(self._queues.values()):
            await queue.put({"type": "_disconnect"})
        self._queues.clear()
        self._liveness_cache.clear()

    async def start_heartbeat_monitor(self) -> None:
        return None

    def bind_internal_response_dispatcher(self, dispatcher: Any) -> None:
        self._dispatcher = dispatcher
        self._publish.bind_internal_response_dispatcher(dispatcher)

    async def register_hub(self, hub_id: str, owner_id: str, **kwargs) -> HubInfo:
        if not self._connection:
            return HubInfo(hub_id=hub_id, owner_id=owner_id)
        return await self._connection.register_hub(hub_id, owner_id)

    async def get_hub(self, hub_id: str) -> HubInfo | None:
        if not self._connection:
            return None
        return await self._connection.get_hub(hub_id)

    async def list_hubs(self, owner_id: str) -> list[HubInfo]:
        if not self._connection:
            return []
        return await self._connection.list_hubs(owner_id)

    def connect_hub_stream(self, hub_id: str, **kwargs) -> AsyncIterator[dict]:
        async def stream() -> AsyncIterator[dict]:
            queue: asyncio.Queue = asyncio.Queue()
            old_queue = self._queues.get(hub_id)
            if old_queue is not None:
                await old_queue.put({"type": "_disconnect"})
            self._queues[hub_id] = queue
            self._liveness_cache[hub_id] = True
            if self.deps.streams:
                await self.deps.streams.record_heartbeat(hub_id)
            else:
                for queued_event in self._offline_queue(hub_id).pop_fresh():
                    await queue.put(queued_event)

            try:
                yield {"type": "connection_ready"}
                if self.deps.streams:
                    last_id = kwargs.get("last_event_id") or "$"
                    while True:
                        read_task = asyncio.ensure_future(
                            self.deps.streams.read_events(
                                hub_id, last_id=last_id, block_ms=5000
                            )
                        )
                        disconnect_task = asyncio.ensure_future(queue.get())
                        done, pending = await asyncio.wait(
                            {read_task, disconnect_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in pending:
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                        if disconnect_task in done:
                            event = disconnect_task.result()
                            if event.get("type") == "_disconnect":
                                break
                            continue
                        entries = read_task.result()
                        await self.deps.streams.record_heartbeat(hub_id)
                        if not entries:
                            yield {"type": "heartbeat", "timestamp": utcnow().isoformat()}
                            continue
                        for entry_id, payload in entries:
                            payload["_stream_id"] = entry_id
                            last_id = entry_id
                            yield payload
                    return

                while True:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(),
                            timeout=self.deps.config.heartbeat_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        yield {"type": "heartbeat", "timestamp": utcnow().isoformat()}
                        continue
                    if event.get("type") == "_disconnect":
                        break
                    yield event
            finally:
                if self._queues.get(hub_id) is queue:
                    self._queues.pop(hub_id, None)
                    await self._preserve_pending_queue_events(hub_id, queue)
                    self._liveness_cache[hub_id] = False

        return stream()

    def connect_hub(
        self, hub_id: str, api_key: Any, last_event_id: str | None = None
    ) -> AsyncIterator[dict]:
        return self.connect_hub_stream(hub_id, last_event_id=last_event_id)

    async def process_publish(self, hub_id: str, request: Any, api_key: Any) -> None:
        payload = (
            request.model_dump(mode="json")
            if hasattr(request, "model_dump")
            else dict(request)
        )
        await self.publish_from_hub(hub_id, payload)

    async def publish_from_hub(self, hub_id: str, payload: dict) -> None:
        if "owner_id" not in payload and self.deps.hub_repository:
            hub = await self.deps.hub_repository.get_by_id(hub_id)
            if hub:
                payload = {**payload, "owner_id": hub.get("user_id") or hub.get("owner_id")}
        await self._publish.publish_from_hub(hub_id, payload)

    async def sync_agents(
        self, hub_id: str, agents: list[Any], owner_id: str, *, prune_missing: bool = True
    ) -> list[dict]:
        if not self._sync:
            return []
        return await self._sync.sync_agents(
            hub_id, owner_id, agents, prune_missing=prune_missing
        )

    async def get_hub_status(self, owner_id: str) -> list[Any]:
        return await self.hub_status_for_user(owner_id)

    async def hub_status_for_user(self, owner_id: str) -> list[Any]:
        return await self.list_hubs(owner_id)

    async def record_hub_heartbeat(self, hub_id: str, owner_id: str | None = None) -> None:
        if self.deps.streams:
            await self.deps.streams.record_heartbeat(hub_id)
        self._liveness_cache[hub_id] = True
        if self.deps.hub_repository:
            await self.deps.hub_repository.update_hub_status(
                hub_id, is_online=True, last_heartbeat_at=utcnow()
            )

    async def is_hub_online(self, hub_id: str) -> bool:
        online = await self._liveness.is_hub_online(hub_id)
        self._liveness_cache[hub_id] = online
        return online

    async def get_hub_owner_id(self, hub_id: str) -> str | None:
        return await self._liveness.get_hub_owner_id(hub_id)

    async def send_to_hub(self, command: HubDispatchCommand) -> HubDispatchResult:
        return await self._relay.send_to_hub(command)

    async def cancel_hub_task(self, command: HubCancelCommand) -> bool:
        return await self._relay.cancel_hub_task(command)

    async def reply_to_hub_task(self, command: HubReplyCommand) -> bool:
        return await self._relay.reply_to_hub_task(command)

    def is_hub_online_cached(self, hub_id: str) -> bool:
        return bool(self._liveness_cache.get(hub_id, False))

    async def _push_event_dict(self, hub_id: str, event: dict) -> bool:
        if self.deps.streams:
            alive = await self.deps.streams.is_hub_alive(hub_id)
            self._liveness_cache[hub_id] = bool(alive)
            if not alive:
                return False
            if await self.deps.streams.push_event(hub_id, event):
                return True
            return None
        if hub_id not in self._queues:
            self._liveness_cache[hub_id] = False
            dropped = self._offline_queue(hub_id).append(event)
            if dropped is not None:
                await self._mark_dropped_offline_event(dropped)
            return True
        await self._queues[hub_id].put(event)
        return True

    async def _preserve_pending_queue_events(
        self, hub_id: str, queue: asyncio.Queue
    ) -> None:
        while True:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if isinstance(event, dict) and event.get("type") == "_disconnect":
                continue
            dropped = self._offline_queue(hub_id).append(event)
            if dropped is not None:
                await self._mark_dropped_offline_event(dropped)

    def _offline_queue(self, hub_id: str) -> OfflineQueue:
        queue = self._offline_queues.get(hub_id)
        if queue is None:
            queue = OfflineQueue(
                max_size=self.deps.config.offline_queue_max,
                ttl_seconds=self.deps.config.offline_queue_ttl_seconds,
            )
            self._offline_queues[hub_id] = queue
        return queue

    async def _mark_dropped_offline_event(self, event: dict) -> None:
        if self.deps.offline_failure_port is None:
            return
        if event.get("type") != "user_message":
            return
        await self.deps.offline_failure_port.mark_hub_message_failed(
            OfflineHubFailureCommand(
                room_id=event.get("room_id", ""),
                agent_message_id=event.get("agent_message_id", ""),
                agent_id=event.get("agent_id", ""),
                task_id=event.get("task_id") or event.get("agent_message_id", ""),
                error_text="Hub agent offline queue overflowed before delivery",
            )
        )


HubRuntimeBridgeFacade = HubFacade

__all__ = ["HubFacade", "HubRuntimeBridgeFacade"]
