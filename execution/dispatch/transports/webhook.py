"""WebhookTransport — push-notification transport for async A2A agents.

Unlike DirectTransport and RelayTransport, this is inbound-only:
the agent initiates the call, not the user.

Owns: webhook auth/token validation, StreamResponse parsing,
Task -> AgentEvent normalization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from a2a.types import (
    Artifact,
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from fastapi import HTTPException

from common.utils.a2a_helpers import (
    extract_error_message,
    extract_parts_from_artifacts,
)
from common.utils.logger import get_logger
from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.transports.base import AgentTransport
from common.a2a_constants import INTERACTIVE_STATES, is_failure_state, is_terminal_state

if TYPE_CHECKING:
    from execution.dispatch.response_handler import AgentResponseHandler
    from execution.dispatch.dispatch_middleware import DispatchContext
    from models.room import RoomAgentMessage

logger = get_logger(__name__)


class WebhookTransport(AgentTransport):
    """Push-notification transport for async A2A agents (inbound-only)."""

    def __init__(
        self,
        response_handler: AgentResponseHandler,
        db: DatabaseService,
        task_notifier=None,
    ) -> None:
        super().__init__(response_handler)
        self._db = db
        self._task_notifier = task_notifier

    async def dispatch(
        self,
        ctx: DispatchContext,
        message: RoomAgentMessage,
    ) -> Any:
        raise NotImplementedError("Webhooks are inbound-only")

    async def handle_webhook(
        self,
        message_id: str,
        payload: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        """Called by the FastAPI route. Validate, parse, delegate."""
        # 1. Validate webhook token (hash-based)
        if not token:
            logger.warning("Webhook for task %s: Missing authorization token", message_id)
            raise HTTPException(status_code=401, detail="Missing authorization token")

        is_valid, error_reason = await self._db.verify_webhook_token_for_task(
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
                    message_id, error_reason,
                )
                raise HTTPException(status_code=500, detail="Token verification failed")

        # 2. Parse StreamResponse
        updated_task = parse_stream_response(payload, message_id)
        logger.info(
            "Webhook for task %s: Parsed task state=%s, artifacts=%d",
            message_id,
            updated_task.status.state,
            len(updated_task.artifacts) if updated_task.artifacts else 0,
        )

        # 3. Load current message, check idempotency
        current_msg = await self._db.get_room_agent_message_by_message_id(message_id)
        if not current_msg or not current_msg.has_task_tracking:
            logger.warning("Webhook for unknown task %s", message_id)
            raise HTTPException(status_code=404, detail="Task not found")

        # 3a. Check if the message was cancelled while the agent was processing
        is_cancelled = await self._db.is_message_cancelled(message_id)
        if not is_cancelled and current_msg.related_message_id:
            is_cancelled = await self._db.is_message_cancelled(
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
                    state=TaskState.canceled,
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
                    "Webhook for task %s: Already terminal (%s)", message_id, current_state
                )
                return {
                    "status": "already_terminal",
                    "state": current_state.value
                    if hasattr(current_state, "value")
                    else str(current_state),
                }

        # 4. Normalize Task -> AgentEvent and delegate
        event = self._task_to_event(updated_task, current_msg)
        await self.response_handler.handle(event)

        return {"status": "accepted"}

    def _task_to_event(self, task: Task, msg: RoomAgentMessage) -> AgentEvent:
        """Convert A2A Task -> AgentEvent."""
        state = task.status.state
        base = dict(
            message_id=msg.message_id,
            room_id=msg.room_id,
            agent_id=msg.agent_id or "",
            related_message_id=msg.related_message_id,
            user_id=msg.user_id,
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
        if not text and task.status and task.status.message:
            text = extract_error_message(task) or None

        if state == TaskState.canceled:
            return AgentEvent(
                kind="canceled",
                **base,
                text=text or "",
                state=state.value,
            )

        if is_failure_state(state):
            return AgentEvent(
                kind="error",
                **base,
                error_text=text or "Unknown agent error",
                state=state.value,
            )

        if state in INTERACTIVE_STATES:
            return AgentEvent(
                kind="interactive",
                **base,
                text=text or "",
                state=state.value,
            )

        if is_terminal_state(state):
            # Serialize full artifacts for DB persistence so file parts survive refresh
            serialized_artifacts = None
            if task.artifacts:
                serialized_artifacts = [
                    a.model_dump(mode="json", exclude_none=True)
                    for a in task.artifacts
                ]
            return AgentEvent(
                kind="response",
                **base,
                text=text or "",
                state=state.value,
                parts=parts,
                artifacts=serialized_artifacts,
            )

        return AgentEvent(
            kind="status_update",
            **base,
            text=text or "",
            state=state.value,
        )


_PROTO_STATE_MAP: dict[str, str] = {
    "TASK_STATE_SUBMITTED": "submitted",
    "TASK_STATE_WORKING": "working",
    "TASK_STATE_INPUT_REQUIRED": "input-required",
    "TASK_STATE_AUTH_REQUIRED": "auth-required",
    "TASK_STATE_COMPLETED": "completed",
    "TASK_STATE_FAILED": "failed",
    "TASK_STATE_CANCELED": "canceled",
    "TASK_STATE_REJECTED": "rejected",
}


def _normalize_proto_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize protobuf-serialized camelCase payload to Pydantic snake_case.

    A2A SDK v1.x uses protobuf and serializes via MessageToDict which produces
    camelCase keys and TASK_STATE_* enum strings. Our Pydantic models (SDK v0.3.x)
    expect snake_case keys and short enum names like "completed".
    """
    import re

    def _to_snake(name: str) -> str:
        s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    def _convert(obj: Any, *, parent_key: str = "") -> Any:
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                new_key = _to_snake(k)
                new_val = _convert(v, parent_key=new_key)
                if new_key == "state" and isinstance(new_val, str) and new_val in _PROTO_STATE_MAP:
                    new_val = _PROTO_STATE_MAP[new_val]
                result[new_key] = new_val
            # Proto text parts lack `kind` — add it for Pydantic discriminator
            if "text" in result and "kind" not in result and parent_key == "parts":
                result["kind"] = "text"
            elif "file" in result and "kind" not in result and parent_key == "parts":
                result["kind"] = "file"
            elif "data" in result and "kind" not in result and parent_key == "parts":
                result["kind"] = "data"
            return result
        if isinstance(obj, list):
            return [_convert(item, parent_key=parent_key) for item in obj]
        return obj

    return _convert(data)


def _is_proto_format(data: dict[str, Any]) -> bool:
    """Detect if payload uses protobuf-style camelCase format."""
    if "contextId" in data or "artifactId" in data:
        return True
    status = data.get("status", {})
    if isinstance(status, dict):
        state_val = status.get("state", "")
        if isinstance(state_val, str) and state_val.startswith("TASK_STATE_"):
            return True
    return False


def parse_stream_response(payload: dict[str, Any], message_id: str) -> Task:
    """Parse A2A StreamResponse format into a Task object.

    Per A2A spec section 4.3.3, StreamResponse is a discriminated union with:
    - task: Full Task object (preferred, includes artifacts)
    - statusUpdate: TaskStatusUpdateEvent (status only)
    - artifactUpdate: TaskArtifactUpdateEvent (streaming)
    - message: Message object

    Handles both SDK v0.x (Pydantic/snake_case) and v1.x (protobuf/camelCase) formats.
    """
    if "task" in payload:
        logger.debug("Webhook for task %s: Received full Task in StreamResponse", message_id)
        task_data = payload["task"]
        if _is_proto_format(task_data):
            task_data = _normalize_proto_payload(task_data)
        return Task.model_validate(task_data)

    if "statusUpdate" in payload or "status_update" in payload:
        logger.debug("Webhook for task %s: Received statusUpdate in StreamResponse", message_id)
        raw = payload.get("statusUpdate") or payload.get("status_update")
        if _is_proto_format(raw):
            raw = _normalize_proto_payload(raw)
        status_event = TaskStatusUpdateEvent.model_validate(raw)
        return Task(
            id=status_event.task_id,
            context_id=status_event.context_id,
            status=status_event.status,
        )

    if "message" in payload:
        logger.debug("Webhook for task %s: Received message in StreamResponse", message_id)
        import uuid

        msg_data = payload["message"]
        if isinstance(msg_data, dict) and "contextId" in msg_data:
            msg_data = _normalize_proto_payload(msg_data)
        message = Message.model_validate(msg_data)
        return Task(
            id=str(uuid.uuid4()),
            context_id=message.context_id or "",
            status=TaskStatus(state=TaskState.completed),
            artifacts=[
                Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="response",
                    parts=message.parts,
                )
            ],
        )

    if "artifactUpdate" in payload or "artifact_update" in payload:
        logger.debug("Webhook for task %s: Received artifactUpdate in StreamResponse", message_id)
        raw = payload.get("artifactUpdate") or payload.get("artifact_update")
        if isinstance(raw, dict) and ("artifactId" in raw or "contextId" in raw):
            raw = _normalize_proto_payload(raw)
        artifact_event = TaskArtifactUpdateEvent.model_validate(raw)
        return Task(
            id=artifact_event.task_id,
            context_id=artifact_event.context_id,
            status=TaskStatus(state=TaskState.working),
            artifacts=[artifact_event.artifact],
        )

    # Fallback: raw Task (backwards compatibility)
    if "id" in payload and "status" in payload:
        logger.debug("Webhook for task %s: Received raw Task (not StreamResponse envelope)", message_id)
        if _is_proto_format(payload):
            payload = _normalize_proto_payload(payload)
        return Task.model_validate(payload)

    raise HTTPException(
        status_code=400,
        detail="Invalid StreamResponse: expected 'task', 'statusUpdate', 'message', or 'artifactUpdate' key",
    )
