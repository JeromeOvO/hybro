"""Gateway-owned FastAPI dependency context."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from fastapi import Depends, Request

from agent.protocols import (
    AgentCapabilityIssueStore,
    AgentCenterCompatibility,
    AgentGroupStoreCompatibility,
    AgentInspection,
    AgentLivenessChecker,
    AgentSuggestionService,
)
from common.protocols import (
    A2ATaskStatusReader,
    AgentAvatarManager,
    AgentRegistry,
    AgentVectorIndexWriter,
    APIKeyRateLimiter,
    APIKeyStore,
    EmbeddingServiceProtocol,
    ExecutionEngine,
    FileStorage,
    GatewayDiscoveryProvider,
    GatewayService,
    HITLManager,
    HubRelayManagement,
    HubStatusReader,
    RoomOwnershipReader,
    RoomRouteReader,
    SSERouteTransport,
    SSEStateReader,
    ViewSetRepositoryProvider,
    WebhookReceiver,
)
from context_memory.protocols import LegacyChatContextAPI
from room.protocols import RoomCenterCompatibility


@dataclass(frozen=True, slots=True)
class APIGatewayDeps:
    task_store: A2ATaskStatusReader
    agent_center: AgentCenterCompatibility
    agent_service: AgentRegistry
    capability_issue_service: AgentCapabilityIssueStore
    agent_avatar_manager: AgentAvatarManager
    agent_liveness_checker: AgentLivenessChecker
    agent_group_store: AgentGroupStoreCompatibility
    api_key_store: APIKeyStore | None
    discovery_service: GatewayDiscoveryProvider | None
    discovery_rate_limiter: APIKeyRateLimiter | None
    discovery_default_limit: int
    file_storage: FileStorage
    room_ownership_reader: RoomOwnershipReader
    hitl_manager: HITLManager
    hub_relay_service: HubStatusReader
    inspection_center: AgentInspection
    memory_center: LegacyChatContextAPI
    gateway_service: GatewayService | None
    gateway_rate_limiter: APIKeyRateLimiter | None
    relay_service: HubRelayManagement
    room_center: RoomCenterCompatibility
    room_store: RoomRouteReader
    agent_selection_service: AgentSuggestionService
    execution_engine: ExecutionEngine
    sse_store: SSEStateReader
    sse_transport: SSERouteTransport
    webhook_receiver: WebhookReceiver
    repository_provider: ViewSetRepositoryProvider
    embedding_provider: EmbeddingServiceProtocol
    vector_index: AgentVectorIndexWriter


def missing_required_deps(deps: APIGatewayDeps | None) -> list[str]:
    if deps is None:
        return ["app.state.api_gateway_deps"]

    optional_fields = {
        "api_key_store",
        "discovery_service",
        "discovery_rate_limiter",
        "gateway_service",
        "gateway_rate_limiter",
    }

    return [
        field.name
        for field in fields(APIGatewayDeps)
        if getattr(deps, field.name) is None and field.name not in optional_fields
    ]


def bind_api_gateway_deps(app: Any, deps: APIGatewayDeps) -> None:
    missing = missing_required_deps(deps)
    if missing:
        raise RuntimeError("APIGatewayDeps incomplete - missing: " + ", ".join(missing))

    app.state.api_gateway_deps = deps


def get_api_gateway_deps(request: Request) -> APIGatewayDeps:
    deps = getattr(request.app.state, "api_gateway_deps", None)
    if deps is None:
        raise RuntimeError("APIGatewayDeps not bound - startup incomplete")
    return deps


_API_GATEWAY_DEPS_DEPENDENCY = Depends(get_api_gateway_deps)


def is_bound(app: Any) -> bool:
    deps = getattr(app.state, "api_gateway_deps", None)
    return deps is not None and not missing_required_deps(deps)


def get_task_store(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> A2ATaskStatusReader:
    return deps.task_store


def get_agent_center(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentCenterCompatibility:
    return deps.agent_center


def get_agent_service(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentRegistry:
    return deps.agent_service


def get_capability_issue_service(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentCapabilityIssueStore:
    return deps.capability_issue_service


def get_agent_avatar_manager(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentAvatarManager:
    return deps.agent_avatar_manager


def get_agent_liveness_checker(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentLivenessChecker:
    return deps.agent_liveness_checker


def get_agent_group_store(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentGroupStoreCompatibility:
    return deps.agent_group_store


def get_api_key_store(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> APIKeyStore:
    return deps.api_key_store


def get_discovery_service(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> GatewayDiscoveryProvider:
    return deps.discovery_service


def get_discovery_rate_limiter(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> APIKeyRateLimiter:
    return deps.discovery_rate_limiter


def get_discovery_default_limit(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> int:
    return deps.discovery_default_limit


def get_file_storage(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> FileStorage:
    return deps.file_storage


def get_room_ownership_reader(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> RoomOwnershipReader:
    return deps.room_ownership_reader


def get_hitl_manager(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> HITLManager:
    return deps.hitl_manager


def get_hub_relay_service(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> HubStatusReader:
    return deps.hub_relay_service


def get_inspection_center(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentInspection:
    return deps.inspection_center


def get_memory_center(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> LegacyChatContextAPI:
    return deps.memory_center


def get_gateway_service(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> GatewayService:
    return deps.gateway_service


def get_gateway_rate_limiter(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> APIKeyRateLimiter:
    return deps.gateway_rate_limiter


def get_relay_service(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> HubRelayManagement:
    return deps.relay_service


def get_room_center(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> RoomCenterCompatibility:
    return deps.room_center


def get_room_store(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> RoomRouteReader:
    return deps.room_store


def get_agent_selection_service(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentSuggestionService:
    return deps.agent_selection_service


def get_execution_engine(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> ExecutionEngine:
    return deps.execution_engine


def get_sse_store(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> SSEStateReader:
    return deps.sse_store


def get_sse_transport(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> SSERouteTransport:
    return deps.sse_transport


def get_webhook_receiver(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> WebhookReceiver:
    return deps.webhook_receiver


def get_viewset_repository_provider(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> ViewSetRepositoryProvider:
    return deps.repository_provider


def get_agent_viewset_embedding_provider(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> EmbeddingServiceProtocol:
    return deps.embedding_provider


def get_agent_viewset_vector_index(
    deps: APIGatewayDeps = _API_GATEWAY_DEPS_DEPENDENCY,
) -> AgentVectorIndexWriter:
    return deps.vector_index
