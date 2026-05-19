from collections.abc import AsyncIterator
import inspect
from typing import Any, Protocol, runtime_checkable

from common.dto import (
    HubCancelCommand,
    HubDispatchCommand,
    HubDispatchResult,
    HubInfo,
    HubReplyCommand,
    OfflineHubFailureCommand,
)


@runtime_checkable
class HubManagement(Protocol):
    async def register_hub(self, hub_id: str, owner_id: str, **kwargs) -> HubInfo: ...
    async def get_hub(self, hub_id: str) -> HubInfo | None: ...
    async def list_hubs(self, owner_id: str) -> list[HubInfo]: ...
    def connect_hub_stream(self, hub_id: str, **kwargs) -> AsyncIterator[dict]: ...
    async def publish_from_hub(self, hub_id: str, payload: dict) -> None: ...
    async def sync_agents(
        self, hub_id: str, agents: list[Any], owner_id: str, *, prune_missing: bool = True
    ) -> list[dict]: ...
    async def get_hub_status(self, owner_id: str) -> list[Any]: ...
    async def record_hub_heartbeat(self, hub_id: str, owner_id: str | None = None) -> None: ...
    async def hub_status_for_user(self, owner_id: str) -> list[Any]: ...
    async def start_heartbeat_monitor(self) -> None: ...
    async def stop(self) -> None: ...


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
    async def mark_hub_message_failed(self, command: OfflineHubFailureCommand) -> None: ...


__all__ = [
    "HubDispatchPort",
    "HubDispatchPolicy",
    "HubInternalResponseDispatcher",
    "HubLivenessReader",
    "HubManagement",
    "OfflineHubFailurePort",
]
