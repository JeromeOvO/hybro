from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from common.a2a_constants import SSEProcessingStatus
from common.dto import (
    CancellationAck,
    ExecutionAck,
    ExecutionRequest,
    HITLRequest,
    HITLResponse,
    HubAgentResponseInternal,
    RunInfo,
)
from common.observability import bind_log_context, traced_create_task
from common.protocols import EventPublisher
from common.types import Task, TaskStatusUpdateEvent
from common.utils.logger import get_logger
from execution.cancellation.finalizer import (
    CancellationFinalizationResult,
    CancellationFinalizer,
)
from execution.cancellation.ports import (
    CancellationMarkerRepositoryPort,
    CancellationMessageReaderPort,
)
from execution.cancellation.service import CancellationService
from execution.dispatch.a2a_interaction import input_observation_from_a2a
from execution.dispatch.agent_event import AgentEvent
from execution.events import emit_room_processing_status
from execution.hitl.translators import (
    hitl_response_dict_to_common,
    model_hitl_request_to_common,
)
from execution.idempotency import (
    IDEMPOTENCY_FINGERPRINT_VERSION,
    build_execution_request_fingerprint,
    normalize_client_request_id,
)
from execution.orchestration.run_reducer import record_hitl_resolution
from execution.orchestration.run_store import (
    DuplicateEventIdConflict,
    OrchestrationRunStore,
    OrchestrationStoreConflict,
)
from execution.orchestrator_routing import (
    OWNER_LEGACY,
    OWNER_ORCHESTRATOR,
    UnsupportedEnvelopeError,
)
from execution.ports import (
    AgentResponseHandlerPort,
    AgentTaskCleanupPort,
    CancellationStatePort,
    ClientRequestIdResolver,
    HITLMessageCancellationPort,
    RunEventEnabled,
    RunLifecyclePort,
    RunReadPort,
    TaskFactory,
)
from execution.shutdown import GRACEFUL_SHUTDOWN_CANCEL_REASON
from execution.translators import room_response_to_execution_ack
from models.orchestration import (
    OrchestrationEventType,
    OrchestrationRunEvent,
    OrchestrationRunState,
    OrchestrationStatus,
)
from models.request import OrchestrationRequest, RoomCenterUserMessageRequest
from models.run import RunState

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


@dataclass(frozen=True)
class _RequestIdempotency:
    client_request_id: str | None = None
    fingerprint: str | None = None
    fingerprint_version: int | None = None


def _orchestrator_cancellation_ack(results: dict[str, str]) -> CancellationAck:
    """Translate per-call cancellation outcomes into an honest public ack.

    The orchestrator cancellation coordinator returns one state per call. A
    call still ``cancel_pending`` means its remote effect was not reconciled in
    this attempt, so the Run-level ack must not claim full reconciliation.
    """
    if not results:
        # A Run with no in-flight calls has nothing left to reconcile.
        return CancellationAck(
            status="canceled", cancellation_applied=True, reconciled=True
        )
    if any(state == "cancel_pending" for state in results.values()):
        return CancellationAck(
            status="cancellation_pending",
            cancellation_applied=False,
            reconciled=False,
        )
    return CancellationAck(
        status="canceled", cancellation_applied=True, reconciled=True
    )


class RoomCenterPort(Protocol):
    async def get_idempotent_user_message(
        self,
        *,
        room_id: str,
        client_request_id: str,
        idempotency_fingerprint: str,
        idempotency_fingerprint_version: int,
    ) -> Any | None: ...

    async def send_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: Any = None,
        mentioned_agent_ids: Any = None,
        *,
        idempotency_fingerprint: str | None = None,
        idempotency_fingerprint_version: int | None = None,
    ) -> Any: ...

    async def persist_message_to_room(
        self,
        request: RoomCenterUserMessageRequest,
        target_group: Any = None,
        mentioned_agent_ids: Any = None,
        *,
        idempotency_fingerprint: str | None = None,
        idempotency_fingerprint_version: int | None = None,
    ) -> tuple[Any, Any | None]: ...

    async def run_message_preflight_to_room(self, context: Any) -> Any: ...

    def discard_message_preflight(self, context: Any) -> None: ...

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
    async def request_interaction(self, **kwargs: Any) -> list[Any] | None: ...

    async def handle_response(
        self,
        room_id: str,
        request_id: str,
        user_input: str,
        user_id: str,
    ) -> dict[str, Any]: ...

    async def handle_batch_response(
        self,
        room_id: str,
        interaction_id: str,
        answers: list[dict[str, str]],
        user_id: str,
        client_request_id: str | None = None,
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


def _hub_interactive_observation(
    event: HubAgentResponseInternal,
    payload: dict[str, Any],
):
    task_payload = payload.get("task")
    if isinstance(task_payload, dict):
        source = Task.model_validate(task_payload)
        if source.id != event.task_id:
            raise ValueError("Hub interactive Task id conflicts with event.task_id")
    else:
        update_payload = payload.get("status_update")
        if isinstance(update_payload, dict):
            source = TaskStatusUpdateEvent.model_validate(update_payload)
        else:
            status_payload = payload.get("_a2a_status")
            if not isinstance(status_payload, dict):
                return None
            if not payload.get("context_id"):
                return None
            source = TaskStatusUpdateEvent.model_validate(
                {
                    "taskId": event.task_id,
                    "contextId": payload.get("context_id"),
                    "status": status_payload,
                    "final": False,
                }
            )
        if source.task_id != event.task_id:
            raise ValueError("Hub status task id conflicts with event.task_id")
    return input_observation_from_a2a(source)


def hub_agent_response_internal_to_agent_event(
    event: HubAgentResponseInternal,
) -> AgentEvent:
    payload = _thaw_hub_payload_value(event.payload)
    kind = _hub_payload_kind(payload)
    _validate_hub_event_consistency(event, kind, payload)
    _validate_hub_payload_for_kind(kind, payload)
    observation = (
        _hub_interactive_observation(event, payload) if kind == "interactive" else None
    )
    return AgentEvent(
        kind=kind,
        room_id=event.room_id,
        message_id=_hub_payload_message_id(payload),
        agent_id=event.agent_id,
        task_id=event.task_id,
        turn_id=_optional_hub_str(payload, "turn_id"),
        text=(
            ""
            if kind == "interactive"
            else _optional_hub_str(payload, "text", default="") or ""
        ),
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
        input_observation=observation,
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
        cancellation_repository: CancellationMarkerRepositoryPort,
        cancellation_message_reader: CancellationMessageReaderPort,
        hitl_message_cancellation: HITLMessageCancellationPort,
        agent_task_cleanup: AgentTaskCleanupPort,
        agent_response_handler: AgentResponseHandlerPort,
        event_publisher: EventPublisher,
        run_event_enabled: RunEventEnabled,
        client_request_id_resolver: ClientRequestIdResolver,
        orchestration_run_store: OrchestrationRunStore | None = None,
        task_factory: TaskFactory = traced_create_task,
        orchestrator_router: Any | None = None,
    ) -> None:
        self._room_center = room_center
        self._room_message_center = room_message_center
        self._orchestrator_router = orchestrator_router
        self._hitl_manager = hitl_manager
        self._run_lifecycle = run_lifecycle
        self._run_reader = run_reader
        self._cancellation_state = cancellation_state
        self._hitl_message_cancellation = hitl_message_cancellation
        self._agent_task_cleanup = agent_task_cleanup
        self._agent_response_handler = agent_response_handler
        self._event_publisher = event_publisher
        self._run_event_enabled = run_event_enabled
        self._client_request_id_resolver = client_request_id_resolver
        self._orchestration_run_store = orchestration_run_store
        cancellation_finalizer = CancellationFinalizer(
            run_store=orchestration_run_store,
            project_status=self._project_orchestration_status,
            broadcast_cancellation=cancellation_state.cancel_message_and_broadcast,
            get_active_token=cancellation_state.get_active_token,
            release_active_token=cancellation_state.release_active_token,
            clear_cancellation=cancellation_state.clear_cancellation,
            cancel_hitl=hitl_message_cancellation.cancel_requests_for_message,
            project_public_terminal=self._project_public_terminal_status,
            cleanup_agent_tasks=agent_task_cleanup.cleanup_cancelled_message_tasks,
            mark_reconciled=cancellation_repository.mark_reconciled,
            get_public_run=getattr(
                run_reader,
                "get_run_strict",
                run_reader.get_run,
            ),
        )
        self._cancellation_service = CancellationService(
            repository=cancellation_repository,
            finalizer=cancellation_finalizer,
            message_reader=cancellation_message_reader,
        )
        self._task_factory = task_factory
        self._inflight: set[asyncio.Task] = set()

    def bind_orchestrator_router(self, router: Any) -> None:
        """Attach the dual-routing seam after composition (container-wired)."""
        self._orchestrator_router = router

    @staticmethod
    def _prepare_request_idempotency(
        request: ExecutionRequest,
    ) -> tuple[ExecutionRequest, _RequestIdempotency]:
        if not isinstance(request.client_request_id, str):
            return request, _RequestIdempotency()
        client_request_id = normalize_client_request_id(request.client_request_id)
        if client_request_id != request.client_request_id:
            request = request.model_copy(
                update={"client_request_id": client_request_id}
            )
        if not client_request_id:
            return request, _RequestIdempotency()
        return request, _RequestIdempotency(
            client_request_id=client_request_id,
            fingerprint=build_execution_request_fingerprint(request),
            fingerprint_version=IDEMPOTENCY_FINGERPRINT_VERSION,
        )

    async def _lookup_idempotent_ack(
        self,
        *,
        request: ExecutionRequest,
        idempotency: _RequestIdempotency,
    ) -> ExecutionAck | None:
        if (
            idempotency.client_request_id is None
            or idempotency.fingerprint is None
            or idempotency.fingerprint_version is None
        ):
            return None
        response = await self._room_center.get_idempotent_user_message(
            room_id=request.room_id,
            client_request_id=idempotency.client_request_id,
            idempotency_fingerprint=idempotency.fingerprint,
            idempotency_fingerprint_version=idempotency.fingerprint_version,
        )
        return (
            room_response_to_execution_ack(response) if response is not None else None
        )

    async def _replay_or_rejection(
        self,
        *,
        request: ExecutionRequest,
        idempotency: _RequestIdempotency,
        rejection: ExecutionAck,
    ) -> ExecutionAck:
        replay_ack = await self._lookup_idempotent_ack(
            request=request,
            idempotency=idempotency,
        )
        return replay_ack or rejection

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
    def _room_request_extend_info(request: ExecutionRequest) -> dict[str, Any]:
        return {
            "execution_mode": request.mode,
            "agent_scope": request.agent_scope.model_dump(mode="json"),
        }

    @staticmethod
    def _scope_routing(request: ExecutionRequest) -> tuple[str, list[str] | None]:
        scope = request.agent_scope
        if scope.source == "mention":
            return "room_team", list(scope.agent_ids)
        if scope.source == "all_agents":
            return "all_agents", None
        if scope.source == "saved_group":
            return scope.group_id, None
        return "room_team", None

    async def execute(self, request: ExecutionRequest) -> ExecutionAck:
        request, idempotency = self._prepare_request_idempotency(request)
        room_request = RoomCenterUserMessageRequest(
            room_id=request.room_id,
            user_id=request.sender_id,
            user_name=request.sender_name,
            message=request.message,
            attachments=request.attachments,
            inline_file_ids=request.inline_file_ids,
            client_request_id=idempotency.client_request_id,
            extend_info=self._room_request_extend_info(request),
        )
        replay_ack = await self._lookup_idempotent_ack(
            request=request,
            idempotency=idempotency,
        )
        if replay_ack is not None:
            return replay_ack

        hitl_rejection = await self._reject_if_hitl_pending(request)
        if hitl_rejection is not None:
            return await self._replay_or_rejection(
                request=request,
                idempotency=idempotency,
                rejection=hitl_rejection,
            )

        active_run_rejection = await self._reject_if_room_has_active_run(request)
        if active_run_rejection is not None:
            return await self._replay_or_rejection(
                request=request,
                idempotency=idempotency,
                rejection=active_run_rejection,
            )

        target_group, mentioned_agent_ids = self._scope_routing(request)
        (
            persisted_response,
            preflight_context,
        ) = await self._room_center.persist_message_to_room(
            room_request,
            target_group,
            mentioned_agent_ids,
            idempotency_fingerprint=idempotency.fingerprint,
            idempotency_fingerprint_version=idempotency.fingerprint_version,
        )
        if preflight_context is None:
            return room_response_to_execution_ack(persisted_response)
        try:
            persisted_ack = room_response_to_execution_ack(persisted_response)
            try:
                await self._emit_room_preflight_processing_status(
                    request, persisted_ack
                )
            except Exception:
                logger.warning(
                    "room preflight processing status emission failed after persistence",
                    exc_info=True,
                )
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
        except BaseException:
            try:
                self._room_center.discard_message_preflight(preflight_context)
            except BaseException:
                logger.warning(
                    "room preflight cleanup failed while preserving original error",
                    exc_info=True,
                )
            raise

    async def _route_orchestration(
        self,
        request: ExecutionRequest,
        orchestration_request: OrchestrationRequest,
    ) -> None:
        owner = OWNER_LEGACY
        router = self._orchestrator_router
        if router is not None:
            try:
                owner = await router.assign_runtime(
                    room_id=request.room_id,
                    client_request_id=request.client_request_id,
                    user_id=request.sender_id,
                    mode=request.mode,
                    agent_scope=orchestration_request.agent_scope,
                )
            except Exception:
                logger.warning(
                    "orchestrator routing failed; falling back to legacy",
                    exc_info=True,
                )
                owner = OWNER_LEGACY
            if owner == OWNER_ORCHESTRATOR:
                try:
                    await router.preflight_room_user_message(orchestration_request)
                except UnsupportedEnvelopeError:
                    logger.info(
                        "orchestrator envelope unsupported; falling back to legacy",
                        extra={"room_id": request.room_id},
                    )
                    owner = OWNER_LEGACY
                except Exception:
                    logger.warning(
                        "orchestrator envelope preflight failed; "
                        "falling back to legacy",
                        exc_info=True,
                    )
                    owner = OWNER_LEGACY
        if owner == OWNER_ORCHESTRATOR:
            try:
                await router.process_room_user_message(orchestration_request)
                return
            except UnsupportedEnvelopeError:
                logger.info(
                    "orchestrator message adapter rejected envelope; "
                    "falling back to legacy",
                    extra={"room_id": request.room_id},
                )
        await self._room_message_center.process_room_user_message(orchestration_request)

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
            mode=request.mode,
            agent_scope=request.agent_scope.model_dump(mode="json"),
        )
        with bind_log_context(
            client_request_id=request.client_request_id,
            room_id=request.room_id,
            user_message_id=ack.message_id,
            message_id=ack.message_id,
        ):
            task = self._spawn_orchestration(
                self._route_orchestration(request, orchestration_request),
                name=f"execution-orchestrate-{ack.message_id}",
            )
        await task

    def schedule_orchestration(
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
            mode=request.mode,
            agent_scope=request.agent_scope.model_dump(mode="json"),
        )
        with bind_log_context(
            client_request_id=request.client_request_id,
            room_id=request.room_id,
            user_message_id=ack.message_id,
            message_id=ack.message_id,
        ):
            self._spawn_orchestration(
                self._route_orchestration(request, orchestration_request),
                name=f"execution-orchestrate-{ack.message_id}",
            )

    def schedule_recovery_orchestration(
        self,
        request: OrchestrationRequest,
        *,
        reason: str,
    ) -> asyncio.Task[Any]:
        message_id = (
            request.room_user_message_id or request.room_agent_message_id or "unknown"
        )

        async def _recover() -> None:
            # Recovery must respect persisted runtime ownership: a user
            # message owned by the orchestrator runtime is recovered by its
            # own A2A recovery cycle, never re-entered into the legacy
            # executor (dual execution of one turn).
            router = self._orchestrator_router
            if router is not None and request.room_user_message_id:
                try:
                    owner = await router.resolve_run_owner_by_user_message(
                        request.room_user_message_id
                    )
                except Exception:
                    logger.warning(
                        "recovery ownership lookup failed; falling back to legacy",
                        exc_info=True,
                    )
                    owner = OWNER_LEGACY
                if owner == OWNER_ORCHESTRATOR:
                    logger.info(
                        "recovery skipped: user message is owned by the "
                        "orchestrator runtime",
                        extra={
                            "room_id": request.room_id,
                            "user_message_id": request.room_user_message_id,
                        },
                    )
                    return
            await self._room_message_center.process_room_user_message(request)

        with bind_log_context(
            client_request_id=request.client_request_id,
            room_id=request.room_id,
            user_message_id=message_id,
            message_id=message_id,
        ):
            return self._spawn_orchestration(
                _recover(),
                name=f"execution-recovery-{reason}-{message_id}",
            )

    def _spawn_orchestration(
        self,
        coro,
        *,
        name: str,
    ) -> asyncio.Task[Any]:
        task = self._task_factory(coro, name=name)
        self._inflight.add(task)

        def _on_done(done: asyncio.Task) -> None:
            self._inflight.discard(done)
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

    async def _project_orchestration_status(
        self,
        *,
        room_id: str,
        message_id: str,
        status: OrchestrationStatus,
    ) -> bool:
        try:
            return bool(
                await self._room_center.update_user_message_orchestration_status(
                    message_id,
                    status.value,
                )
            )
        except Exception:
            logger.warning(
                "failed to project orchestration status",
                extra={
                    "message_id": message_id,
                    "room_id": room_id,
                    "status": status.value,
                },
                exc_info=True,
            )
            return False

    async def _project_public_terminal_status(
        self,
        *,
        room_id: str,
        message_id: str,
        status: OrchestrationStatus,
    ) -> None:
        target_state = {
            OrchestrationStatus.COMPLETED: RunState.COMPLETED,
            OrchestrationStatus.CANCELED: RunState.CANCELED,
            OrchestrationStatus.FAILED: RunState.FAILED,
            OrchestrationStatus.BUDGET_EXHAUSTED: RunState.FAILED,
        }[status]
        projected = await self._run_lifecycle.project_run_state(
            room_id=room_id,
            run_id=message_id,
            trigger_message_id=message_id,
            target_state=target_state,
            terminal_reason=(
                "request canceled" if target_state == RunState.CANCELED else None
            ),
            causation_id=f"orchestration-terminal-repair:{message_id}:{status.value}",
        )
        if projected is None:
            strict_get_run = getattr(
                self._run_reader,
                "get_run_strict",
                self._run_reader.get_run,
            )
            public_run = await strict_get_run(message_id)
            public_state = getattr(
                getattr(public_run, "state", None),
                "value",
                getattr(public_run, "state", None),
            )
            if public_state != target_state.value:
                raise RuntimeError("public terminal lifecycle projection failed")

    async def finalize_pending_cancellation(
        self,
        *,
        room_id: str,
        message_id: str,
        settle_no_run: bool = False,
    ) -> CancellationFinalizationResult:
        return await self._cancellation_service.finalize(
            room_id=room_id,
            message_id=message_id,
            settle_no_run=settle_no_run,
        )

    @property
    def cancellation_service(self) -> CancellationService:
        return self._cancellation_service

    async def cancel(
        self,
        room_id: str,
        message_id: str,
        *,
        requested_by_user_id: str,
    ) -> bool | CancellationAck:
        router = self._orchestrator_router
        if router is not None:
            try:
                owner = await router.resolve_run_owner_by_user_message(message_id)
            except Exception:
                logger.warning(
                    "orchestrator cancel ownership resolution failed; "
                    "falling back to legacy",
                    exc_info=True,
                )
                owner = OWNER_LEGACY
            if owner == OWNER_ORCHESTRATOR:
                try:
                    results = await router.route_cancellation_by_user_message(
                        message_id,
                        reason=f"user:{requested_by_user_id}",
                    )
                    return _orchestrator_cancellation_ack(results)
                except Exception:
                    logger.exception(
                        "orchestrator cancellation routing failed after ownership "
                        "was resolved"
                    )
                    raise
        return await self._cancellation_service.cancel(
            room_id=room_id,
            message_id=message_id,
            requested_by_user_id=requested_by_user_id,
        )

    async def get_run(self, run_id: str) -> RunInfo | None:
        return await self._run_reader.get_run(run_id)

    async def get_runs_for_room(self, room_id: str) -> list[RunInfo]:
        return await self._run_reader.get_runs_for_room(room_id)

    async def get_latest_runs_for_rooms(
        self, room_ids: list[str]
    ) -> dict[str, RunInfo]:
        return await self._run_reader.get_latest_runs_for_rooms(room_ids)

    async def cancel_inflight_tasks(self) -> int:
        """Interrupt local execution without terminalizing durable runs.

        Graceful process shutdown is an infrastructure interruption, not a user
        cancellation. Non-terminal orchestration remains recoverable after the
        next process starts, so this method must not emit a public terminal state.
        """
        tasks = {task for task in set(self._inflight) if not task.done()}
        for task in tasks:
            task.cancel(GRACEFUL_SHUTDOWN_CANCEL_REASON)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return sum(task.cancelled() for task in tasks)

    async def heal_diverged_runs(self, limit: int = 500) -> int:
        return await self._run_lifecycle.heal_diverged_runs(limit=limit)

    async def _record_and_schedule_resolved_hitl(
        self,
        *,
        hitl_result: dict[str, Any],
        response: str,
    ) -> OrchestrationRunState | None:
        """Idempotently project answers and resume orchestration.

        The HITL application coordinator journals and fences this callback. Keeping
        scheduling inside the callback means recovery after a crash replays the
        complete supervisor effect rather than only recording the answer.
        """
        saved_state = await self._record_resolved_hitl_on_orchestration_run(
            hitl_result=hitl_result,
            response=response,
        )
        self._schedule_orchestration_after_hitl_if_needed(
            state=saved_state,
            hitl_result=hitl_result,
        )
        return saved_state

    async def _record_resolved_hitl_on_orchestration_run(  # noqa: C901
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
        answer_records = hitl_result.get("answer_records")
        if not isinstance(answer_records, list) or not answer_records:
            answer_records = [{"request_id": request_id, "response": response}]
        normalized_records = [
            record
            for record in answer_records
            if isinstance(record, dict)
            and isinstance(record.get("request_id"), str)
            and isinstance(record.get("response"), str)
        ]
        if not normalized_records:
            return None

        for _attempt in range(2):
            state = await self._orchestration_run_store.get_run(run_id)
            if state is None:
                return None
            expected_version = state.state_version
            updated = state
            for record in normalized_records:
                updated = record_hitl_resolution(
                    updated,
                    request_id=record["request_id"],
                    response=record["response"],
                    hitl_result=hitl_result,
                )
            try:
                saved = (
                    updated
                    if updated.state_version == expected_version
                    else await self._orchestration_run_store.save_state(
                        updated,
                        expected_version=expected_version,
                    )
                )
            except OrchestrationStoreConflict:
                continue

            interaction_id = hitl_result.get("interaction_id") or request_id
            revision = hitl_result.get("application_revision") or 1
            try:
                await self._orchestration_run_store.append_event(
                    OrchestrationRunEvent(
                        event_id=f"hitl-resolved:{run_id}:{interaction_id}:{revision}",
                        run_id=saved.run_id,
                        room_id=saved.room_id,
                        type=OrchestrationEventType.HITL_RESOLVED,
                        state_version=saved.state_version,
                        payload={
                            "request_ids": [
                                record["request_id"] for record in normalized_records
                            ],
                            "answer_recorded": True,
                            "source": hitl_result.get("source"),
                            "interaction_id": interaction_id,
                            "application_revision": revision,
                        },
                    )
                )
            except DuplicateEventIdConflict:
                pass
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

    async def resolve_hitl_batch(
        self,
        room_id: str,
        interaction_id: str,
        answers: list[dict[str, str]],
        responder_id: str,
        client_request_id: str | None = None,
    ) -> HITLResponse:
        router = self._orchestrator_router
        if router is not None:
            try:
                owner = await router.resolve_interaction_owner(interaction_id)
            except Exception:
                logger.warning(
                    "orchestrator HITL ownership resolution failed; "
                    "falling back to legacy",
                    exc_info=True,
                )
                owner = OWNER_LEGACY
            if owner == OWNER_ORCHESTRATOR:
                await router.route_hitl_answer(
                    interaction_id=interaction_id,
                    answers=answers,
                    responder_id=responder_id,
                    room_id=room_id,
                )
                return HITLResponse(
                    request_id=interaction_id,
                    status="accepted",
                    responder_id=responder_id,
                    client_request_id=client_request_id,
                )
        result = await self._hitl_manager.handle_batch_response(
            room_id=room_id,
            interaction_id=interaction_id,
            answers=answers,
            user_id=responder_id,
            client_request_id=client_request_id,
        )
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        result.setdefault("request_id", interaction_id)
        result.setdefault("responder_id", responder_id)
        if client_request_id:
            result.setdefault("client_request_id", client_request_id)
        if (
            result.get("status") != "accepted"
            and result.get("run_projection_status") != "applied"
        ):
            combined_response = "\n\n".join(
                answer.get("user_input", "")
                for answer in answers
                if isinstance(answer.get("user_input"), str)
            )
            await self._record_and_schedule_resolved_hitl(
                hitl_result=result,
                response=combined_response,
            )
        return hitl_response_dict_to_common(result)

    async def get_pending_hitl(self, room_id: str) -> list[HITLRequest]:
        requests = await self._hitl_manager.get_pending_requests(room_id)
        return [model_hitl_request_to_common(request) for request in requests]

    async def cancel_hitl_interaction(
        self,
        room_id: str,
        interaction_id: str,
        expected_version: int,
    ) -> int:
        return await self._hitl_manager.cancel_interaction_by_user(
            interaction_id,
            room_id,
            expected_version=expected_version,
        )

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
