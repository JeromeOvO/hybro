"""Canonical, idempotent task notification service.

``notify_task_update`` is the **standalone entry point** for background jobs
and safety-net paths.  ``AgentResponseHandler.notify_task_update`` is the
preferred entry point when a handler instance is available.

Both delegate to ``_notify_task_update_impl`` — the shared core that
performs idempotency checks, DB reads, artifact backfill, S3 conversion,
and SSE emission.

Idempotency is provided by ``db.update_last_notified_state``:
calling the function twice with the same state for the same message is a
safe no-op.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from common.a2a_constants import SSEProcessingStatus
from common.types import (
    Artifact,
    Part,
    Task,
    TaskState,
    TextPart,
)
from common.utils.a2a_helpers import (
    extract_error_message,
    extract_status_message,
    extract_text_from_artifacts,
    get_message_from_task,
    get_text_from_message,
)
from common.utils.logger import get_logger
from execution.task_tracking import (
    public_part_data,
    public_persisted_task_data,
    resolve_public_task_label,
)

if TYPE_CHECKING:
    from execution.ports import (
        ExecutionDeliveryPort,
        NotificationServicePort,
        TaskNotificationStorePort,
    )

logger = get_logger(__name__)

ProcessingStatusEmitter = Callable[..., Awaitable[dict[str, Any] | None]]
_processing_status_emitter: ProcessingStatusEmitter | None = None
_notification_store: TaskNotificationStorePort | None = None
_task_notifier = None
_delivery = None


def bind_notification_store(notification_store: TaskNotificationStorePort) -> None:
    global _notification_store

    _notification_store = notification_store


def bind_processing_status_emitter(
    processing_status_emitter: ProcessingStatusEmitter,
) -> None:
    global _processing_status_emitter

    _processing_status_emitter = processing_status_emitter


def bind_task_notification_runtime(
    *,
    task_notifier,
    delivery,
) -> None:
    globals()["_task_notifier"] = task_notifier
    globals()["_delivery"] = delivery


class TaskNotificationAdapter:
    def __init__(
        self,
        notify_task_update: Callable[..., Awaitable[bool]],
        *,
        state_converter: Callable[[str], Any] | None = None,
    ) -> None:
        self._notify_task_update = notify_task_update
        self._state_converter = state_converter or (lambda value: value)

    async def notify_task_update(
        self,
        *,
        message_id: str,
        state: str,
        room_id: str,
        user_id: str,
        error: str | None = None,
        parts: list[dict] | None = None,
    ) -> bool:
        return await self._notify_task_update(
            message_id=message_id,
            state=self._state_converter(state),
            room_id=room_id,
            user_id=user_id,
            error=error,
            parts=parts,
        )


def _map_task_state_to_processing_status(state: TaskState) -> SSEProcessingStatus | None:
    """Map TaskState updates to lifecycle processing_status values."""
    if state == TaskState.completed:
        return SSEProcessingStatus.COMPLETED
    if state == TaskState.failed:
        return SSEProcessingStatus.FAILED
    if state == TaskState.canceled:
        return SSEProcessingStatus.CANCELED
    if state == TaskState.rejected:
        return SSEProcessingStatus.REJECTED
    if state == TaskState.expired:
        return SSEProcessingStatus.FAILED
    if state in (
        TaskState.input_required,
        TaskState.auth_required,
        TaskState.policy_required,
    ):
        return SSEProcessingStatus.AWAITING_INPUT
    return None


# ---------------------------------------------------------------------------
# Shared implementation — called by both the handler method and the
# standalone wrapper below.
# ---------------------------------------------------------------------------


async def _notify_task_update_impl(
    notification_store: TaskNotificationStorePort,
    notification_svc: NotificationServicePort,
    delivery: ExecutionDeliveryPort,
    *,
    message_id: str,
    state: TaskState,
    room_id: str,
    user_id: str,
    error: str | None = None,
    parts: list[dict] | None = None,
    emit_processing_status: bool = False,
    processing_status_emitter: ProcessingStatusEmitter | None = None,
) -> bool:
    """Shared core: idempotency check, DB read, backfill, SSE emission.

    Both ``notify_task_update`` (standalone) and
    ``AgentResponseHandler.notify_task_update`` delegate here.
    """
    state_value = state.value if hasattr(state, "value") else str(state)

    logger.info(
        "notify_task_update called: message_id=%s state=%s room_id=%s",
        message_id,
        state_value,
        room_id,
    )

    # --- Idempotency check ------------------------------------------------
    # If update_last_notified_state fails (DB error), we proceed with the
    # notification anyway — a duplicate SSE is harmless whereas a missed SSE
    # causes a stuck bubble in the UI.
    try:
        is_new = await notification_store.update_last_notified_state(
            message_id, state_value
        )
    except Exception:
        logger.warning(
            "notify_task_update: update_last_notified_state failed for %s; "
            "proceeding with notification to avoid stuck bubble",
            message_id,
            exc_info=True,
        )
        is_new = True

    if not is_new:
        logger.debug(
            "Skipping duplicate notification for task %s state %s",
            message_id,
            state_value,
        )
        return False

    # --- Load the message from DB -----------------------------------------
    room_agent_message = None
    for attempt in range(3):
        room_agent_message = (
            await notification_store.get_room_agent_message_by_message_id(message_id)
        )
        if room_agent_message and room_agent_message.has_task_tracking:
            break
        if attempt < 2:
            await asyncio.sleep(0.5)

    if not room_agent_message or not room_agent_message.has_task_tracking:
        logger.debug(
            "notify_task_update: message %s has no task tracking; skipping",
            message_id,
        )
        return False

    task: Task | None = (
        room_agent_message.message_content.message_task
        if room_agent_message.message_content
        else None
    )
    if task is not None:
        task = Task.model_validate(public_persisted_task_data(task))
        room_agent_message.message_content.message_task = task

    # --- Diagnostic: log what we read from DB ---
    def _summarize_kinds(parts):
        """Summarize part kinds as counts instead of listing all."""
        if not parts:
            return "none"
        kinds = Counter(getattr(getattr(p, 'root', p), 'kind', '?') for p in parts)
        return ",".join(f"{k}:{v}" for k, v in kinds.items())

    if task:
        _art_count = len(task.artifacts) if task.artifacts else 0
        _task_state = task.status.state if task.status else "no-status"
        _parts_detail = ""
        if task.artifacts:
            _parts_detail = "; ".join(
                f"art[{i}]={len(a.parts) if a.parts else 0}p,kinds=[{_summarize_kinds(a.parts)}]"
                for i, a in enumerate(task.artifacts)
            )
        logger.info(
            "notify_task_update: DB task for %s: id=%s, db_state=%s, "
            "artifacts=%d (%s), has_message_text=%s",
            message_id,
            task.id[:30] if task.id else "None",
            _task_state,
            _art_count,
            _parts_detail or "none",
            bool(
                room_agent_message.message_content
                and room_agent_message.message_content.message_text
            ),
        )
    else:
        logger.warning(
            "notify_task_update: NO task in DB for %s (mc=%s)",
            message_id,
            bool(room_agent_message.message_content),
        )

    # --- Extract content / error / flags from the persisted task ----------
    content = None
    resolved_error = error
    requires_input = False
    requires_auth = False
    status_message = None

    if task:
        # Extract content from artifacts for any terminal state that has them
        if task.artifacts:
            from common.utils.a2a_helpers import extract_parts_from_artifacts

            extracted = extract_parts_from_artifacts(task.artifacts)
            content = extracted.text if extracted.text else None
            if parts is None and extracted.has_non_text:
                parts = extracted.file_parts + extracted.data_parts
            if not content:
                for i, artifact in enumerate(task.artifacts):
                    logger.warning(
                        "Artifact %d: parts=%d",
                        i,
                        len(artifact.parts) if artifact.parts else 0,
                    )
            logger.info(
                "notify_task_update: extraction result for %s: "
                "content=%s, text_parts=%d, file_parts=%d, data_parts=%d",
                message_id,
                repr(str(content)[:80]) if content else "None",
                len(extracted.text_parts),
                len(extracted.file_parts),
                len(extracted.data_parts),
            )
        elif state == TaskState.completed:
            logger.warning(
                "notify_task_update: completed but NO artifacts for %s "
                "(task.artifacts=%s)",
                message_id,
                type(task.artifacts).__name__ if task is not None else "no-task",
            )

        if state == TaskState.failed:
            raw_failure_detail = resolved_error or extract_error_message(task)
            if raw_failure_detail:
                logger.info(
                    "notify_task_update: raw failed detail retained internally for %s",
                    message_id,
                )
            resolved_error = "Task failed"

        elif state == TaskState.rejected:
            raw_rejection_detail = resolved_error or extract_error_message(task)
            if raw_rejection_detail:
                logger.info(
                    "notify_task_update: raw rejected detail retained internally for %s",
                    message_id,
                )
            resolved_error = "Task was rejected by the agent"

        elif state == TaskState.canceled:
            if not resolved_error:
                resolved_error = "Task was canceled"
            status_message = None

        elif state == TaskState.expired:
            raw_expired_detail = resolved_error or extract_error_message(task)
            if raw_expired_detail:
                logger.info(
                    "notify_task_update: raw expired detail retained internally for %s",
                    message_id,
                )
            resolved_error = "Task expired"

        elif state in (TaskState.input_required, TaskState.policy_required):
            requires_input = True
            status_message = None

        elif state == TaskState.auth_required:
            requires_auth = True
            status_message = (
                extract_status_message(task) or "Authentication required"
            )

    public_agent_text = content
    if (
        task is not None
        and not public_agent_text
        and state not in {TaskState.failed, TaskState.rejected}
    ):
        public_agent_text = get_text_from_message(get_message_from_task(task)) or None

    # --- Write-side: artifact backfill + message_text backfill ------------
    # Only write back to DB when a backfill actually modifies the message.
    # An unconditional full-document write here can overwrite real task data
    # (artifacts, id, context_id) saved by A2A transport partial $set if
    # the Pydantic round-trip (deserialize from DB → model_dump → $set)
    # loses fields from the a2a Task schema.
    needs_write = False
    if room_agent_message.message_content and task:
        if (
            state == TaskState.completed
            and public_agent_text
            and (not task.artifacts or len(task.artifacts) == 0)
        ):
            task = Task(
                id=task.id,
                context_id=task.context_id,
                status=task.status,
                history=task.history,
                metadata=task.metadata,
                artifacts=[
                    Artifact(
                        artifact_id=str(uuid.uuid4()),
                        name="response",
                        parts=[Part(root=TextPart(text=public_agent_text))],
                    )
                ],
            )
            room_agent_message.message_content.message_task = task
            content = extract_text_from_artifacts(task.artifacts)
            needs_write = True
            logger.info(
                "Task %s: Populated artifacts from public agent output",
                message_id,
            )

        if not room_agent_message.message_content.message_text:
            if content:
                room_agent_message.message_content.message_text = content
                needs_write = True
            elif resolved_error:
                room_agent_message.message_content.message_text = resolved_error
                needs_write = True
            elif status_message:
                room_agent_message.message_content.message_text = status_message
                needs_write = True

        if needs_write:
            update_ok = await notification_store.update_room_agent_message_by_message_id(
                room_agent_message.message_id, room_agent_message
            )
            if not update_ok:
                logger.error(
                    "Failed to update room agent message %s for task",
                    room_agent_message.message_id,
                )

    # --- Resolve agent_name from room's agent set -------------------------
    agent_name: str | None = None
    agent_id = room_agent_message.agent_id
    if agent_id:
        try:
            room = await notification_store.get_room_by_room_id(room_id)
            if room and room.room_agent_set:
                agent_name = room.room_agent_set.get(agent_id)
        except Exception:
            logger.warning(
                "notify_task_update: failed to resolve agent_name for %s",
                message_id,
                exc_info=True,
            )

    # --- Resolve remaining metadata from the message ----------------------
    created_at: str | None = None
    if room_agent_message.task_created_at:
        created_at = room_agent_message.task_created_at.isoformat()

    client_request_id = room_agent_message.client_request_id
    if not client_request_id:
        resolver = getattr(notification_store, "resolve_client_request_id_for_agent_message", None)
        if callable(resolver):
            resolved = resolver(room_agent_message)
            client_request_id = (
                await resolved if inspect.isawaitable(resolved) else resolved
            )
    if not isinstance(client_request_id, str) or not client_request_id:
        client_request_id = None

    # --- Send the SSE -----------------------------------------------------
    from common.utils.a2a_helpers import (
        filter_non_text_parts,
        is_terminal_task_state_value,
        resolve_terminal_sse_content,
    )

    if parts:
        parts = [public_part_data(part) for part in parts]

    if is_terminal_task_state_value(state):
        content = resolve_terminal_sse_content(
            state,
            message_text=public_agent_text,
            artifact_text=content,
        )
        # SSE text lives in ``content``; strip any text parts so ``parts`` is
        # file/data only and cannot drift from the resolved terminal body.
        parts = filter_non_text_parts(parts)

    task_content = resolve_public_task_label(
        room_agent_message.extend_info,
        agent_name or agent_id or "agent",
    )

    # Convert any inline base64 file bytes to S3 URIs before broadcasting
    if parts:
        from common.utils.a2a_helpers import convert_inline_bytes_to_s3

        await convert_inline_bytes_to_s3(parts, room_id, message_id)

    await notification_svc.send_task_update(
        room_id=room_id,
        message_id=message_id,
        status=state,
        content=content,
        error=resolved_error,
        requires_input=requires_input,
        requires_auth=requires_auth,
        status_message=status_message,
        agent_name=agent_name,
        agent_id=agent_id,
        related_message_id=room_agent_message.related_message_id,
        created_at=created_at,
        step_number=room_agent_message.step_number,
        total_steps=room_agent_message.total_steps,
        task_content=task_content,
        parts=parts,
        client_request_id=client_request_id,
    )

    processing_status = _map_task_state_to_processing_status(state)
    if emit_processing_status and processing_status is not None:
        status_details = None
        detail_text = resolved_error or status_message
        if detail_text:
            status_details = {"message": detail_text}
        emitter = processing_status_emitter or _processing_status_emitter
        if emitter is None:
            logger.warning(
                "notify_task_update: processing status emitter is not bound; "
                "skipping processing_status for %s",
                message_id,
            )
        else:
            status_value = processing_status.value
            error_message = (
                detail_text
                if detail_text
                and status_value
                in {
                    SSEProcessingStatus.FAILED.value,
                    SSEProcessingStatus.CANCELED.value,
                    SSEProcessingStatus.REJECTED.value,
                    SSEProcessingStatus.ERROR.value,
                }
                else None
            )
            await emitter(
                room_id=room_id,
                status=processing_status,
                message_id=message_id,
                lifecycle_message_id=message_id,
                record_lifecycle=True,
                client_request_id=client_request_id,
                details=status_details,
                error_message=error_message,
            )

    logger.info("Sent SSE notification for task %s state %s", message_id, state)

    return True


# ---------------------------------------------------------------------------
# Standalone wrapper — for background jobs and safety-net paths that lack
# a handler instance.
# ---------------------------------------------------------------------------


async def notify_task_update(
    message_id: str,
    state: TaskState,
    room_id: str,
    user_id: str,
    error: str | None = None,
    parts: list[dict] | None = None,
) -> bool:
    """Standalone entry point over explicitly bound runtime dependencies.

    Prefer ``AgentResponseHandler.notify_task_update`` when a handler
    instance is available.  This wrapper exists for background jobs
    (``stale_task_checker``) and safety-net paths (``RoomMessageCenter``)
    that have no handler context.
    """
    if _notification_store is None:
        raise RuntimeError("Task notification store dependency has not been bound")
    if _task_notifier is None or _delivery is None:
        raise RuntimeError("Task notification runtime dependencies have not been bound")
    return await _notify_task_update_impl(
        _notification_store,
        _task_notifier,
        _delivery,
        message_id=message_id,
        state=state,
        room_id=room_id,
        user_id=user_id,
        error=error,
        parts=parts,
        emit_processing_status=True,
    )


__all__ = [
    "TaskNotificationAdapter",
    "bind_notification_store",
    "bind_processing_status_emitter",
    "bind_task_notification_runtime",
    "_map_task_state_to_processing_status",
    "_notify_task_update_impl",
    "notify_task_update",
]
