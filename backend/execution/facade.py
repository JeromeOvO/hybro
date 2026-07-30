from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Protocol

from common.a2a_constants import SSEProcessingStatus
from common.dto import (
    ExecutionAck,
    ExecutionRequest,
    HITLRequest,
    HITLResponse,
    HubAgentResponseInternal,
    RunInfo,
)
from common.observability import bind_log_context, traced_create_task
from common.protocols import EventPublisher
from common.utils.logger import get_logger
from common.utils.time import utcnow
from execution.dispatch.agent_event import AgentEvent
from execution.events import emit_processing_status, emit_room_processing_status
from execution.hitl.translators import (
    hitl_cancel_none_to_success,
    hitl_response_dict_to_common,
    model_hitl_request_to_common,
)
from execution.orchestration.run_reducer import record_hitl_resolution
from execution.orchestration.run_store import (
    OrchestrationRunStore,
    OrchestrationStoreConflict,
)
from execution.ports import (
    AgentResponseHandlerPort,
    AgentTaskCleanupPort,
    CancellationStatePort,
    CancellationStorePort,
    ClientRequestIdResolver,
    HITLMessageCancellationPort,
    RunEventEnabled,
    RunLifecyclePort,
    RunReadPort,
    TaskFactory,
)
from execution.translators import room_response_to_execution_ack
from models.orchestration import (
    TERMINAL_ORCHESTRATION_STATUSES,
    OrchestrationEventType,
    OrchestrationRunEvent,
    OrchestrationRunState,
    OrchestrationStatus,
)
from models.request import OrchestrationRequest, RoomCenterUserMessageRequest

if TYPE_CHECKING:
    from models.response import OrchestrationResponse

logger = get_logger(__name__)

AGENT_EVENT_KINDS = {
    "artifact_update",
    "response",
    "error",
    "canceled",
    "task_submitted",
    "status_update",
    "interactive",
    "processing_status",
}
TERMINAL_AGENT_EVENT_KINDS = {"response", "error", "canceled"}
LEGACY_COMMON_AGENT_EVENT_KIND_MAP = {
    "final": "response",
    "input_required": "interactive",
    "status_update": "status_update",
    "error": "error",
}
UNSUPPORTED_PHASE7B_HUB_EVENT_TYPES = {"partial"}
LEGACY_TASK_STATE_VALUE_MAP = {
    "input_required": "input-required",
    "auth_required": "auth-required",
}
LEGACY_PROCESSING_STATUS_VALUE_MAP = {
    "input_required": "awaiting_input",
    "input-required": "awaiting_input",
    "auth_required": "awaiting_input",
    "auth-required": "awaiting_input",
}
VALID_ERROR_TASK_STATES = {"failed", "canceled", "rejected"}
VALID_INTERACTIVE_TASK_STATES = {"input-required", "auth-required"}
VALID_PROCESSING_STATUS_STATES = {
    "queued",
    "processing",
    "awaiting_input",
    "completed",
    "failed",
    "canceled",
    "rejected",
    "rate_limited",
    "error",
}


class RoomCenterPort(Protocol):
    async def send_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: Any = None,
        mentioned_agent_ids: Any = None,
    ) -> Any: ...

    async def persist_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: Any = None,
        mentioned_agent_ids: Any = None,
    ) -> tuple[Any, Any | None]: ...

    async def run_message_preflight_to_room(self, context: Any) -> Any: ...

    async def update_user_message_orchestration_status(
        self,
        message_id: str,
        status: str,
    ) -> bool: ...


class RoomMessageCenterPort(Protocol):
    async def process_room_user_message(
        self,
        request: OrchestrationRequest,
    ) -> OrchestrationResponse: ...


class HITLServicePort(Protocol):
    async def request_input(
        self,
        room_id: str,
        user_message_id: str,
        source: str,
        prompt: str,
        **kwargs: Any,
    ) -> Any | None: ...

    async def handle_response(
        self,
        room_id: str,
        request_id: str,
        user_input: str,
        user_id: str,
    ) -> dict[str, Any]: ...

    async def get_pending_requests(self, room_id: str) -> list[Any]: ...

    async def cancel_request(
        self,
        request_id: str,
        room_id: str | None = None,
    ) -> Any: ...


def _thaw_hub_payload_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_hub_payload_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw_hub_payload_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return [_thaw_hub_payload_value(item) for item in value]
    return deepcopy(value)


def _hub_payload_kind(payload: dict[str, Any]) -> str:
    raw = payload.get("kind") or payload.get("event_type")
    if raw is None or raw == "":
        raise ValueError(
            "HubAgentResponseInternal payload missing required field: kind"
        )
    raw_kind = str(raw)
    if raw_kind in UNSUPPORTED_PHASE7B_HUB_EVENT_TYPES:
        raise ValueError(
            f"Unsupported non-terminal Hub AgentEvent event_type: {raw_kind}"
        )
    kind = LEGACY_COMMON_AGENT_EVENT_KIND_MAP.get(raw_kind, raw_kind)
    if kind not in AGENT_EVENT_KINDS:
        raise ValueError(f"Unsupported AgentEvent kind from Hub payload: {kind}")
    return kind


def _hub_payload_message_id(payload: dict[str, Any]) -> str:
    value = payload.get("message_id")
    if value is None:
        value = payload.get("continuation_message_id")
    if not isinstance(value, str) or not value:
        raise ValueError(
            "HubAgentResponseInternal payload requires non-empty string message_id "
            "or continuation_message_id"
        )
    return value


def _optional_hub_str(
    payload: dict[str, Any],
    key: str,
    *,
    default: str | None = None,
) -> str | None:
    value = payload.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Hub AgentEvent field {key} must be a string")
    return value


def _optional_hub_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Hub AgentEvent field {key} must be a boolean")
    return value


def _optional_hub_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Hub AgentEvent field {key} must be an integer")
    return value


def _optional_hub_list_of_dicts(
    payload: dict[str, Any],
    key: str,
) -> list[dict[str, Any]] | None:
    value = _thaw_hub_payload_value(payload.get(key))
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"Hub AgentEvent field {key} must be a list of objects")
    return value


def _agent_event_details(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _normalize_hub_state(kind: str, value: str | None) -> str | None:
    if value is None:
        return None
    if kind == "processing_status":
        normalized = LEGACY_PROCESSING_STATUS_VALUE_MAP.get(value, value)
        allowed = VALID_PROCESSING_STATUS_STATES
    elif kind == "error":
        normalized = LEGACY_TASK_STATE_VALUE_MAP.get(value, value)
        allowed = VALID_ERROR_TASK_STATES
    elif kind == "interactive":
        normalized = LEGACY_TASK_STATE_VALUE_MAP.get(value, value)
        allowed = VALID_INTERACTIVE_TASK_STATES
    else:
        normalized = LEGACY_TASK_STATE_VALUE_MAP.get(value, value)
        return normalized
    if normalized not in allowed:
        raise ValueError(f"Unsupported Hub AgentEvent state for {kind}: {value}")
    return normalized


def _hub_payload_state(kind: str, payload: dict[str, Any]) -> str | None:
    return _normalize_hub_state(kind, _optional_hub_str(payload, "state"))


def _validate_hub_payload_for_kind(kind: str, payload: dict[str, Any]) -> None:
    state = _hub_payload_state(kind, payload)
    text = _optional_hub_str(payload, "text", default="")
    error_text = _optional_hub_str(payload, "error_text")
    if kind == "processing_status" and not state:
        raise ValueError("processing_status Hub payload requires state")
    if kind == "error" and not (error_text or text):
        raise ValueError("error Hub payload requires error_text or text")
    if payload.get("is_final") is not None and not isinstance(
        payload.get("is_final"), bool
    ):
        raise ValueError("Hub AgentEvent field is_final must be a boolean")
    verified = payload.get("lifecycle_message_id_verified")
    if verified is not None and not isinstance(verified, bool):
        raise ValueError(
            "Hub AgentEvent field lifecycle_message_id_verified must be a boolean"
        )


def _validate_hub_event_consistency(
    event: HubAgentResponseInternal,
    kind: str,
    payload: dict[str, Any],
) -> None:
    payload_task_id = payload.get("task_id")
    if payload_task_id is not None and payload_task_id != event.task_id:
        raise ValueError("Hub payload task_id conflicts with event.task_id")
    if not event.task_id:
        raise ValueError("HubAgentResponseInternal requires top-level task_id")
    if kind in TERMINAL_AGENT_EVENT_KINDS and not event.is_terminal:
        raise ValueError(f"Hub AgentEvent kind {kind} requires terminal internal event")
    if kind not in TERMINAL_AGENT_EVENT_KINDS and event.is_terminal:
        raise ValueError(
            f"Hub AgentEvent kind {kind} must not use a terminal internal event"
        )
    if kind in TERMINAL_AGENT_EVENT_KINDS and payload.get("is_final") is False:
        raise ValueError(f"Hub AgentEvent kind {kind} cannot set is_final=False")


def _hub_payload_lifecycle_message_id(kind: str, payload: dict[str, Any]) -> str | None:
    value = _optional_hub_str(payload, "lifecycle_message_id")
    if kind == "processing_status" and value is None:
        raise ValueError("Hub processing_status requires verified lifecycle_message_id")
    if (
        value is not None
        and kind == "processing_status"
        and payload.get("lifecycle_message_id_verified") is not True
    ):
        raise ValueError(
            "Hub processing_status lifecycle_message_id requires upstream "
            "turn/root validation"
        )
    return value


def hub_agent_response_internal_to_agent_event(
    event: HubAgentResponseInternal,
) -> AgentEvent:
    payload = _thaw_hub_payload_value(event.payload)
    kind = _hub_payload_kind(payload)
    _validate_hub_event_consistency(event, kind, payload)
    _validate_hub_payload_for_kind(kind, payload)
    return AgentEvent(
        kind=kind,
        room_id=event.room_id,
        message_id=_hub_payload_message_id(payload),
        agent_id=event.agent_id,
        task_id=event.task_id,
        turn_id=_optional_hub_str(payload, "turn_id"),
        text=_optional_hub_str(payload, "text", default="") or "",
        state=_hub_payload_state(kind, payload),
        parts=_optional_hub_list_of_dicts(payload, "parts"),
        artifacts=_optional_hub_list_of_dicts(payload, "artifacts"),
        context_id=_optional_hub_str(payload, "context_id"),
        error_text=_optional_hub_str(payload, "error_text"),
        related_message_id=_optional_hub_str(payload, "related_message_id"),
        user_id=_optional_hub_str(payload, "user_id"),
        client_request_id=_optional_hub_str(payload, "client_request_id"),
        lifecycle_message_id=_hub_payload_lifecycle_message_id(kind, payload),
        append=_optional_hub_bool(payload, "append", default=False),
        last_chunk=_optional_hub_bool(payload, "last_chunk", default=False),
        artifact_update_id=event.idempotency_key,
        retry_on_finalization_conflict=bool(event.journal_id),
        finalization_recovery_id=event.journal_id,
        is_final=_optional_hub_bool(payload, "is_final", default=event.is_terminal),
        agent_name=_optional_hub_str(payload, "agent_name"),
        step_number=_optional_hub_int(payload, "step_number"),
        total_steps=_optional_hub_int(payload, "total_steps"),
        skip_persist=_optional_hub_bool(payload, "skip_persist", default=False),
        files_materialized=_optional_hub_bool(
            payload, "files_materialized", default=False
        ),
        details=_agent_event_details(_thaw_hub_payload_value(payload.get("details"))),
    )


class ExecutionFacade:
    def __init__(
        self,
        *,
        room_center: RoomCenterPort,
        room_message_center: RoomMessageCenterPort,
        hitl_manager: HITLServicePort,
        run_lifecycle: RunLifecyclePort,
        run_reader: RunReadPort,
        cancellation_state: CancellationStatePort,
        cancellation_store: CancellationStorePort,
        hitl_message_cancellation: HITLMessageCancellationPort,
        agent_task_cleanup: AgentTaskCleanupPort,
        agent_response_handler: AgentResponseHandlerPort,
        event_publisher: EventPublisher,
        run_event_enabled: RunEventEnabled,
        client_request_id_resolver: ClientRequestIdResolver,
        orchestration_run_store: OrchestrationRunStore | None = None,
        task_factory: TaskFactory = traced_create_task,
    ) -> None:
        self._room_center = room_center
        self._room_message_center = room_message_center
        self._hitl_manager = hitl_manager
        self._run_lifecycle = run_lifecycle
        self._run_reader = run_reader
        self._cancellation_state = cancellation_state
        self._cancellation_store = cancellation_store
        self._hitl_message_cancellation = hitl_message_cancellation
        self._agent_task_cleanup = agent_task_cleanup
        self._agent_response_handler = agent_response_handler
        self._event_publisher = event_publisher
        self._run_event_enabled = run_event_enabled
        self._client_request_id_resolver = client_request_id_resolver
        self._orchestration_run_store = orchestration_run_store
        self._task_factory = task_factory
        self._inflight: set[asyncio.Task] = set()
        self._inflight_metadata: dict[asyncio.Task, dict[str, str | None]] = {}

    async def _reject_if_hitl_pending(
        self,
        request: ExecutionRequest,
    ) -> ExecutionAck | None:
        try:
            pending_requests = await self._hitl_manager.get_pending_requests(
                request.room_id
            )
        except Exception:
            logger.warning("pending HITL lookup failed before execute", exc_info=True)
            return None
        if not pending_requests:
            return None
        return ExecutionAck(
            room_id=request.room_id,
            success=False,
            error="Room is waiting for your input before it can process another message.",
            status_code=409,
            should_start_orchestration=False,
        )

    async def _reject_if_room_has_active_run(
        self,
        request: ExecutionRequest,
    ) -> ExecutionAck | None:
        try:
            active_runs = await self._run_reader.get_runs_for_room(request.room_id)
        except Exception:
            logger.warning(
                "active room run lookup failed before execute", exc_info=True
            )
            return None
        if not active_runs:
            return None
        return ExecutionAck(
            room_id=request.room_id,
            success=False,
            error="Room is already processing another message.",
            status_code=409,
            should_start_orchestration=False,
        )

    async def _emit_room_preflight_processing_status(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None:
        if not ack.message_id:
            return
        lifecycle_message_id = ack.dispatch_root_message_id or ack.message_id
        await emit_room_processing_status(
            room_id=ack.room_id or request.room_id,
            status=SSEProcessingStatus.PROCESSING,
            message_id=ack.message_id,
            lifecycle_message_id=lifecycle_message_id,
            run_lifecycle=self._run_lifecycle,
            event_publisher=self._event_publisher,
            run_event_enabled=self._run_event_enabled,
            client_request_id_resolver=self._client_request_id_resolver,
            client_request_id=request.client_request_id,
        )

    def _terminal_preflight_status(
        self,
        ack: ExecutionAck,
    ) -> SSEProcessingStatus | None:
        if ack.should_start_orchestration:
            return None
        status_by_outcome = {
            "completed": SSEProcessingStatus.COMPLETED,
            "canceled": SSEProcessingStatus.CANCELED,
            "failed": SSEProcessingStatus.FAILED,
        }
        if ack.preflight_outcome in status_by_outcome:
            return status_by_outcome[ack.preflight_outcome]
        if ack.message_id and not ack.success:
            return SSEProcessingStatus.FAILED
        return None

    async def _emit_room_preflight_terminal_status(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None:
        status = self._terminal_preflight_status(ack)
        if status is None or not ack.message_id:
            return
        lifecycle_message_id = ack.dispatch_root_message_id or ack.message_id
        await emit_room_processing_status(
            room_id=ack.room_id or request.room_id,
            status=status,
            message_id=ack.message_id,
            lifecycle_message_id=lifecycle_message_id,
            run_lifecycle=self._run_lifecycle,
            event_publisher=self._event_publisher,
            run_event_enabled=self._run_event_enabled,
            client_request_id_resolver=self._client_request_id_resolver,
            client_request_id=request.client_request_id,
            details=ack.preflight_details or ack.error,
        )

    async def _emit_room_preflight_statuses(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None:
        try:
            await self._emit_room_preflight_processing_status(request, ack)
            await self._emit_room_preflight_terminal_status(request, ack)
        except Exception:
            logger.warning(
                "room preflight status emission failed after persistence",
                exc_info=True,
            )

    @staticmethod
    def _room_request_extend_info(request: ExecutionRequest) -> dict[str, Any] | None:
        extend_info: dict[str, Any] = {}
        if (
            request.mode != "direct"
            or request.selected_agent_ids is not None
            or request.candidate_scope_mode is not None
            or request.candidate_scope_group_id is not None
            or request.orchestration_schema_version is not None
        ):
            extend_info["mode"] = request.mode
        if request.selected_agent_ids is not None:
            extend_info["selected_agent_ids"] = list(request.selected_agent_ids)
        if request.candidate_scope_mode is not None:
            extend_info["candidate_scope_mode"] = request.candidate_scope_mode
        if request.candidate_scope_group_id is not None:
            extend_info["candidate_scope_group_id"] = request.candidate_scope_group_id
        if request.orchestration_schema_version is not None:
            extend_info["orchestration_schema_version"] = (
                request.orchestration_schema_version
            )
        return extend_info or None

    async def execute(self, request: ExecutionRequest) -> ExecutionAck:
        hitl_rejection = await self._reject_if_hitl_pending(request)
        if hitl_rejection is not None:
            return hitl_rejection

        active_run_rejection = await self._reject_if_room_has_active_run(request)
        if active_run_rejection is not None:
            return active_run_rejection

        room_request = RoomCenterUserMessageRequest(
            room_id=request.room_id,
            user_id=request.sender_id,
            user_name=request.sender_name,
            message=request.message,
            attachments=request.attachments,
            inline_file_ids=request.inline_file_ids,
            client_request_id=request.client_request_id,
            extend_info=self._room_request_extend_info(request),
        )
        (
            persisted_response,
            preflight_context,
        ) = await self._room_center.persist_message_to_room(
            room_request,
            request.target_group,
            request.mentioned_agent_ids,
        )
        persisted_ack = room_response_to_execution_ack(persisted_response)
        try:
            await self._emit_room_preflight_processing_status(request, persisted_ack)
        except Exception:
            logger.warning(
                "room preflight processing status emission failed after persistence",
                exc_info=True,
            )
        if preflight_context is None:
            return persisted_ack
        response = await self._room_center.run_message_preflight_to_room(
            preflight_context
        )
        ack = room_response_to_execution_ack(response)
        try:
            await self._emit_room_preflight_terminal_status(request, ack)
        except Exception:
            logger.warning(
                "room preflight terminal status emission failed after preflight",
                exc_info=True,
            )
        return ack

    async def start_orchestration(
        self,
        request: ExecutionRequest,
        ack: ExecutionAck,
    ) -> None:
        if not ack.success or not ack.message_id or not ack.should_start_orchestration:
            return
        orchestration_request = OrchestrationRequest(
            room_id=request.room_id,
            room_user_message_id=ack.message_id,
            room_related_message_id=request.parent_message_id,
            user_id=request.sender_id,
            client_request_id=request.client_request_id,
        )
        with bind_log_context(
            client_request_id=request.client_request_id,
            room_id=request.room_id,
            user_message_id=ack.message_id,
            message_id=ack.message_id,
        ):
            task = self._spawn_orchestration(
                self._room_message_center.process_room_user_message(
                    orchestration_request
                ),
                name=f"execution-orchestrate-{ack.message_id}",
                room_id=request.room_id,
                message_id=ack.message_id,
                client_request_id=request.client_request_id,
            )
        await task

    def schedule_recovery_orchestration(
        self,
        request: OrchestrationRequest,
        *,
        reason: str,
    ) -> asyncio.Task[Any]:
        message_id = (
            request.room_user_message_id or request.room_agent_message_id or "unknown"
        )
        with bind_log_context(
            client_request_id=request.client_request_id,
            room_id=request.room_id,
            user_message_id=message_id,
            message_id=message_id,
        ):
            return self._spawn_orchestration(
                self._room_message_center.process_room_user_message(request),
                name=f"execution-recovery-{reason}-{message_id}",
                room_id=request.room_id,
                message_id=message_id,
                client_request_id=request.client_request_id,
            )

    def _spawn_orchestration(
        self,
        coro,
        *,
        name: str,
        room_id: str | None = None,
        message_id: str | None = None,
        client_request_id: str | None = None,
    ) -> asyncio.Task[Any]:
        task = self._task_factory(coro, name=name)
        self._inflight.add(task)
        self._inflight_metadata[task] = {
            "room_id": room_id,
            "message_id": message_id,
            "client_request_id": client_request_id,
        }

        def _on_done(done: asyncio.Task) -> None:
            self._inflight.discard(done)
            self._inflight_metadata.pop(done, None)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.error(
                    "execution orchestration task failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_on_done)
        return task

    async def cancel(
        self,
        room_id: str,
        message_id: str,
        *,
        requested_by_user_id: str,
    ) -> bool:
        if self._orchestration_run_store is not None:
            current = await self._orchestration_run_store.get_latest_by_user_message_id(
                message_id
            )
            if (
                current is not None
                and current.status in TERMINAL_ORCHESTRATION_STATUSES
            ):
                logger.info(
                    "cancellation ignored for terminal orchestration",
                    extra={
                        "message_id": message_id,
                        "run_id": current.run_id,
                        "status": current.status.value,
                    },
                )
                return True
        persisted = await self._cancellation_store.cancel_message(
            message_id,
            requested_by_user_id,
        )
        if not persisted:
            return False
        await self._cancellation_state.cancel_message_and_broadcast(message_id)
        await self._hitl_message_cancellation.cancel_requests_for_message(message_id)
        sidecar_canceled = await self._cancel_orchestration_sidecar(message_id)
        if sidecar_canceled:
            try:
                projected = (
                    await self._room_center.update_user_message_orchestration_status(
                        message_id,
                        OrchestrationStatus.CANCELED.value,
                    )
                )
            except Exception:
                projected = False
                logger.warning(
                    "failed to project canceled orchestration status",
                    extra={"message_id": message_id, "room_id": room_id},
                    exc_info=True,
                )
            if not projected:
                logger.warning(
                    "canceled orchestration status was not persisted",
                    extra={"message_id": message_id, "room_id": room_id},
                )
        await emit_processing_status(
            room_id=room_id,
            status="canceled",
            message_id=message_id,
            lifecycle_message_id=message_id,
            run_lifecycle=self._run_lifecycle,
            event_publisher=self._event_publisher,
            run_event_enabled=self._run_event_enabled,
            client_request_id_resolver=self._client_request_id_resolver,
        )
        try:
            await self._agent_task_cleanup.cleanup_cancelled_message_tasks(
                room_id=room_id,
                message_id=message_id,
            )
        except Exception:
            logger.warning("agent task cleanup failed for cancellation", exc_info=True)
        return True

    async def _cancel_orchestration_sidecar(self, user_message_id: str) -> bool:
        """Terminalize the paused orchestration state when a run is canceled."""
        if self._orchestration_run_store is None:
            return False
        for _ in range(3):
            current = await self._orchestration_run_store.get_latest_by_user_message_id(
                user_message_id
            )
            if current is None:
                return False
            if current.status in TERMINAL_ORCHESTRATION_STATUSES:
                return current.status == OrchestrationStatus.CANCELED
            updated = deepcopy(current)
            updated.status = OrchestrationStatus.CANCELED
            updated.terminal_reason = "request canceled"
            updated.pending_hitl_request_ids.clear()
            updated.pending_agent_continuations.clear()
            for question in updated.open_questions:
                if question.get("status") == "open":
                    question["status"] = "canceled"
            updated.state_version = current.state_version + 1
            updated.updated_at = utcnow()
            try:
                await self._orchestration_run_store.save_state(
                    updated,
                    expected_version=current.state_version,
                )
                return True
            except OrchestrationStoreConflict:
                continue
        logger.warning(
            "failed to terminalize orchestration sidecar after cancellation",
            extra={"user_message_id": user_message_id},
        )
        return False

    async def get_run(self, run_id: str) -> RunInfo | None:
        return await self._run_reader.get_run(run_id)

    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]:
        return await self._run_reader.get_runs_for_room(room_id)

    async def cancel_inflight_tasks(self) -> int:
        task_metadata = {
            task: (self._inflight_metadata.get(task) or {})
            for task in set(self._inflight)
            if not task.done()
        }
        for task in task_metadata:
            task.cancel()
        if task_metadata:
            await asyncio.gather(*task_metadata, return_exceptions=True)

        canceled_count = 0
        for task, metadata in task_metadata.items():
            if not task.cancelled():
                continue
            room_id = metadata.get("room_id")
            message_id = metadata.get("message_id")
            if not room_id or not message_id:
                continue
            try:
                await emit_processing_status(
                    room_id=room_id,
                    status="canceled",
                    message_id=message_id,
                    lifecycle_message_id=message_id,
                    run_lifecycle=self._run_lifecycle,
                    event_publisher=self._event_publisher,
                    run_event_enabled=self._run_event_enabled,
                    client_request_id_resolver=self._client_request_id_resolver,
                    client_request_id=metadata.get("client_request_id"),
                )
            except Exception:
                logger.warning(
                    "execution shutdown failed to mark orchestration canceled",
                    exc_info=True,
                )
            canceled_count += 1
        return canceled_count

    async def heal_diverged_runs(self, limit: int = 500) -> int:
        return await self._run_lifecycle.heal_diverged_runs(limit=limit)

    async def create_hitl_request(
        self,
        room_id: str,
        user_message_id: str,
        prompt: str,
        source: str,
        **kwargs: Any,
    ) -> HITLRequest | None:
        result = await self._hitl_manager.request_input(
            room_id=room_id,
            user_message_id=user_message_id,
            source=source,
            prompt=prompt,
            **kwargs,
        )
        return model_hitl_request_to_common(result) if result is not None else None

    async def resolve_hitl(
        self,
        room_id: str,
        request_id: str,
        response: str,
        responder_id: str,
    ) -> HITLResponse:
        result = await self._hitl_manager.handle_response(
            room_id=room_id,
            request_id=request_id,
            user_input=response,
            user_id=responder_id,
        )
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        saved_state = await self._record_resolved_hitl_on_orchestration_run(
            hitl_result=result,
            response=response,
        )
        self._schedule_orchestration_after_hitl_if_needed(
            state=saved_state,
            hitl_result=result,
        )
        return hitl_response_dict_to_common(result)

    async def _record_resolved_hitl_on_orchestration_run(
        self,
        *,
        hitl_result: dict[str, Any],
        response: str,
    ) -> OrchestrationRunState | None:
        if self._orchestration_run_store is None:
            return None
        run_id = hitl_result.get("orchestration_run_id")
        request_id = hitl_result.get("request_id")
        if not isinstance(run_id, str) or not run_id:
            return None
        if not isinstance(request_id, str) or not request_id:
            return None

        for _attempt in range(2):
            state = await self._orchestration_run_store.get_run(run_id)
            if state is None:
                return None
            expected_version = state.state_version
            updated = record_hitl_resolution(
                state,
                request_id=request_id,
                response=response,
                hitl_result=hitl_result,
            )
            try:
                saved = await self._orchestration_run_store.save_state(
                    updated,
                    expected_version=expected_version,
                )
            except OrchestrationStoreConflict:
                continue

            try:
                await self._orchestration_run_store.append_event(
                    OrchestrationRunEvent(
                        run_id=saved.run_id,
                        room_id=saved.room_id,
                        type=OrchestrationEventType.HITL_RESOLVED,
                        state_version=saved.state_version,
                        payload={
                            "request_ids": [request_id],
                            "answer_recorded": True,
                            "source": hitl_result.get("source"),
                        },
                    )
                )
            except Exception:
                logger.debug(
                    "Failed to append orchestration HITL resolution event",
                    exc_info=True,
                )
            return saved

        raise OrchestrationStoreConflict(
            "failed to record resolved HITL after repeated orchestration store "
            f"conflicts for run {run_id!r} and request {request_id!r}"
        )

    @staticmethod
    def _has_open_pending_hitl(state: OrchestrationRunState) -> bool:
        pending_request_ids = {
            request_id
            for request_id in state.pending_hitl_request_ids
            if isinstance(request_id, str)
        }
        if not pending_request_ids:
            return False
        return any(
            isinstance(question, dict)
            and question.get("request_id") in pending_request_ids
            and question.get("status") in {"open", "creating"}
            for question in state.open_questions
        )

    def _schedule_orchestration_after_hitl_if_needed(
        self,
        *,
        state: OrchestrationRunState | None,
        hitl_result: dict[str, Any],
    ) -> None:
        if state is None:
            return
        if hitl_result.get("resume_execution") is False:
            return
        if self._has_open_pending_hitl(state):
            return

        request = OrchestrationRequest(
            room_id=state.room_id,
            room_user_message_id=state.user_message_id,
            user_id=hitl_result.get("responder_id"),
            is_recovery=True,
            reuse_processing_claim=True,
            client_request_id=state.client_request_id,
        )
        self.schedule_recovery_orchestration(
            request,
            reason="hitl-resolved",
        )

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]:
        requests = await self._hitl_manager.get_pending_requests(room_id)
        return [model_hitl_request_to_common(request) for request in requests]

    async def cancel_hitl(self, room_id: str, request_id: str) -> bool:
        result = await self._hitl_manager.cancel_request(request_id, room_id=room_id)
        return hitl_cancel_none_to_success(result)

    async def handle_hub_agent_response(
        self,
        event: HubAgentResponseInternal,
    ) -> None:
        await self._agent_response_handler.handle(
            hub_agent_response_internal_to_agent_event(event)
        )


__all__ = [
    "ExecutionFacade",
    "hub_agent_response_internal_to_agent_event",
]
