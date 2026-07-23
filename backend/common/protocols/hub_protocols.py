import inspect
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, TypeAlias, runtime_checkable

from common.dto import (
    HubCancelCommand,
    HubDispatchCommand,
    HubDispatchResult,
    HubInfo,
    HubReplyCommand,
    OfflineHubFailureCommand,
)
from common.protocols.platform_protocols import APIKeyPrincipal

HubJsonScalar: TypeAlias = str | int | float | bool | None
HubJsonValue: TypeAlias = (
    HubJsonScalar | list["HubJsonValue"] | dict[str, "HubJsonValue"]
)


@runtime_checkable
class HubPublishEventPayload(Protocol):
    type: str
    agent_message_id: str
    data: Mapping[str, HubJsonValue]

    def model_dump(self, *, mode: str = "python") -> dict[str, HubJsonValue]: ...


@runtime_checkable
class HubPublishRouteRequest(Protocol):
    room_id: str
    events: Sequence[HubPublishEventPayload]

    def model_dump(self, *, mode: str = "python") -> dict[str, HubJsonValue]: ...


@runtime_checkable
class HubAgentSyncPayload(Protocol):
    local_agent_id: str
    name: str
    description: str
    capabilities: Sequence[str]
    agent_card: Mapping[str, HubJsonValue]


@runtime_checkable
class HubStatusPayload(Protocol):
    hub_id: str
    is_online: bool
    last_connected_at: datetime | None
    agent_count: int
    active_agent_count: int
    inactive_agent_count: int


@runtime_checkable
class HubRegistrationResult(Protocol):
    hub_id: str
    user_id: str


@runtime_checkable
class HubManagement(Protocol):
    async def register_hub(self, hub_id: str, owner_id: str, **kwargs) -> HubInfo: ...
    async def get_hub(self, hub_id: str) -> HubInfo | None: ...
    async def list_hubs(self, owner_id: str) -> list[HubInfo]: ...
    def connect_hub(
        self, hub_id: str, api_key: Any, last_event_id: str | None = None
    ) -> AsyncIterator[dict]: ...
    def connect_hub_stream(self, hub_id: str, **kwargs) -> AsyncIterator[dict]: ...
    async def process_publish(
        self, hub_id: str, request: Any, api_key: Any
    ) -> None: ...
    async def publish_from_hub(self, hub_id: str, payload: dict) -> None: ...
    async def sync_agents(
        self,
        hub_id: str,
        agents: list[Any],
        owner_id: str,
        *,
        prune_missing: bool = True,
    ) -> list[dict]: ...
    async def get_hub_status(self, owner_id: str) -> list[Any]: ...
    async def record_hub_heartbeat(
        self, hub_id: str, owner_id: str | None = None
    ) -> None: ...
    async def hub_status_for_user(self, owner_id: str) -> list[Any]: ...
    async def start_heartbeat_monitor(self) -> None: ...
    async def stop(self) -> None: ...


@runtime_checkable
class HubRelayManagement(Protocol):
    async def register_hub(
        self, hub_id: str, api_key: APIKeyPrincipal
    ) -> HubRegistrationResult: ...
    def connect_hub(
        self, hub_id: str, api_key: APIKeyPrincipal, last_event_id: str | None = None
    ) -> AsyncIterator[dict[str, HubJsonValue]]: ...
    async def process_publish(
        self,
        hub_id: str,
        request: HubPublishRouteRequest,
        api_key: APIKeyPrincipal,
    ) -> None: ...
    async def sync_agents(
        self,
        hub_id: str,
        agents: Sequence[HubAgentSyncPayload],
        api_key: APIKeyPrincipal,
        *,
        prune_missing: bool = True,
    ) -> Sequence[Mapping[str, HubJsonValue]]: ...
    async def get_hub_status(self, owner_id: str) -> Sequence[HubStatusPayload]: ...
    async def record_hub_heartbeat(
        self, hub_id: str, api_key: APIKeyPrincipal
    ) -> None: ...


@runtime_checkable
class HubStatusReader(Protocol):
    async def get_hub_status(self, owner_id: str) -> Sequence[HubStatusPayload]: ...


@runtime_checkable
class HubLivenessReader(Protocol):
    async def is_hub_online(self, hub_id: str) -> bool: ...
    async def get_hub_owner_id(self, hub_id: str) -> str | None: ...


def validate_hub_liveness_reader(reader: Any | None) -> None:
    if reader is None:
        return
    method = getattr(reader, "is_hub_online", None)
    if not callable(method):
        raise TypeError("HubLivenessReader.is_hub_online must be callable")
    if not inspect.iscoroutinefunction(method):
        raise TypeError("HubLivenessReader.is_hub_online must be async")


@runtime_checkable
class HubDispatchPort(Protocol):
    async def send_to_hub(self, command: HubDispatchCommand) -> HubDispatchResult: ...
    async def cancel_hub_task(self, command: HubCancelCommand) -> bool: ...
    async def reply_to_hub_task(self, command: HubReplyCommand) -> bool: ...
    def is_hub_online(self, hub_id: str) -> bool: ...


@runtime_checkable
class HubDispatchPolicy(Protocol):
    async def can_dispatch_to_hub(self, hub_id: str, agent_id: str) -> bool: ...


@runtime_checkable
class HubInternalResponseDispatcher(Protocol):
    async def dispatch_hub_internal_response(self, event: Any) -> None: ...


@runtime_checkable
class OfflineHubFailurePort(Protocol):
    async def mark_hub_message_failed(
        self, command: OfflineHubFailureCommand
    ) -> None: ...


__all__ = [
    "HubDispatchPort",
    "HubDispatchPolicy",
    "HubInternalResponseDispatcher",
    "HubLivenessReader",
    "HubManagement",
    "HubRegistrationResult",
    "HubRelayManagement",
    "HubStatusReader",
    "OfflineHubFailurePort",
]
