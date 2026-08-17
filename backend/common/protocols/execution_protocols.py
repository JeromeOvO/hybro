from typing import Literal, Protocol, runtime_checkable

from common.dto import (
    CancellationAck,
    ExecutionAck,
    ExecutionRequest,
    HITLRequest,
    HITLResponse,
    HubAgentResponseInternal,
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
    async def create_hitl_request(
        self,
        room_id: str,
        user_message_id: str,
        prompt: str,
        source: Literal["agent", "supervisor"],
        source_step_id: str | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        a2a_task_id: str | None = None,
        a2a_context_id: str | None = None,
        continuation_message_id: str | None = None,
        display_message_id: str | None = None,
        prompt_type: Literal[
            "text",
            "textarea",
            "choice",
            "single_choice",
            "multi_choice",
            "confirmation",
            "approval",
            "authentication",
            "date",
            "file",
        ] = "text",
        choices: list[str] | None = None,
        group_id: str | None = None,
        group_total: int | None = None,
        group_index: int | None = None,
    ) -> HITLRequest | None: ...

    async def resolve_hitl(
        self,
        room_id: str,
        request_id: str,
        response: str,
        responder_id: str,
    ) -> HITLResponse: ...

    async def resolve_hitl_batch(
        self,
        room_id: str,
        interaction_id: str,
        answers: list[dict[str, str]],
        responder_id: str,
        client_request_id: str | None = None,
    ) -> HITLResponse: ...

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]: ...
    async def cancel_hitl(self, room_id: str, request_id: str) -> bool: ...


@runtime_checkable
class HubAgentResponseSink(Protocol):
    async def handle_hub_agent_response(
        self, event: HubAgentResponseInternal
    ) -> None: ...


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
    "HubAgentResponseSink",
    "RoomDistributedLock",
    "WebhookReceiver",
]
