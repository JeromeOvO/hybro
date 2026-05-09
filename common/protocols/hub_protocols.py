from typing import Protocol, runtime_checkable

from common.dto import (
    HubAgentResponseInternal,
    HubConnectionInfo,
    HubDispatchCommand,
    HubDispatchResult,
    HubInfo,
    RelayPayload,
)


@runtime_checkable
class HubManagement(Protocol):
    async def register_hub(self, hub: HubInfo) -> HubInfo: ...
    async def connect_hub(self, hub_id: str) -> HubConnectionInfo: ...
    async def process_publish(self, hub_id: str, payload: RelayPayload) -> bool: ...


@runtime_checkable
class HubLivenessReader(Protocol):
    async def get_hub_status(self, hub_id: str) -> HubConnectionInfo | None: ...
    async def is_hub_online(self, hub_id: str) -> bool: ...


@runtime_checkable
class HubDispatchPort(Protocol):
    async def push_to_hub(self, command: HubDispatchCommand) -> HubDispatchResult: ...
    async def cancel_relay_task(self, hub_id: str, task_id: str) -> bool: ...
    async def reply_to_relay_task(
        self, hub_id: str, task_id: str, payload: dict
    ) -> bool: ...


@runtime_checkable
class HubAgentResponseSink(Protocol):
    async def handle_hub_agent_response(
        self, event: HubAgentResponseInternal
    ) -> None: ...


__all__ = [
    "HubAgentResponseSink",
    "HubDispatchPort",
    "HubLivenessReader",
    "HubManagement",
]
