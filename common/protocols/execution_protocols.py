from typing import Literal, Protocol, runtime_checkable

from common.dto import (
    ExecutionAck,
    ExecutionRequest,
    HITLRequest,
    HITLResponse,
    HubAgentResponseInternal,
    RunInfo,
)


@runtime_checkable
class ExecutionEngine(Protocol):
    async def execute(self, request: ExecutionRequest) -> ExecutionAck: ...
    async def cancel(self, room_id: str, message_id: str) -> bool: ...
    async def get_run(self, run_id: str) -> RunInfo | None: ...
    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]: ...
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
        agent_id: str | None = None,
        a2a_task_id: str | None = None,
        a2a_context_id: str | None = None,
        continuation_message_id: str | None = None,
    ) -> HITLRequest | None: ...

    async def resolve_hitl(
        self, request_id: str, response: str, responder_id: str
    ) -> HITLResponse: ...

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]: ...
    async def cancel_hitl(self, request_id: str) -> bool: ...


@runtime_checkable
class HubAgentResponseSink(Protocol):
    async def handle_hub_agent_response(
        self, event: HubAgentResponseInternal
    ) -> None: ...


__all__ = ["ExecutionEngine", "HITLManager", "HubAgentResponseSink"]
