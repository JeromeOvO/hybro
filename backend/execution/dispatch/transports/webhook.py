"""WebhookTransport — push-notification transport for async A2A agents.

Unlike DirectTransport and RelayTransport, this is inbound-only:
the agent initiates the call, not the user.

Owns: webhook auth/token validation, StreamResponse parsing,
Task -> AgentEvent normalization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import HTTPException

from a2a_adapter.webhook_payloads import (
    _is_proto_format,  # noqa: F401 - compatibility re-export
    _normalize_proto_payload,  # noqa: F401 - compatibility re-export
    parse_stream_response_payload,
)
from common.a2a_constants import (
    INTERACTIVE_STATES,
    CommonTaskState,
    is_failure_state,
    is_terminal_state,
    normalize_task_state_value,
)
from common.protocols import JsonMap, JsonValue  # noqa: F401
from common.types import Task
from common.utils.a2a_helpers import (
    extract_parts_from_artifacts,
)
from common.utils.logger import get_logger
from execution.dispatch.a2a_interaction import input_observation_from_a2a
from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.transports.base import AgentTransport
from execution.task_tracking import extract_public_completed_status_text

if TYPE_CHECKING:
    from execution.dispatch.dispatch_middleware import DispatchContext
    from execution.dispatch.response_handler import AgentResponseHandler
    from models.room import RoomAgentMessage

    class WebhookAuthPort(Protocol):
        async def verify_webhook_token_for_task(
            self, message_id: str, token: str
        ) -> tuple[bool, str | None]: ...

    class WebhookMessageReader(Protocol):
        async def get_room_agent_message_by_message_id(self, message_id: str): ...

    class WebhookCancellationReader(Protocol):
        async def is_message_cancelled(self, message_id: str) -> bool: ...


logger = get_logger(__name__)

_PUBLIC_TERMINAL_ERRORS = {
    "failed": "Task failed",
    "error": "Task failed",
    "rate_limited": "Task failed",
    "rejected": "Task was rejected by the agent",
    "canceled": "Task was canceled",
    "expired": "Task expired",
}


def _safe_terminal_error(state: Any) -> str:
    return _PUBLIC_TERMINAL_ERRORS.get(str(state), "Task failed")


def _jsonrpc_update_id(payload: JsonMap) -> str:
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    for chunk in encoder.iterencode({"id": payload["id"], "payload": payload}):
        digest.update(chunk.encode())
    return f"jsonrpc:v1:{digest.hexdigest()}"


class WebhookTransport(AgentTransport):
    """Push-notification transport for async A2A agents (inbound-only)."""

    def __init__(
        self,
        response_handler: AgentResponseHandler,
        webhook_auth: WebhookAuthPort,
        message_reader: WebhookMessageReader,
        cancellation_reader: WebhookCancellationReader,
        task_notifier=None,
        terminal_task_fetcher: Callable[[str, str], Awaitable[Any | None]]
        | None = None,
    ) -> None:
        super().__init__(response_handler)
        self._webhook_auth = webhook_auth
        self._message_reader = message_reader
        self._cancellation_reader = cancellation_reader
        self._task_notifier = task_notifier
        self._terminal_task_fetcher = terminal_task_fetcher

    async def dispatch(
        self,
        ctx: DispatchContext,
        message: RoomAgentMessage,
    ) -> Any:
        raise NotImplementedError("Webhooks are inbound-only")

    async def authenticate_webhook(self, message_id: str, token: str) -> None:
        """Reject unauthorized webhook requests before their body is read."""
        if not token:
            logger.warning(
                "Webhook for task %s: Missing authorization token", message_id
            )
            raise HTTPException(status_code=401, detail="Missing authorization token")

        is_valid, error_reason = await self._webhook_auth.verify_webhook_token_for_task(
            message_id, token
        )
        if not is_valid:
            if error_reason == "task_not_found":
                logger.warning(
                    "Webhook for task %s: Task not found (may be race condition)",
                    message_id,
                )
                raise HTTPException(
                    status_code=404,
                    detail="Task not found. The task may not have been created yet.",
                )
            elif error_reason == "invalid_token":
                logger.warning("Webhook for task %s: Invalid token", message_id)
                raise HTTPException(status_code=401, detail="Invalid token")
            else:
                logger.error(
                    "Webhook for task %s: Token verification error: %s",
                    message_id,
                    error_reason,
                )
                raise HTTPException(status_code=500, detail="Token verification failed")

    async def handle_webhook(
        self,
        message_id: str,
        payload: JsonMap,
        token: str,
    ) -> JsonMap:
        """Called by the FastAPI route. Validate, parse, delegate."""
        # Revalidate at the business boundary so non-HTTP callers cannot bypass auth.
        await self.authenticate_webhook(message_id, token)

        # 2. Parse StreamResponse
        updated_task = await asyncio.to_thread(
            parse_stream_response, payload, message_id
        )
        logger.info(
            "Webhook for task %s: Parsed task state=%s, artifacts=%d",
            message_id,
            updated_task.status.state,
            len(updated_task.artifacts) if updated_task.artifacts else 0,
        )

        # 3. Load current message, check idempotency
        current_msg = await self._message_reader.get_room_agent_message_by_message_id(
            message_id
        )
        if not current_msg or not current_msg.has_task_tracking:
            logger.warning("Webhook for unknown task %s", message_id)
            raise HTTPException(status_code=404, detail="Task not found")

        # 3a. Check if the message was cancelled while the agent was processing
        is_cancelled = await self._cancellation_reader.is_message_cancelled(message_id)
        if not is_cancelled and current_msg.related_message_id:
            is_cancelled = await self._cancellation_reader.is_message_cancelled(
                current_msg.related_message_id
            )
        if is_cancelled:
            logger.info(
                "Webhook for task %s: message was cancelled — discarding payload",
                message_id,
            )
            if self._task_notifier is not None:
                await self._task_notifier(
                    message_id=message_id,
                    state=CommonTaskState.CANCELED,
                    room_id=current_msg.room_id,
                    user_id=current_msg.user_id or "",
                )
            return {"status": "canceled"}

        current_task = (
            current_msg.message_content.message_task
            if current_msg.message_content
            else None
        )
        if current_task:
            current_state = current_task.status.state
            if is_terminal_state(current_state):
                logger.debug(
                    "Webhook for task %s: Already terminal (%s)",
                    message_id,
                    current_state,
                )
                return {
                    "status": "already_terminal",
                    "state": current_state.value
                    if hasattr(current_state, "value")
                    else str(current_state),
                }

        if (
            is_terminal_state(updated_task.status.state)
            and not updated_task.artifacts
            and self._terminal_task_fetcher is not None
            and current_msg.agent_url
            and current_task is not None
            and current_task.id
        ):
            fetched = await self._terminal_task_fetcher(
                current_msg.agent_url, current_task.id
            )
            if fetched is not None and is_terminal_state(fetched.status.state):
                updated_task = fetched

        # 4. Normalize Task -> AgentEvent and delegate
        is_artifact_update = _artifact_update_payload(payload)
        event = await asyncio.to_thread(
            self._artifact_update_event if is_artifact_update else self._task_to_event,
            *(
                (payload, updated_task, current_msg)
                if is_artifact_update
                else (updated_task, current_msg)
            ),
        )
        await self.response_handler.handle(event)

        return {"status": "accepted"}

    def _task_to_event(self, task: Any, msg: RoomAgentMessage) -> AgentEvent:
        """Convert A2A Task -> AgentEvent."""
        state = task.status.state
        base = dict(
            message_id=msg.message_id,
            room_id=msg.room_id,
            agent_id=msg.agent_id or "",
            related_message_id=msg.related_message_id,
            user_id=msg.user_id,
            client_request_id=msg.client_request_id,
            task_id=task.id if hasattr(task, "id") else None,
            context_id=task.context_id if hasattr(task, "context_id") else None,
        )

        text = None
        parts = None
        if task.artifacts:
            extracted = extract_parts_from_artifacts(task.artifacts)
            text = extracted.text if extracted.text else None
            parts = (
                (extracted.file_parts + extracted.data_parts)
                if extracted.has_non_text
                else None
            )

        state_value = normalize_task_state_value(state) or str(state)

        if state == CommonTaskState.CANCELED:
            return AgentEvent(
                kind="canceled",
                **base,
                text=_safe_terminal_error("canceled"),
                state=state_value,
            )

        if is_failure_state(state):
            return AgentEvent(
                kind="error",
                **base,
                error_text=_safe_terminal_error(state_value),
                state=state_value,
            )

        if state in INTERACTIVE_STATES:
            observation_source = (
                task
                if isinstance(task, Task)
                else Task.model_validate(task.model_dump())
            )
            return AgentEvent(
                kind="interactive",
                **base,
                text="",
                state=state_value,
                input_observation=input_observation_from_a2a(observation_source),
            )

        if is_terminal_state(state):
            # Serialize full artifacts for DB persistence so file parts survive refresh
            serialized_artifacts = None
            if task.artifacts:
                serialized_artifacts = [
                    a.model_dump(mode="json", exclude_none=True) for a in task.artifacts
                ]
            return AgentEvent(
                kind="response",
                **base,
                text=text or "",
                public_text=extract_public_completed_status_text(task),
                state=state_value,
                parts=parts,
                artifacts=serialized_artifacts,
            )

        return AgentEvent(
            kind="status_update",
            **base,
            text="",
            state=state_value,
        )

    def _artifact_update_event(
        self, payload: JsonMap, task: Any, msg: RoomAgentMessage
    ) -> AgentEvent:
        envelope = payload.get("result")
        source = envelope if isinstance(envelope, dict) else payload
        update = source.get("artifactUpdate") or source.get("artifact_update")
        if not isinstance(update, dict):
            update = payload
        update_metadata = update.get("metadata")
        if not isinstance(update_metadata, dict):
            update_metadata = {}
        stable_update_id = next(
            (
                str(value)
                for key in ("event_id", "eventId", "idempotency_key", "idempotencyKey")
                if (value := update_metadata.get(key)) is not None
            ),
            None,
        )
        if (
            stable_update_id is None
            and payload.get("jsonrpc") is not None
            and payload.get("id") is not None
        ):
            stable_update_id = _jsonrpc_update_id(payload)
        artifacts = [
            artifact.model_dump(mode="json", exclude_none=True)
            for artifact in (getattr(task, "artifacts", None) or [])
        ]
        return AgentEvent(
            kind="artifact_update",
            message_id=msg.message_id,
            room_id=msg.room_id,
            agent_id=msg.agent_id or "",
            related_message_id=msg.related_message_id,
            user_id=msg.user_id,
            client_request_id=msg.client_request_id,
            task_id=getattr(task, "id", None),
            context_id=getattr(task, "context_id", None),
            artifacts=artifacts,
            append=bool(update.get("append", False)),
            last_chunk=bool(update.get("lastChunk", update.get("last_chunk", False))),
            artifact_update_id=stable_update_id,
        )


def parse_stream_response(payload: dict[str, Any], message_id: str) -> Any:
    """Parse A2A StreamResponse format into a Task object.

    Per A2A spec section 4.3.3, StreamResponse is a discriminated union with:
    - task: Full Task object (preferred, includes artifacts)
    - statusUpdate: TaskStatusUpdateEvent (status only)
    - artifactUpdate: TaskArtifactUpdateEvent (streaming)
    - message: Message object

    Handles both SDK v0.x (Pydantic/snake_case) and v1.x (protobuf/camelCase) formats.
    """
    try:
        return parse_stream_response_payload(payload, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _artifact_update_payload(payload: JsonMap) -> bool:
    if "artifactUpdate" in payload or "artifact_update" in payload:
        return True
    result = payload.get("result")
    return isinstance(result, dict) and (
        "artifactUpdate" in result or "artifact_update" in result
    )
