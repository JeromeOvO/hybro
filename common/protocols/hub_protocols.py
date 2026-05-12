from collections.abc import AsyncIterator
import inspect
from typing import Any, Protocol, runtime_checkable

from common.dto import HubDispatchCommand, HubDispatchResult, HubInfo


@runtime_checkable
class HubManagement(Protocol):
    async def register_hub(self, hub_id: str, owner_id: str, **kwargs) -> HubInfo: ...
    async def get_hub(self, hub_id: str) -> HubInfo | None: ...
    async def list_hubs(self, owner_id: str) -> list[HubInfo]: ...
    async def connect_hub_stream(self, hub_id: str) -> AsyncIterator[dict]: ...
    async def publish_from_hub(self, hub_id: str, payload: dict) -> None: ...
    async def start_heartbeat_monitor(self) -> None: ...
    async def stop(self) -> None: ...


@runtime_checkable
class HubLivenessReader(Protocol):
    def is_hub_online(self, hub_id: str) -> bool: ...
    async def get_hub_owner_id(self, hub_id: str) -> str | None: ...


def validate_hub_liveness_reader(reader: Any | None) -> None:
    if reader is None:
        return
    method = getattr(reader, "is_hub_online", None)
    if not callable(method):
        raise TypeError("HubLivenessReader.is_hub_online must be callable")
    if inspect.iscoroutinefunction(method):
        raise TypeError(
            "HubLivenessReader.is_hub_online must be synchronous; "
            "async implementations return truthy coroutine objects for sync consumers"
        )


@runtime_checkable
class HubDispatchPort(Protocol):
    async def send_to_hub(self, command: HubDispatchCommand) -> HubDispatchResult: ...
    async def cancel_hub_task(self, hub_id: str, task_id: str) -> bool: ...
    async def reply_to_hub_task(
        self, hub_id: str, task_id: str, reply: dict
    ) -> bool: ...
    def is_hub_online(self, hub_id: str) -> bool: ...


__all__ = ["HubDispatchPort", "HubLivenessReader", "HubManagement"]
