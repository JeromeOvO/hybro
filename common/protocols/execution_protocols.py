from typing import Protocol, runtime_checkable

from common.dto import (
    ExecutionAck,
    ExecutionRequest,
    ExecutionResult,
    HITLRequest,
    HITLResponse,
    RunInfo,
    WorkflowState,
)


@runtime_checkable
class ExecutionEngine(Protocol):
    async def execute(self, request: ExecutionRequest) -> ExecutionAck: ...
    async def get_result(self, run_id: str) -> ExecutionResult | None: ...


@runtime_checkable
class HITLManager(Protocol):
    async def request_input(self, request: HITLRequest) -> HITLRequest: ...
    async def handle_response(self, response: HITLResponse) -> bool: ...
    async def get_pending_requests(
        self, room_id: str | None = None
    ) -> list[HITLRequest]: ...
    async def cancel_request(self, request_id: str) -> bool: ...
    async def cancel_requests_for_message(self, message_id: str) -> int: ...


@runtime_checkable
class WorkflowController(Protocol):
    async def record_processing_status(
        self, run_id: str, status: str, metadata: dict | None = None
    ) -> WorkflowState: ...
    async def heal_head_from_events(self, run_id: str) -> RunInfo | None: ...
    async def append_run_timeout_failure(self, run_id: str, reason: str) -> RunInfo: ...


__all__ = [
    "ExecutionEngine",
    "HITLManager",
    "WorkflowController",
]
