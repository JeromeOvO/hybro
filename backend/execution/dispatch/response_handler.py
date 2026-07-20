"""AgentResponseHandler — single source of truth for processing agent results.

Terminal events delegate to ``notify_task_update`` for SSE emission.
Streaming events (artifact_update) use ``delivery port`` directly.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Protocol

from a2a_adapter.task_status import coerce_task_state
from common.a2a_constants import is_interactive_state
from common.config.settings import settings
from common.utils.a2a_helpers import (
    extract_text_from_artifact_dicts,
    filter_non_text_parts,
)
from common.utils.logger import get_logger
from execution.dispatch.agent_event import AgentEvent
from execution.orchestration.result_ingestor import AgentResultRead
from execution.task_tracking import public_artifact_data, public_part_data

if TYPE_CHECKING:
    from execution.ports import ExecutionDeliveryPort, TaskNotificationStorePort


class ResponseMessageWriter(Protocol):
    async def accumulate_artifact_on_message(
        self,
        message_id: str,
        artifact: dict,
        *,
        append: bool = False,
    ) -> bool: ...


class ResponseTaskWriter(Protocol):
    async def update_task_state_on_message(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None = None,
        artifacts: list[dict] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> tuple[bool, str | None]: ...


class ResponseContinuationStore(Protocol):
    async def get_pending_continuation_on_message(
        self,
        message_id: str,
    ) -> dict | None: ...


class ResponseClientRequestResolver(Protocol):
    async def resolve_client_request_id_for_message_id(
        self, message_id: str
    ) -> str | None: ...

    async def get_room_agent_message_by_message_id(self, message_id: str): ...

    async def resolve_client_request_id_for_agent_message(
        self, room_agent_message
    ) -> str | None: ...


class ResponseRoomReader(Protocol):
    async def get_room_by_room_id(self, room_id: str): ...


class ResponseHITLReader(Protocol):
    async def get_pending_hitl_requests_for_message(
        self, user_message_id: str
    ) -> list[dict]: ...


class OrchestrationResultIngestorService(Protocol):
    async def ingest_agent_result(self, result: AgentResultRead) -> Any: ...


logger = get_logger(__name__)
_orchestration_result_ingestor: OrchestrationResultIngestorService | None = None

_PUBLIC_TERMINAL_ERRORS = {
    "failed": "Task failed",
    "error": "Task failed",
    "rate_limited": "Task failed",
    "rejected": "Task was rejected by the agent",
    "canceled": "Task was canceled",
    "expired": "Task expired",
}


def _safe_terminal_error(status: str | None) -> str:
    return _PUBLIC_TERMINAL_ERRORS.get(str(status or "failed"), "Task failed")


def _part_payload(part: dict) -> dict:
    root = part.get("root")
    return root if isinstance(root, dict) else part


def _part_has_unaddressable_file(part: dict) -> bool:
    payload = _part_payload(part)
    file_payload = payload.get("file") if isinstance(payload, dict) else None
    return isinstance(file_payload, dict) and not file_payload.get("uri")


def _sanitize_public_parts(parts: list[dict] | None) -> list[dict]:
    sanitized: list[dict] = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        public_part = public_part_data(part)
        if public_part is None:
            continue
        if _part_has_unaddressable_file(public_part):
            continue
        sanitized.append(public_part)
    return sanitized


def _sanitize_public_artifacts(
    artifacts: list[dict] | None,
    *,
    keep_empty_artifacts: bool = False,
) -> list[dict]:
    sanitized: list[dict] = []
    for artifact in artifacts or []:
        if not isinstance(artifact, dict):
            continue
        public_artifact = public_artifact_data(artifact)
        public_artifact["parts"] = _sanitize_public_parts(
            public_artifact.get("parts") or []
        )
        if keep_empty_artifacts or public_artifact["parts"]:
            sanitized.append(public_artifact)
    return sanitized


def _materialized_text_artifact(message_id: str, text: str) -> dict:
    return {
        "artifactId": f"{message_id}-response",
        "name": "response",
        "parts": [{"kind": "text", "text": text}],
    }


def bind_orchestration_result_ingestor(
    service: OrchestrationResultIngestorService | None,
) -> None:
    global _orchestration_result_ingestor
    _orchestration_result_ingestor = service


class AgentResponseHandler:
    """Single source of truth for processing agent results.

    Terminal events delegate to notify_task_update for SSE emission.
    Streaming events (artifact_update) use delivery directly.
    """

    def __init__(
        self,
        message_writer: ResponseMessageWriter,
        task_writer: ResponseTaskWriter,
        continuation_store: ResponseContinuationStore,
        client_request_resolver: ResponseClientRequestResolver,
        room_reader: ResponseRoomReader,
        hitl_reader: ResponseHITLReader,
        delivery: ExecutionDeliveryPort,
        room_message_center: object,
        slot_lifecycle=None,
        hitl_coordinator=None,
        task_notifier=None,
        task_notification_store: TaskNotificationStorePort | None = None,
        task_notification_impl=None,
    ) -> None:
        self._message_writer = message_writer
        self._task_writer = task_writer
        self._continuation_store = continuation_store
        self._client_request_resolver = client_request_resolver
        self._room_reader = room_reader
        self._hitl_reader = hitl_reader
        self._delivery = delivery
        self._rmc = room_message_center
        self._slot_lifecycle = slot_lifecycle
        self.hitl_coordinator = hitl_coordinator
        self._task_notifier = task_notifier
        if task_notification_impl is not None and task_notification_store is None:
            raise RuntimeError("Task notification store dependency has not been bound")
        self._task_notification_store = task_notification_store
        self._task_notification_impl = task_notification_impl
        self._processing_status_emitter = None

    def bind_execution_event_deps(self, processing_status_emitter) -> None:
        self._processing_status_emitter = processing_status_emitter

    async def _resolve_client_request_id(self, e: AgentEvent) -> str | None:
        explicit = e.client_request_id
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        message_id = e.message_id
        if not isinstance(message_id, str) or not message_id.strip():
            return None

        resolve_from_message_id = getattr(
            self._client_request_resolver,
            "resolve_client_request_id_for_message_id",
            None,
        )
        if callable(resolve_from_message_id):
            maybe_resolved = resolve_from_message_id(message_id)
            resolved = (
                await maybe_resolved
                if inspect.isawaitable(maybe_resolved)
                else maybe_resolved
            )
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip()

        get_agent_message = getattr(
            self._client_request_resolver, "get_room_agent_message_by_message_id", None
        )
        resolve_from_agent_message = getattr(
            self._client_request_resolver,
            "resolve_client_request_id_for_agent_message",
            None,
        )
        if callable(get_agent_message) and callable(resolve_from_agent_message):
            maybe_room_agent_message = get_agent_message(message_id)
            room_agent_message = (
                await maybe_room_agent_message
                if inspect.isawaitable(maybe_room_agent_message)
                else maybe_room_agent_message
            )
            if room_agent_message is None:
                return None
            maybe_resolved = resolve_from_agent_message(room_agent_message)
            resolved = (
                await maybe_resolved
                if inspect.isawaitable(maybe_resolved)
                else maybe_resolved
            )
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip()

        return None

    async def _terminate_slot(
        self,
        e: AgentEvent,
        status: str,
        content: str | None = None,
        artifacts: list[dict] | None = None,
        error: str | None = None,
        has_partial_content: bool | None = None,
    ) -> None:
        """Emit slot_terminated turn event if slot_lifecycle is available and turn_id is set."""
        if not getattr(self, "_slot_lifecycle", None) or not e.turn_id:
            return
        try:
            await self._slot_lifecycle.terminate_slot(
                room_id=e.room_id,
                turn_id=e.turn_id,
                slot_id=e.message_id,
                status=status,
                content=content,
                artifacts=artifacts,
                error=error,
                has_partial_content=has_partial_content,
            )
        except Exception:
            logger.warning(
                "AgentResponseHandler: slot_terminated emission failed for %s",
                e.message_id,
                exc_info=True,
            )

    async def handle(self, event: AgentEvent) -> None:
        match event.kind:
            case "artifact_update":
                await self._on_artifact(event)
            case "response":
                await self._on_response(event)
            case "error":
                await self._on_error(event)
            case "canceled":
                await self._on_canceled(event)
            case "task_submitted":
                await self._on_submitted(event)
            case "status_update":
                await self._on_status(event)
            case "interactive":
                await self._on_interactive(event)
            case "processing_status":
                await self._on_processing_status(event)

    # --- Streaming events (direct SSE, no terminal DB persist) ---

    async def _on_artifact(self, e: AgentEvent) -> None:
        artifact = e.artifacts[0] if e.artifacts else None

        # Convert inline bytes to S3 before broadcasting.
        # Skip if the transport already performed conversion (s3_converted=True).
        if artifact and not e.s3_converted:
            artifact_parts = artifact.get("parts", [])
            if artifact_parts:
                try:
                    from common.utils.a2a_helpers import convert_inline_bytes_to_s3

                    await convert_inline_bytes_to_s3(
                        artifact_parts,
                        e.room_id,
                        e.message_id,
                    )
                except Exception:
                    logger.warning(
                        "S3 conversion failed for artifact on message %s; "
                        "dropping unaddressable file bytes before broadcast",
                        e.message_id,
                        exc_info=True,
                    )

        if artifact:
            sanitized_artifacts = _sanitize_public_artifacts(
                [artifact],
                keep_empty_artifacts=True,
            )
            artifact = sanitized_artifacts[0] if sanitized_artifacts else None
            e.artifacts = sanitized_artifacts
            e.parts = filter_non_text_parts(artifact.get("parts") if artifact else None)

        # SSE emission: artifact_update for real artifacts or synthetic text-only fallback
        sse_kw: dict = {}
        if e.client_request_id:
            sse_kw["client_request_id"] = e.client_request_id

        if artifact:
            logger.debug(
                "Sending artifact_update SSE for message %s (append=%s, last_chunk=%s)",
                e.message_id,
                e.append,
                e.last_chunk,
            )
            await self._delivery.send_artifact_update(
                room_id=e.room_id,
                message_id=e.message_id,
                agent_id=e.agent_id,
                artifact=artifact,
                append=e.append,
                last_chunk=e.last_chunk,
                **sse_kw,
            )
        elif e.text:
            fallback_artifact = {
                "artifact_id": f"{e.message_id}-stream",
                "parts": [{"kind": "text", "text": e.text}],
            }
            await self._delivery.send_artifact_update(
                room_id=e.room_id,
                message_id=e.message_id,
                agent_id=e.agent_id,
                artifact=fallback_artifact,
                append=e.append,
                last_chunk=e.last_chunk,
                **sse_kw,
            )

    # --- Terminal events (DB persist -> notify_task_update -> orchestration) ---

    async def _project_completed_output(  # noqa: C901
        self,
        e: AgentEvent,
    ) -> tuple[str | None, list[dict] | None]:
        had_structured_output = bool(e.parts or e.artifacts)
        # Convert inline base64 file parts to S3 URIs before public projection.
        if had_structured_output:
            from common.utils.a2a_helpers import convert_inline_bytes_to_s3

            try:
                # Convert parts referenced by both e.parts and nested inside e.artifacts
                if e.parts:
                    await convert_inline_bytes_to_s3(
                        e.parts,
                        e.room_id,
                        e.message_id,
                    )
                if e.artifacts:
                    for artifact in e.artifacts:
                        artifact_parts = artifact.get("parts", [])
                        if artifact_parts:
                            await convert_inline_bytes_to_s3(
                                artifact_parts,
                                e.room_id,
                                e.message_id,
                            )
            except Exception:
                logger.warning(
                    "S3 conversion failed for terminal artifacts on message %s; "
                    "dropping unaddressable file bytes before persistence",
                    e.message_id,
                    exc_info=True,
                )

        artifacts_for_db: list[dict] | None = None
        if e.artifacts:
            artifacts_for_db = _sanitize_public_artifacts(e.artifacts) or None
        elif e.parts:
            sanitized_parts = _sanitize_public_parts(e.parts)
            if sanitized_parts:
                artifacts_for_db = [
                    {
                        "artifactId": f"{e.message_id}-response",
                        "name": "response",
                        "parts": sanitized_parts,
                    }
                ]
        elif e.text:
            artifacts_for_db = [_materialized_text_artifact(e.message_id, e.text)]

        display_text = extract_text_from_artifact_dicts(artifacts_for_db)
        if e.text and not display_text and not artifacts_for_db and not had_structured_output:
            artifacts_for_db = [_materialized_text_artifact(e.message_id, e.text)]
            display_text = e.text

        display_artifacts = artifacts_for_db
        e.artifacts = artifacts_for_db
        e.parts = filter_non_text_parts(
            [part for artifact in artifacts_for_db or [] for part in artifact.get("parts") or []]
        )
        e.text = display_text or ""
        e.error_text = None
        e.details = None
        return display_text, display_artifacts

    async def _on_response(self, e: AgentEvent) -> None:  # noqa: C901
        display_text, display_artifacts = await self._project_completed_output(e)

        if not e.skip_persist:
            _, resolved_text = await self._task_writer.update_task_state_on_message(
                e.message_id,
                "completed",
                message_text=display_text,
                artifacts=display_artifacts,
            )
            if resolved_text:
                display_text = resolved_text
        await self._notify_terminal_best_effort(e, coerce_task_state("completed"))
        await self._terminate_slot(
            e,
            "completed",
            content=display_text,
            artifacts=display_artifacts,
        )
        # NOTE: send_agent_response removed — _notify() above already delivers
        # content + parts via task_update SSE. The redundant agent_response SSE
        # created a duplicate message entity in the frontend.
        await self._ingest_orchestration_result(
            e,
            status="completed",
            text=display_text,
            artifacts=display_artifacts or [],
        )
        await self._resume_orchestration(e.message_id, display_text or "")

    async def _on_error(self, e: AgentEvent) -> None:
        state = e.state or "failed"
        error = _safe_terminal_error(state)
        e.text = ""
        e.error_text = error
        e.parts = None
        e.artifacts = None
        if not e.skip_persist:
            await self._task_writer.update_task_state_on_message(
                e.message_id,
                state,
                message_text=error,
            )
        await self._notify_terminal_best_effort(
            e, coerce_task_state(state), error=error
        )
        await self._terminate_slot(
            e,
            "failed",
            content=None,
            error=error,
        )
        await self._ingest_orchestration_result(
            e,
            status=state,
            text=None,
            artifacts=[],
            error=error,
        )
        await self._resume_orchestration(e.message_id, "", failed=True)

    async def _on_canceled(self, e: AgentEvent) -> None:
        canceled_text = _safe_terminal_error("canceled")
        e.text = ""
        e.error_text = canceled_text
        e.parts = None
        e.artifacts = None
        if not e.skip_persist:
            await self._task_writer.update_task_state_on_message(
                e.message_id,
                "canceled",
                message_text=canceled_text,
            )
        await self._notify_terminal_best_effort(e, coerce_task_state("canceled"))
        await self._terminate_slot(e, "canceled")
        await self._ingest_orchestration_result(
            e,
            status="canceled",
            text=None,
            artifacts=[],
            error=canceled_text,
        )
        await self._resume_orchestration(e.message_id, "", failed=True)

    async def _on_interactive(self, e: AgentEvent) -> None:
        state = e.state or "input-required"
        prompt = e.text or "The agent needs additional information."
        e.text = ""
        e.details = None
        e.parts = None
        e.artifacts = None
        if not e.skip_persist:
            await self._task_writer.update_task_state_on_message(
                e.message_id,
                state,
                message_text=None,
                task_id=e.task_id,
                context_id=e.context_id,
            )

        # For async transports (relay, webhook) the queue has already moved
        # to PAUSED before this callback fires, so QueueExecutor never sees
        # AWAITING_INPUT.  Create the HITL request here when a continuation
        # is already saved (indicates an async callback, not inline dispatch).
        if is_interactive_state(state):
            await self._maybe_create_hitl_for_async_interactive(e, prompt=prompt)

        await self._notify(
            e,
            coerce_task_state(state),
            emit_processing_status=not is_interactive_state(state),
        )

    async def _maybe_create_hitl_for_async_interactive(
        self,
        e: AgentEvent,
        *,
        prompt: str | None = None,
    ) -> None:
        """Create HITL request for async transports (relay / webhook).

        Only acts when a pending_continuation already exists on the message,
        which proves this is an async callback — not an inline direct dispatch
        where QueueExecutor handles HITL creation itself.
        """
        continuation = (
            await self._continuation_store.get_pending_continuation_on_message(
                e.message_id
            )
        )
        if not continuation:
            return

        if self.hitl_coordinator is None:
            raise RuntimeError("HITL coordinator has not been bound")

        user_message_id = continuation.get("user_message_id", "")
        if not user_message_id:
            logger.warning(
                "_maybe_create_hitl_for_async_interactive: no user_message_id "
                "in continuation for %s",
                e.message_id,
            )
            return

        msg = await self._client_request_resolver.get_room_agent_message_by_message_id(
            e.message_id
        )
        agent_name = e.agent_name
        if not agent_name and msg:
            try:
                room = await self._room_reader.get_room_by_room_id(e.room_id)
                if room and room.room_agent_set:
                    agent_name = room.room_agent_set.get(e.agent_id)
            except Exception:
                logger.debug("agent name lookup failed", exc_info=True)

        hitl_req = await self.hitl_coordinator.request_input(
            room_id=e.room_id,
            user_message_id=user_message_id,
            source="agent",
            prompt=prompt or "The agent needs additional information.",
            agent_id=e.agent_id,
            agent_name=agent_name,
            a2a_task_id=e.task_id,
            a2a_context_id=e.context_id,
            continuation_message_id=e.message_id,
            display_message_id=msg.message_id if msg else e.message_id,
        )
        if hitl_req:
            await self._emit_processing_status(
                room_id=e.room_id,
                status="awaiting_input",
                message_id=user_message_id,
                lifecycle_message_id=user_message_id,
            )
            logger.info(
                "Created HITL request %s for async interactive event on %s",
                hitl_req.request_id,
                e.message_id,
            )

    async def _on_submitted(self, e: AgentEvent) -> None:
        client_request_id = await self._resolve_client_request_id(e)
        kw: dict = {}
        if client_request_id:
            kw["client_request_id"] = client_request_id
        await self._delivery.send_task_submitted(
            room_id=e.room_id,
            message_id=e.message_id,
            task_id=e.task_id,
            agent_name=e.agent_name,
            agent_id=e.agent_id,
            status="working",
            related_message_id=e.related_message_id,
            created_at=e.created_at,
            step_number=e.step_number,
            total_steps=e.total_steps,
            **kw,
        )

    async def _on_status(self, e: AgentEvent) -> None:
        e.text = ""
        e.parts = None
        e.artifacts = None

    async def _on_processing_status(self, e: AgentEvent) -> None:
        if not e.lifecycle_message_id:
            logger.warning(
                "AgentResponseHandler: dropping processing_status without "
                "lifecycle_message_id for %s",
                e.message_id,
            )
            return

        # Hub relay streaming path: when the hub suppresses `agent_response`
        # (content was already streamed via artifact_update), it only emits
        # processing_status(completed/failed/canceled). In that case neither
        # _on_response nor _on_error ever runs, so the DB task state is never
        # closed and no task_update SSE is emitted. Detect terminal statuses here
        # and close them out using the same public projection as terminal
        # response/error paths before any persistence, notification, lifecycle,
        # or orchestration-ingestion side effects run.
        terminal_result_status, terminal_task_state = (
            self._processing_terminal_status(e)
        )
        emit_details = None
        if terminal_result_status is not None:
            emit_details = await self._close_processing_status_terminal(
                e,
                terminal_result_status=terminal_result_status,
                terminal_task_state=terminal_task_state,
            )

        await self._emit_processing_status(
            room_id=e.room_id,
            status=e.state,
            message_id=e.message_id,
            lifecycle_message_id=e.lifecycle_message_id,
            record_lifecycle=True,
            client_request_id=await self._resolve_client_request_id(e),
            details=emit_details,
        )

    def _processing_terminal_status(self, e: AgentEvent) -> tuple[str | None, Any]:
        if not e.state:
            return None, None

        terminal_result_status: str | None = None
        terminal_task_state = None
        state_value = str(getattr(e.state, "value", e.state))
        if state_value in settings.terminal_processing_statuses:
            terminal_result_status = state_value
        try:
            from common.a2a_constants import TERMINAL_STATES

            state_enum = coerce_task_state(e.state)
            if state_enum in TERMINAL_STATES:
                terminal_result_status = getattr(
                    state_enum,
                    "value",
                    str(state_enum),
                )
                terminal_task_state = state_enum
        except (ValueError, KeyError):
            pass
        except Exception:
            logger.warning(
                "_on_processing_status: terminal close-out failed for %s",
                e.message_id,
                exc_info=True,
            )
        return terminal_result_status, terminal_task_state

    async def _close_processing_status_terminal(
        self,
        e: AgentEvent,
        *,
        terminal_result_status: str,
        terminal_task_state,
    ) -> str | None:
        error = self._terminal_result_error(e, terminal_result_status)
        if error:
            await self._close_processing_status_error(
                e,
                terminal_result_status=terminal_result_status,
                terminal_task_state=terminal_task_state,
                error=error,
            )
            return error

        await self._close_processing_status_completed(
            e,
            terminal_result_status=terminal_result_status,
            terminal_task_state=terminal_task_state,
        )
        return None

    async def _persist_processing_status_terminal(
        self,
        e: AgentEvent,
        *,
        status: str,
        message_text: str | None,
        artifacts: list[dict] | None,
    ) -> str | None:
        if e.skip_persist:
            return None
        try:
            _, resolved_text = await self._task_writer.update_task_state_on_message(
                e.message_id,
                status,
                message_text=message_text,
                artifacts=artifacts,
            )
            return resolved_text
        except Exception:
            logger.warning(
                "_on_processing_status: update_task_state_on_message failed for %s",
                e.message_id,
                exc_info=True,
            )
            return None

    async def _close_processing_status_error(
        self,
        e: AgentEvent,
        *,
        terminal_result_status: str,
        terminal_task_state,
        error: str,
    ) -> None:
        e.text = ""
        e.error_text = error
        e.parts = None
        e.artifacts = None
        e.details = None
        if terminal_task_state is not None:
            await self._persist_processing_status_terminal(
                e,
                status=terminal_result_status,
                message_text=error,
                artifacts=None,
            )
            await self._notify_terminal_best_effort(
                e,
                terminal_task_state,
                error=error,
                emit_processing_status=False,
            )
        await self._terminate_slot(
            e,
            "canceled" if terminal_result_status == "canceled" else "failed",
            content=None,
            error=error,
        )
        await self._ingest_orchestration_result(
            e,
            status=terminal_result_status,
            text=None,
            artifacts=[],
            error=error,
        )

    async def _close_processing_status_completed(
        self,
        e: AgentEvent,
        *,
        terminal_result_status: str,
        terminal_task_state,
    ) -> None:
        display_text, display_artifacts = await self._project_completed_output(e)
        if terminal_task_state is not None:
            resolved_text = await self._persist_processing_status_terminal(
                e,
                status=terminal_result_status,
                message_text=display_text,
                artifacts=display_artifacts,
            )
            if resolved_text:
                display_text = resolved_text
            await self._notify_terminal_best_effort(
                e,
                terminal_task_state,
                emit_processing_status=False,
            )
        await self._terminate_slot(
            e,
            "completed",
            content=display_text,
            artifacts=display_artifacts,
        )
        await self._ingest_orchestration_result(
            e,
            status=terminal_result_status,
            text=display_text,
            artifacts=display_artifacts or [],
        )

    # --- Helpers ---

    async def _emit_processing_status(
        self,
        *,
        room_id: str,
        status,
        message_id: str | None,
        lifecycle_message_id: str | None = None,
        record_lifecycle: bool = True,
        client_request_id: str | None = None,
        details=None,
    ) -> None:
        if self._processing_status_emitter is None:
            raise RuntimeError(
                "AgentResponseHandler execution event dependencies not bound"
            )
        status_value = status.value if hasattr(status, "value") else str(status)
        await self._processing_status_emitter(
            room_id=room_id,
            status=status,
            message_id=message_id,
            lifecycle_message_id=lifecycle_message_id,
            record_lifecycle=record_lifecycle,
            client_request_id=client_request_id,
            details=(
                details
                if isinstance(details, dict)
                else {"message": details}
                if isinstance(details, str)
                else None
            ),
            error_message=(
                details
                if isinstance(details, str)
                and status_value in {"failed", "canceled", "rejected", "error"}
                else None
            ),
        )

    async def notify_task_update(
        self,
        message_id: str,
        state: Any,
        room_id: str,
        user_id: str,
        error: str | None = None,
        parts: list[dict] | None = None,
        emit_processing_status: bool = True,
    ) -> bool:
        """Handler-owned task notification — delegates to shared impl.

        Preferred over the standalone ``notify_task_update`` function
        because it uses injected services instead of global singletons.
        """
        if self._task_notifier is None or self._task_notification_impl is None:
            raise RuntimeError(
                "Task notification runtime dependencies have not been bound"
            )
        if self._task_notification_store is None:
            raise RuntimeError("Task notification store dependency has not been bound")

        return await self._task_notification_impl(
            self._task_notification_store,
            self._task_notifier,
            self._delivery,
            message_id=message_id,
            state=state,
            room_id=room_id,
            user_id=user_id,
            error=error,
            parts=parts,
            emit_processing_status=emit_processing_status,
            processing_status_emitter=self._processing_status_emitter,
        )

    async def _notify(
        self,
        e: AgentEvent,
        state: Any,
        error: str | None = None,
        emit_processing_status: bool = True,
    ) -> None:
        await self.notify_task_update(
            message_id=e.message_id,
            state=state,
            room_id=e.room_id,
            user_id=e.user_id or "",
            error=error,
            parts=e.parts,
            emit_processing_status=emit_processing_status,
        )

    async def _notify_terminal_best_effort(
        self,
        e: AgentEvent,
        state: Any,
        *,
        error: str | None = None,
        emit_processing_status: bool = True,
    ) -> None:
        try:
            if emit_processing_status:
                await self._notify(e, state, error=error)
            else:
                await self.notify_task_update(
                    message_id=e.message_id,
                    state=state,
                    room_id=e.room_id,
                    user_id=e.user_id or "",
                    error=error,
                    parts=e.parts,
                    emit_processing_status=False,
                )
        except Exception:
            logger.warning(
                "AgentResponseHandler: terminal task notification failed for %s",
                e.message_id,
                exc_info=True,
            )

    @staticmethod
    def _orchestration_result_artifacts(
        e: AgentEvent,
        *,
        persisted_artifacts: list[dict] | None = None,
    ) -> list[dict]:
        if e.artifacts:
            return e.artifacts
        if e.parts:
            non_text = [p for p in e.parts if p.get("kind") in ("file", "data")]
            if non_text:
                return [{"name": "agent-output", "parts": non_text}]
        return persisted_artifacts or []

    @staticmethod
    def _terminal_result_error(e: AgentEvent, status: str) -> str | None:
        if status not in {"failed", "canceled", "rejected", "error", "rate_limited"}:
            return None
        return _safe_terminal_error(status)

    async def _ingest_orchestration_result(
        self,
        e: AgentEvent,
        *,
        status: str,
        text: str | None = None,
        artifacts: list[dict] | None = None,
        error: str | None = None,
    ) -> None:
        service = _orchestration_result_ingestor
        if service is None:
            return

        try:
            result = AgentResultRead(
                agent_message_id=e.message_id,
                agent_id=e.agent_id,
                status=status,
                text=text,
                artifacts=artifacts or [],
                error=error,
            )
            maybe_result = service.ingest_agent_result(result)
            if inspect.isawaitable(maybe_result):
                await maybe_result
        except Exception:
            logger.warning(
                "AgentResponseHandler: orchestration result ingestion failed for %s",
                e.message_id,
                exc_info=True,
            )

    async def _resume_orchestration(
        self,
        message_id: str,
        response_text: str,
        *,
        failed: bool = False,
    ) -> None:
        try:
            await self._rmc.resume_queue_from_continuation(
                message_id=message_id,
                task_result_text=response_text if not failed else None,
                failed=failed,
            )
        except Exception:
            logger.exception("Failed to resume orchestration for %s", message_id)
