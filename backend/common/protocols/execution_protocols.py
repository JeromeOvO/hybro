from typing import Protocol, runtime_checkable

from common.dto import (
    CancellationAck,
    ExecutionAck,
    ExecutionRequest,
    HITLRequest,
    HITLResponse,
    RunInfo,
)
from common.protocols.json_types import JsonValue


@runtime_checkable
class ExecutionEngine(Protocol):
    async def execute(self, request: ExecutionRequest) -> ExecutionAck: ...
    async def start_orchestration(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None: ...
    def schedule_orchestration(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None: ...
    async def cancel(
        self,
        room_id: str,
        message_id: str,
        *,
        requested_by_user_id: str,
    ) -> bool | CancellationAck: ...
    async def get_run(self, run_id: str) -> RunInfo | None: ...
    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]: ...
    async def get_latest_runs_for_rooms(
        self, room_ids: list[str]
    ) -> dict[str, RunInfo]: ...
    async def cancel_inflight_tasks(self) -> int: ...
    async def heal_diverged_runs(self, limit: int = 500) -> int: ...


@runtime_checkable
class HITLManager(Protocol):
    async def resolve_hitl_batch(
        self,
        room_id: str,
        interaction_id: str,
        answers: list[dict[str, str]],
        responder_id: str,
        client_request_id: str | None = None,
    ) -> HITLResponse: ...

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]: ...
    async def cancel_hitl_interaction(
        self,
        room_id: str,
        interaction_id: str,
        expected_version: int,
    ) -> int: ...


@runtime_checkable
class RoomDistributedLock(Protocol):
    async def acquire(self, room_id: str, owner: str, ttl: int) -> bool | None: ...
    async def renew(self, room_id: str, owner: str, ttl: int) -> bool | None: ...
    async def release(self, room_id: str, owner: str) -> None: ...


@runtime_checkable
class WebhookReceiver(Protocol):
    async def authenticate_webhook(self, message_id: str, token: str) -> None: ...

    async def handle_webhook(
        self, message_id: str, payload: dict[str, JsonValue], token: str
    ) -> dict[str, JsonValue]: ...


__all__ = [
    "ExecutionEngine",
    "HITLManager",
    "RoomDistributedLock",
    "WebhookReceiver",
]
