from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from common.eventing import InternalEventPublisher
from common.observability import (
    MetricsCollector,
    NoopMetricsCollector,
    traced_create_task,
)
from common.protocols import (
    AgentCallCounter,
    AgentRegistryWriter,
    HubAgentStatusReader,
    HubRepository,
    HubResponseJournal,
    HubTaskOwnershipStore,
    LeaderElector,
    MessageCancellationReader,
    OfflineHubFailurePort,
    RoomOwnershipReader,
)
from common.protocols.room_protocols import HubPublishAuthorizationReader
from hub_runtime_bridge.config import HubRuntimeBridgeConfig

TaskRunner = Callable[[Awaitable[Any]], Any]


@dataclass(slots=True)
class HubRuntimeBridgeDeps:
    config: HubRuntimeBridgeConfig
    hub_repository: HubRepository | None = None
    hub_response_journal: HubResponseJournal | None = None
    task_ownership_store: HubTaskOwnershipStore | None = None
    worker_id: str = "local-worker"
    agent_registry_writer: AgentRegistryWriter | None = None
    hub_agent_status_reader: HubAgentStatusReader | None = None
    agent_call_counter: AgentCallCounter | None = None
    room_ownership_reader: RoomOwnershipReader | None = None
    publish_authorization_reader: HubPublishAuthorizationReader | None = None
    cancellation_reader: MessageCancellationReader | None = None
    offline_failure_port: OfflineHubFailurePort | None = None
    leader_elector: LeaderElector | None = None
    metrics: MetricsCollector = NoopMetricsCollector()
    internal_event_publisher: InternalEventPublisher | None = None
    streams: Any | None = None
    clock: Callable[[], Any] | None = None
    task_runner: TaskRunner = traced_create_task


__all__ = ["HubRuntimeBridgeDeps", "TaskRunner"]
