"""WebhookTransport — push-notification transport for async A2A agents.

Unlike DirectTransport and RelayTransport, this is inbound-only:
the agent initiates the call, not the user.

Owns: webhook auth/token validation, StreamResponse parsing,
Task -> AgentEvent normalization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from a2a_adapter.webhook_payloads import (
    _is_proto_format,
    _normalize_proto_payload,
    parse_stream_response_payload,
)
from fastapi import HTTPException

from common.a2a_constants import (
    CommonTaskState,
    INTERACTIVE_STATES,
    is_failure_state,
    is_terminal_state,
    normalize_task_state_value,
)
from common.utils.a2a_helpers import (
    extract_error_message,
    extract_parts_from_artifacts,
)
from common.utils.logger import get_logger
from execution.dispatch.agent_event import AgentEvent
from execution.dispatch.transports.base import AgentTransport

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
        if not text and task.status and task.status.message:
            text = extract_error_message(task) or None

        state_value = normalize_task_state_value(state) or str(state)

        if state == CommonTaskState.CANCELED:
            return AgentEvent(
                kind="canceled",
                **base,
                text=text or "",
                state=state_value,
            )

        if is_failure_state(state):
            return AgentEvent(
                kind="error",
                **base,
                error_text=text or "Unknown agent error",
                state=state_value,
            )

        if state in INTERACTIVE_STATES:
            return AgentEvent(
                kind="interactive",
                **base,
                text=text or "",
                state=state_value,
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
                state=state_value,
                parts=parts,
                artifacts=serialized_artifacts,
            )

        return AgentEvent(
            kind="status_update",
            **base,
            text=text or "",
            state=state_value,
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
