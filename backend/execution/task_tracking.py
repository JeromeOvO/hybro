from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from a2a_adapter.translators import facade_result_to_model, message_to_completed_task
from common.a2a_constants import (
    INTERACTIVE_STATES,
    NON_TERMINAL_STATES,
    SyntheticTaskId,
    is_terminal_state,
)
from common.a2a_task_projection import (
    public_artifact_data,
    public_message_data,
    public_part_data,
    public_persisted_task_data,
)
from common.types import Message, Part, Task, TaskState, TaskStatus, TextPart
from common.types import MessageRole as Role
from common.utils.a2a_helpers import (
    extract_parts_from_artifacts,
    materialize_artifacts,
)
from common.utils.logger import get_logger
from common.utils.time import utcnow
from models.error import A2AServiceError

logger = get_logger(__name__)
RecordCall = Callable[[str | None], Awaitable[None]]
SendMessageCall = Callable[..., Awaitable[dict[str, Any]]]
SendHitlReplyCall = Callable[..., Awaitable[dict[str, Any]]]

_PUBLIC_SAFE_STATUS_TEXT = {
    "failed": "Task failed",
    "rejected": "Task was rejected by the agent",
    "canceled": "Task was canceled",
    "expired": "Task expired",
}
_COMPLETED_STATE = "completed"

if TYPE_CHECKING:
    from execution.ports import A2ATaskTrackingStorePort


class A2ATaskTrackingService:
    def __init__(self, tracking_store: A2ATaskTrackingStorePort) -> None:
        self._tracking_store = tracking_store

    async def create_task_for_tracking(
        self,
        current_message,
        agent_card,
        message,
        *,
        step_number: int | None = None,
        total_steps: int | None = None,
    ) -> dict[str, Any]:
        room_id = current_message.room_id
        user_id = current_message.user_id or "unknown"
        message_id = current_message.message_id

        non_terminal_state_values = [state.value for state in NON_TERMINAL_STATES]
        try:
            await self._tracking_store.check_task_limits(
                user_id,
                room_id,
                non_terminal_state_values,
            )
        except ValueError as exc:
            raise A2AServiceError(str(exc)) from exc

        if not message_id:
            raise A2AServiceError("message_id is required for task tracking")

        webhook_token = self._tracking_store.generate_webhook_token()
        webhook_token_hash = self._tracking_store.hash_webhook_token(webhook_token)
        context_id = message.context_id or str(uuid4())
        placeholder_task = Task(
            id=f"pending-{context_id}",
            context_id=context_id,
            status=TaskStatus(state=TaskState.submitted),
        )
        now = utcnow()

        update_success = await self._tracking_store.enable_task_tracking_on_message(
            message_id=message_id,
            webhook_token_hash=webhook_token_hash,
            agent_url=agent_card.url,
            task_created_at=now,
            task_updated_at=now,
            task_data=placeholder_task.model_dump(mode="json"),
        )
        if not update_success:
            raise A2AServiceError(
                f"Failed to persist task tracking for message {message_id}. "
                "The message document may not exist."
            )

        if current_message.message_content:
            current_message.message_content.message_task = placeholder_task
        current_message.has_task_tracking = True

        return {
            "message_id": message_id,
            "webhook_token": webhook_token,
            "context_id": context_id,
            "created_at": now.isoformat(),
            "step_number": step_number,
            "total_steps": total_steps,
        }

    async def send_message_to_tracked_agent(
        self,
        *,
        agent_card,
        message: Message,
        message_id: str,
        webhook_token: str,
        context_id: str,
        room_id: str | None,
        agent_id: str | None,
        webhook_base_url: str,
        push_notification_timeout: float,
        default_request_timeout: float,
        accepted_output_modes: Sequence[str] | None,
        record_success: RecordCall,
        record_failure: RecordCall,
        send_message: SendMessageCall,
    ) -> dict[str, Any]:
        push_config = _build_push_config(
            agent_card=agent_card,
            message_id=message_id,
            webhook_token=webhook_token,
            webhook_base_url=webhook_base_url,
        )
        use_blocking = push_config is None

        try:
            response = await send_message(
                agent_card,
                message,
                accepted_output_modes=accepted_output_modes,
                push_notification_config=push_config,
                blocking=use_blocking,
                timeout=(
                    push_notification_timeout
                    if push_config
                    else default_request_timeout
                ),
            )
        except Exception as exc:
            await record_failure(agent_id)
            await self._persist_failed_task(
                message_id,
                context_id,
                f"Failed to contact agent: {exc}",
            )
            logger.error("Failed to send message to agent: %s", exc)
            raise A2AServiceError(str(exc)) from exc

        if response.get("kind") == "error":
            await record_failure(agent_id)
            error_payload = response.get("error")
            error_msg = (
                error_payload.get("message")
                if isinstance(error_payload, dict)
                else str(error_payload)
            )
            await self._persist_failed_task(
                message_id,
                context_id,
                f"Agent error: {error_msg}",
            )
            raise A2AServiceError(error_msg)

        await record_success(agent_id)
        try:
            result = facade_result_to_model(response)
        except ValueError as exc:
            raise A2AServiceError(str(exc)) from exc

        if result.kind == "message":
            return await self._handle_message_result(
                result,
                context_id=context_id,
                message_id=message_id,
                room_id=room_id,
            )

        if result.kind == "task":
            return await self._handle_task_result(
                result,
                message_id=message_id,
                room_id=room_id,
                agent_name=agent_card.name,
            )

        raise A2AServiceError(f"Unexpected response kind: {result.kind}")

    async def reply_to_task(
        self,
        *,
        message_id: str,
        task_id: str,
        context_id: str,
        user_input: str,
        webhook_base_url: str,
        push_notification_timeout: float,
        default_request_timeout: float,
        send_hitl_reply: SendHitlReplyCall,
    ) -> dict[str, Any]:
        msg = await self._tracking_store.get_room_agent_message_by_message_id(
            message_id
        )
        if not msg:
            raise ValueError(f"Agent message {message_id} not found")

        agent_url = msg.agent_url
        agent_card = None
        if msg.agent_id and (webhook_base_url or not agent_url):
            agent_record = await self._tracking_store.get_agent_by_agent_id(
                msg.agent_id
            )
            agent_card = getattr(agent_record, "agent_card", None)
            if agent_card is None and webhook_base_url:
                logger.warning(
                    "hitl: could not load agent card for agent %s - disabling push notifications",
                    msg.agent_id,
                )

        if not agent_url:
            agent_url = _agent_card_url(agent_card)
            if agent_url:
                logger.warning(
                    "hitl: agent message %s had no agent_url; using agent card URL",
                    message_id,
                )
        if not agent_url:
            raise ValueError(f"Agent message {message_id} has no agent_url")

        webhook_token = self._tracking_store.generate_webhook_token()
        webhook_token_hash = self._tracking_store.hash_webhook_token(webhook_token)
        token_updated = await self._tracking_store.update_webhook_token_hash_on_message(
            message_id,
            webhook_token_hash,
        )
        if not token_updated:
            raise RuntimeError(
                f"Failed to rotate webhook token for message {message_id} - "
                "agent callback would fail verification; aborting reply"
            )

        push_config = _build_push_config(
            agent_card=agent_card,
            message_id=message_id,
            webhook_token=webhook_token,
            webhook_base_url=webhook_base_url,
        )
        hitl_blocking = push_config is None
        hitl_timeout = (
            default_request_timeout if hitl_blocking else push_notification_timeout
        )

        response = await send_hitl_reply(
            agent_url,
            _build_hitl_reply_message(
                task_id=task_id,
                context_id=context_id,
                user_input=user_input,
            ),
            agent_id=msg.agent_id,
            push_notification_config=push_config,
            blocking=hitl_blocking,
            timeout=hitl_timeout,
        )

        task_result = (
            facade_result_to_model(response)
            if response.get("kind") != "error"
            else None
        )
        task_obj = task_result if getattr(task_result, "kind", None) == "task" else None
        response_text = _extract_reply_response_text(task_result)

        if task_obj:
            public_status_text = extract_public_completed_status_text(task_obj)
            existing_task = (
                msg.message_content.message_task if msg.message_content else None
            )
            trusted_local_hitl_metadata = await self._trusted_local_hitl_metadata(
                msg,
                existing_task,
                task_id=task_id,
                context_id=context_id,
            )
            projected_task_data = public_persisted_task_data(
                task_obj,
                trusted_local_hitl_metadata=trusted_local_hitl_metadata,
            )
            projected_response_text = _extract_reply_response_text(
                _task_model_for_internal_projection(projected_task_data)
            )
            response_text = projected_response_text or public_status_text
            await self._tracking_store.update_task_on_message(
                message_id,
                projected_task_data,
                message_text=public_status_text or projected_response_text,
            )

        logger.info(
            "hitl_reply_to_task_sent",
            extra={
                "message_id": message_id,
                "task_id": task_id,
                "context_id": context_id,
            },
        )

        task_state = None
        if task_obj and task_obj.status:
            task_state = _state_value(task_obj.status.state)

        return {
            "status": "sent",
            "blocking": hitl_blocking,
            "task_state": task_state,
            "response_text": response_text,
        }

    async def _trusted_local_hitl_metadata(
        self,
        msg,
        existing_task: Task | None,
        *,
        task_id: str,
        context_id: str,
    ) -> dict[str, Any] | None:
        metadata = existing_task.metadata if existing_task is not None else None
        request_id = (
            metadata.get("hitl_request_id") if isinstance(metadata, dict) else None
        )
        if not isinstance(request_id, str) or not request_id:
            return None

        getter = getattr(self._tracking_store, "get_hitl_request", None)
        if not callable(getter):
            return None

        try:
            request = getter(request_id)
            if inspect.isawaitable(request):
                request = await request
        except Exception:
            logger.warning(
                "Failed to verify local HITL metadata for agent message %s",
                msg.message_id,
                exc_info=True,
            )
            return None

        if not _is_trusted_local_hitl_request(
            request,
            request_id=request_id,
            room_id=msg.room_id,
            message_id=msg.message_id,
            agent_id=msg.agent_id,
            task_id=task_id,
            context_id=context_id,
        ):
            return None
        return _trusted_metadata_from_hitl_request(request)

    async def _handle_message_result(
        self,
        message: Message,
        *,
        context_id: str,
        message_id: str,
        room_id: str | None,
    ) -> dict[str, Any]:
        completed_task = message_to_completed_task(
            message,
            context_id,
            task_id=str(uuid4()),
            artifact_id=str(uuid4()),
        )
        if completed_task.artifacts:
            await _best_effort_materialize_artifacts(
                completed_task.artifacts,
                room_id=room_id or message_id,
                message_id=message_id,
                context="message",
            )

        projected_task_data = public_persisted_task_data(completed_task)
        projected_task = _task_model_for_internal_projection(projected_task_data)
        message_text = _extract_text_from_task(projected_task)
        persisted = await self._tracking_store.update_task_on_message(
            message_id,
            projected_task_data,
            message_text=message_text or None,
        )
        resp = {
            "type": "message",
            "message_id": message_id,
            "content": message_text,
            "persisted": persisted,
        }
        non_text_parts = _non_text_parts(projected_task.artifacts)
        if non_text_parts:
            resp["parts"] = non_text_parts
        return resp

    async def _handle_task_result(
        self,
        task: Task,
        *,
        message_id: str,
        room_id: str | None,
        agent_name: str,
    ) -> dict[str, Any]:
        state = task.status.state
        if is_terminal_state(state):
            return await self._handle_terminal_task_result(
                task,
                message_id=message_id,
                room_id=room_id,
            )

        if state in INTERACTIVE_STATES:
            return {
                "type": "task",
                "message_id": message_id,
                "task_id": task.id,
                "status": _state_value(state),
                "requires_input": state == TaskState.input_required,
                "requires_auth": state == TaskState.auth_required,
                "message": _extract_status_message(task),
            }

        return {
            "type": "task",
            "message_id": message_id,
            "task_id": task.id,
            "status": _state_value(state),
            "agent_name": agent_name,
        }

    async def _handle_terminal_task_result(
        self,
        task: Task,
        *,
        message_id: str,
        room_id: str | None,
    ) -> dict[str, Any]:
        if task.artifacts:
            await _best_effort_materialize_artifacts(
                task.artifacts,
                room_id=room_id or message_id,
                message_id=message_id,
                context="terminal_task",
            )

        public_status_text = extract_public_completed_status_text(task)
        projected_data = public_persisted_task_data(task)
        projected_task = _task_model_for_internal_projection(projected_data)
        state = projected_task.status.state
        artifact_text = _extract_text_from_task(projected_task)
        task_text = artifact_text or public_status_text
        persisted = await self._tracking_store.update_task_on_message(
            message_id,
            projected_data,
            message_text=public_status_text or artifact_text or None,
        )

        resp = {
            "type": "message",
            "message_id": message_id,
            "content": task_text,
            "status": _state_value(state),
            "persisted": persisted,
        }
        if public_status_text:
            resp["public_message_text"] = public_status_text
        non_text_parts = _non_text_parts(projected_task.artifacts)
        if non_text_parts:
            resp["parts"] = non_text_parts
        if state != TaskState.completed:
            error_text = _extract_status_message(projected_task)
            if error_text:
                resp["error"] = error_text
            elif not task_text:
                resp["error"] = f"Task {_state_value(state)}"
        return resp

    async def _persist_failed_task(
        self,
        message_id: str,
        context_id: str,
        text: str,
    ) -> None:
        logger.error(
            "failed_task_sanitized",
            extra={
                "message_id": message_id,
                "failure_detail_present": bool(text),
            },
        )
        failed_task = Task(
            id=SyntheticTaskId.FAILED,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.failed,
                message=Message(
                    role=Role.AGENT,
                    parts=[
                        Part(root=TextPart(text=_PUBLIC_SAFE_STATUS_TEXT["failed"]))
                    ],
                    message_id=str(uuid4()),
                ),
            ),
        )
        await self._tracking_store.update_task_on_message(
            message_id,
            public_persisted_task_data(failed_task),
        )


def _agent_card_url(agent_card: Any) -> str | None:
    if agent_card is None:
        return None
    if isinstance(agent_card, dict):
        raw_url = agent_card.get("url")
    else:
        raw_url = getattr(agent_card, "url", None)
    if raw_url is None:
        return None
    url = str(raw_url).strip()
    return url or None


def _is_trusted_local_hitl_request(
    request: Any,
    *,
    request_id: str,
    room_id: str,
    message_id: str,
    agent_id: str | None,
    task_id: str,
    context_id: str,
) -> bool:
    if not isinstance(request, dict):
        return False
    if (
        request.get("request_id") != request_id
        or request.get("room_id") != room_id
        or request.get("source") != "agent"
    ):
        return False
    projected_message_id = request.get("display_message_id") or request.get(
        "continuation_message_id"
    )
    if projected_message_id != message_id:
        return False
    request_agent_id = request.get("agent_id")
    if request_agent_id is not None and request_agent_id != agent_id:
        return False
    request_task_id = request.get("a2a_task_id")
    if request_task_id is not None and request_task_id != task_id:
        return False
    request_context_id = request.get("a2a_context_id")
    if request_context_id is not None and request_context_id != context_id:
        return False
    return True


def _trusted_metadata_from_hitl_request(request: dict[str, Any]) -> dict[str, Any]:
    request_id = request["request_id"]
    trusted: dict[str, Any] = {
        "hitl_request_id": request_id,
        "hitl_prompt": request.get("prompt"),
        "hitl_prompt_type": getattr(
            request.get("prompt_type"), "value", request.get("prompt_type")
        ),
    }
    optional_fields = {
        "hitl_choices": request.get("choices"),
        "hitl_a2a_task_id": request.get("a2a_task_id"),
        "hitl_a2a_context_id": request.get("a2a_context_id"),
        "hitl_group_id": request.get("group_id"),
        "hitl_group_total": request.get("group_total"),
        "hitl_group_index": request.get("group_index"),
        "user_answer": request.get("user_input"),
    }
    trusted.update(
        {key: value for key, value in optional_fields.items() if value is not None}
    )
    return {key: value for key, value in trusted.items() if value is not None}


async def _best_effort_materialize_artifacts(
    artifacts: list,
    *,
    room_id: str,
    message_id: str,
    context: str,
) -> None:
    try:
        await materialize_artifacts(
            artifacts,
            room_id=room_id,
            message_id=message_id,
        )
    except Exception:
        logger.warning(
            "A2A artifact conversion failed for %s response on message %s; "
            "continuing with task persistence",
            context,
            message_id,
            exc_info=True,
        )


def _build_push_config(
    *,
    agent_card,
    message_id: str,
    webhook_token: str,
    webhook_base_url: str,
) -> dict[str, str] | None:
    if not _has_push_notification_capability(agent_card) or not webhook_base_url:
        return None
    return {
        "id": message_id,
        "url": f"{webhook_base_url}/api/v1/webhooks/a2a/{message_id}",
        "token": webhook_token,
    }


def _build_hitl_reply_message(
    *,
    task_id: str,
    context_id: str,
    user_input: str,
) -> dict[str, Any]:
    return {
        "kind": "message",
        "role": "user",
        "parts": [{"kind": "text", "text": user_input}],
        "messageId": str(uuid4()),
        "taskId": task_id,
        "contextId": context_id,
        "referenceTaskIds": [task_id],
    }


def _has_push_notification_capability(agent_card) -> bool:
    if agent_card is None:
        return False
    has_caps = agent_card.capabilities is not None
    if not has_caps:
        return False
    push_val = getattr(agent_card.capabilities, "push_notifications", None)
    if push_val is None:
        push_val = getattr(agent_card.capabilities, "pushNotifications", False)
    return bool(push_val)


def _extract_text_from_message(message: Message) -> str:
    texts = []
    for part in message.parts or []:
        if hasattr(part, "text") and part.text:
            texts.append(part.text)
        elif hasattr(part, "root") and hasattr(part.root, "text"):
            texts.append(part.root.text)
    return "".join(texts)


def _extract_text_from_task(task: Task) -> str | None:
    if not task.artifacts:
        return None
    texts = []
    for artifact in task.artifacts:
        for part in artifact.parts or []:
            if hasattr(part, "text") and part.text:
                texts.append(part.text)
            elif hasattr(part, "root") and hasattr(part.root, "text"):
                texts.append(part.root.text)
    return "".join(texts) if texts else None


def _task_model_for_internal_projection(task_data: dict[str, Any]) -> Task:
    try:
        return Task.model_validate(task_data)
    except ValueError:
        return Task.model_validate(_drop_unaddressable_public_file_parts(task_data))


def _drop_unaddressable_public_file_parts(task_data: dict[str, Any]) -> dict[str, Any]:
    artifacts = task_data.get("artifacts")
    if not isinstance(artifacts, list):
        return task_data

    public_artifacts: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        parts = artifact.get("parts")
        if not isinstance(parts, list):
            continue

        public_parts = []
        for part in parts:
            if not isinstance(part, dict):
                public_parts.append(part)
                continue
            payload = part.get("root") if isinstance(part.get("root"), dict) else part
            file_payload = payload.get("file") if isinstance(payload, dict) else None
            if isinstance(file_payload, dict) and not file_payload.get("uri"):
                continue
            public_parts.append(part)

        public_artifact = dict(artifact)
        public_artifact["parts"] = public_parts
        public_artifacts.append(public_artifact)

    sanitized = dict(task_data)
    sanitized["artifacts"] = public_artifacts or None
    return sanitized


def _extract_status_message(task: Task) -> str | None:
    if task.status.message and task.status.message.parts:
        for part in task.status.message.parts:
            if hasattr(part, "text") and part.text:
                return part.text
            if hasattr(part, "root") and hasattr(part.root, "text"):
                return part.root.text
    return None


def extract_public_completed_status_text(task: Task) -> str | None:
    """Extract agent-authored public text from a completed A2A Task status."""
    if _state_value(task.status.state) != _COMPLETED_STATE:
        return None
    message = task.status.message
    if message is None or message.role != Role.AGENT:
        return None
    text = _extract_text_from_message(message).strip()
    return text or None


def _extract_reply_response_text(task_result) -> str | None:
    if getattr(task_result, "kind", None) == "task":
        task_text = _extract_text_from_task(task_result)
        if task_text:
            return task_text
        return _extract_status_message(task_result)
    if getattr(task_result, "kind", None) == "message":
        return _extract_text_from_message(task_result)
    return None


def resolve_public_task_label(extend_info: Any, agent_name: str) -> str:
    if isinstance(extend_info, dict):
        public_task_label = extend_info.get("public_task_label")
        if isinstance(public_task_label, str) and public_task_label.strip():
            return public_task_label.strip()
    return f"Requesting {agent_name}"


def resolve_public_agent_response_text(
    room_agent_message: Any,
    *,
    preferred_text: str | None = None,
    fallback_text: str | None = None,
) -> str | None:
    """Resolve the public agent-authored body without exposing dispatch seeds.

    Modern A2A responses may carry their human-readable text separately from a
    DataPart-only artifact.  Legacy rows can instead have the dispatched task
    copied into ``message_text``; those values are not agent responses and must
    not be rendered as one.
    """

    if isinstance(preferred_text, str) and preferred_text.strip():
        return preferred_text.strip()

    message_content = getattr(room_agent_message, "message_content", None)
    stored_message_text = getattr(message_content, "message_text", None)
    stored_message_text = (
        stored_message_text.strip()
        if isinstance(stored_message_text, str) and stored_message_text.strip()
        else None
    )

    extend_info = getattr(room_agent_message, "extend_info", None)
    public_label = resolve_public_task_label(
        extend_info,
        getattr(room_agent_message, "agent_id", None) or "agent",
    )
    public_dispatch_text = (
        extend_info.get("public_dispatch_text")
        if isinstance(extend_info, dict)
        else None
    )
    dispatch_seed_texts = {
        candidate.strip()
        for candidate in (
            getattr(room_agent_message, "task_content", None),
            public_label,
            public_dispatch_text,
        )
        if isinstance(candidate, str) and candidate.strip()
    }
    if stored_message_text and stored_message_text not in dispatch_seed_texts:
        return stored_message_text

    if isinstance(fallback_text, str) and fallback_text.strip():
        return fallback_text.strip()
    return None


def _state_value(state: TaskState) -> str:
    return state.value if hasattr(state, "value") else str(state)


def _non_text_parts(artifacts) -> list[dict] | None:
    extracted = extract_parts_from_artifacts(artifacts) if artifacts else None
    if extracted and extracted.has_non_text:
        return extracted.file_parts + extracted.data_parts
    return None


__all__ = [
    "A2ATaskTrackingService",
    "extract_public_completed_status_text",
    "public_artifact_data",
    "public_message_data",
    "public_part_data",
    "public_persisted_task_data",
    "resolve_public_agent_response_text",
    "resolve_public_task_label",
]
