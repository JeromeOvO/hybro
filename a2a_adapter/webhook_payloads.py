from __future__ import annotations

import re
import uuid
from typing import Any

from a2a.types import (
    Artifact,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
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
    """Normalize protobuf camelCase payloads for SDK Pydantic models."""

    def _to_snake(name: str) -> str:
        s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    def _convert(obj: Any, *, parent_key: str = "") -> Any:
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                new_key = _to_snake(k)
                new_val = _convert(v, parent_key=new_key)
                if (
                    new_key == "state"
                    and isinstance(new_val, str)
                    and new_val in _PROTO_STATE_MAP
                ):
                    new_val = _PROTO_STATE_MAP[new_val]
                result[new_key] = new_val
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
    """Detect protobuf-style camelCase A2A payloads."""
    if "contextId" in data or "artifactId" in data:
        return True
    status = data.get("status", {})
    if isinstance(status, dict):
        state_val = status.get("state", "")
        if isinstance(state_val, str) and state_val.startswith("TASK_STATE_"):
            return True
    return False


def _extract_text_from_message_dict(message: Any) -> str:
    """Best-effort text extraction from a (possibly proto-shaped) message dict.

    Handles both v0.x (``parts``) and v1.x proto (``content``) part lists and
    ignores role/kind discriminator differences.
    """
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts") or message.get("content") or []
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
    return "".join(texts)


def _task_from_status_update_dict(raw: dict[str, Any], message_id: str) -> Task:
    """Build a Task from a status-update payload that failed strict validation.

    v1.x (proto/gRPC) agents send push notifications whose ``statusUpdate``
    envelope (notably an embedded agent ``message``) does not validate against
    the v0.x Pydantic ``TaskStatusUpdateEvent`` model. Extract the state and
    any response text defensively so the terminal ``completed`` signal is not
    lost (which would otherwise stall the task until the stale-task poller).
    """
    raw = raw if isinstance(raw, dict) else {}
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}

    state_value = status.get("state") or "working"
    if isinstance(state_value, str):
        state_value = _PROTO_STATE_MAP.get(state_value, state_value)
    try:
        state = TaskState(state_value)
    except ValueError:
        state = TaskState.working

    task_id = raw.get("task_id") or raw.get("taskId") or message_id
    context_id = raw.get("context_id") or raw.get("contextId") or ""

    text = _extract_text_from_message_dict(status.get("message"))
    artifacts = None
    if text:
        artifacts = [
            Artifact(
                artifact_id=str(uuid.uuid4()),
                name="response",
                parts=[Part(root=TextPart(text=text))],
            )
        ]

    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state),
        artifacts=artifacts,
    )


def parse_stream_response_payload(payload: dict[str, Any], message_id: str) -> Task:
    """Parse A2A StreamResponse variants into an SDK Task."""
    if "task" in payload:
        task_data = payload["task"]
        if _is_proto_format(task_data):
            task_data = _normalize_proto_payload(task_data)
        return Task.model_validate(task_data)

    if "statusUpdate" in payload or "status_update" in payload:
        raw = payload.get("statusUpdate") or payload.get("status_update")
        if _is_proto_format(raw):
            raw = _normalize_proto_payload(raw)
        try:
            status_event = TaskStatusUpdateEvent.model_validate(raw)
        except ValueError:
            # pydantic ValidationError subclasses ValueError. v1.x (proto/gRPC)
            # agents emit status updates whose embedded
            # agent ``message`` (``ROLE_AGENT`` role, ``content`` parts, no
            # ``kind`` discriminator) does not validate against the v0.x
            # Pydantic model. Rather than reject the (often terminal) signal
            # with HTTP 400, rebuild the Task from the fields we need.
            return _task_from_status_update_dict(raw, message_id)
        return Task(
            id=status_event.task_id,
            context_id=status_event.context_id,
            status=status_event.status,
        )

    if "message" in payload:
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

    if "id" in payload and "status" in payload:
        if _is_proto_format(payload):
            payload = _normalize_proto_payload(payload)
        return Task.model_validate(payload)

    raise ValueError(
        "Invalid StreamResponse: expected 'task', 'statusUpdate', 'message', "
        "or 'artifactUpdate' key"
    )


__all__ = [
    "_is_proto_format",
    "_normalize_proto_payload",
    "parse_stream_response_payload",
]
