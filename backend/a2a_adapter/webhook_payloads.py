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


def _rebuild_status_message(
    raw_message: Any, *, fallback_message_id: str
) -> Message | None:
    """Best-effort Message rebuild that keeps interaction metadata.

    Strict ``TaskStatusUpdateEvent`` validation often fails when agents omit
    ``messageId`` or use proto role/content shapes. Dropping the message
    entirely also drops ``hybro.ai/a2a/interaction`` and turns typed HITL into
    an untyped completed tool result.
    """
    if not isinstance(raw_message, dict):
        return None
    text = _extract_text_from_message_dict(raw_message)
    metadata = raw_message.get("metadata")
    if not isinstance(metadata, dict):
        metadata = None
    message_id = (
        raw_message.get("messageId")
        or raw_message.get("message_id")
        or fallback_message_id
    )
    role = raw_message.get("role") or "agent"
    if isinstance(role, str) and role.upper().startswith("ROLE_"):
        role = role.split("_", 1)[-1].lower()
    if role not in {"agent", "user"}:
        role = "agent"
    if not text and metadata is None:
        return None
    try:
        return Message(
            message_id=str(message_id),
            role=role,
            parts=[Part(root=TextPart(text=text or ""))],
            metadata=metadata,
        )
    except (TypeError, ValueError):
        return None


def _task_from_status_update_dict(raw: dict[str, Any], message_id: str) -> Task:
    """Build a Task from a status-update payload that failed strict validation.

    v1.x (proto/gRPC) agents send push notifications whose ``statusUpdate``
    envelope (notably an embedded agent ``message``) does not validate against
    the v0.x Pydantic ``TaskStatusUpdateEvent`` model. Extract the state and
    any response text defensively so the terminal ``completed`` signal is not
    lost (which would otherwise stall the task until the stale-task poller).
    Preserve status.message metadata when present so typed HITL specs survive.
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

    status_message = _rebuild_status_message(
        status.get("message"),
        fallback_message_id=f"{task_id}-status",
    )

    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state, message=status_message),
        artifacts=artifacts,
    )


def _stream_kind(payload: dict[str, Any]) -> str | None:
    """Normalize StreamResponse ``kind`` (hyphen or underscore) when present."""
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        return None
    return kind.strip().replace("_", "-").lower()


def _status_update_raw(
    payload: dict[str, Any], *, kind: str | None
) -> dict[str, Any] | None:
    """Resolve a status-update event from kind-based or wrapped envelopes."""
    if kind == "status-update":
        return payload
    if "statusUpdate" in payload or "status_update" in payload:
        raw = payload.get("statusUpdate") or payload.get("status_update")
        return raw if isinstance(raw, dict) else None
    return None


def _artifact_update_raw(
    payload: dict[str, Any], *, kind: str | None
) -> dict[str, Any] | None:
    """Resolve an artifact-update event from kind-based or wrapped envelopes."""
    if kind == "artifact-update":
        return payload
    if "artifactUpdate" in payload or "artifact_update" in payload:
        raw = payload.get("artifactUpdate") or payload.get("artifact_update")
        return raw if isinstance(raw, dict) else None
    return None


def _task_from_status_update_raw(raw: dict[str, Any], message_id: str) -> Task:
    if _is_proto_format(raw):
        raw = _normalize_proto_payload(raw)
    try:
        status_event = TaskStatusUpdateEvent.model_validate(raw)
    except ValueError:
        # pydantic ValidationError subclasses ValueError. v1.x (proto/gRPC)
        # agents emit status updates whose embedded agent ``message``
        # (``ROLE_AGENT`` role, ``content`` parts, no ``kind`` discriminator)
        # does not validate against the current Pydantic model. Current JSON-RPC
        # SSE frames also omit ``messageId`` on embedded status messages.
        # Rebuild from the fields we need so terminal ``completed`` is not lost.
        return _task_from_status_update_dict(raw, message_id)
    return Task(
        id=status_event.task_id,
        context_id=status_event.context_id,
        status=status_event.status,
    )


def _task_from_artifact_update_raw(raw: dict[str, Any]) -> Task:
    if isinstance(raw, dict) and ("artifactId" in raw or "contextId" in raw):
        raw = _normalize_proto_payload(raw)
    artifact_event = TaskArtifactUpdateEvent.model_validate(raw)
    return Task(
        id=artifact_event.task_id,
        context_id=artifact_event.context_id,
        status=TaskStatus(state=TaskState.working),
        artifacts=[artifact_event.artifact],
    )


def parse_stream_response_payload(payload: dict[str, Any], message_id: str) -> Task:
    """Parse A2A StreamResponse variants into an SDK Task.

    Accepts both legacy wrapped envelopes (``statusUpdate`` / ``artifactUpdate``)
    and current JSON-RPC SSE frames where ``result`` is the event itself with
    ``kind`` of ``task``, ``status-update``, ``artifact-update``, or ``message``.
    """
    result = payload.get("result")
    if isinstance(result, dict):
        payload = result

    kind = _stream_kind(payload)

    if "task" in payload and kind != "task":
        task_data = payload["task"]
        if _is_proto_format(task_data):
            task_data = _normalize_proto_payload(task_data)
        return Task.model_validate(task_data)

    status_raw = _status_update_raw(payload, kind=kind)
    if status_raw is not None:
        return _task_from_status_update_raw(status_raw, message_id)

    if kind == "message" or "message" in payload:
        msg_data = payload if kind == "message" else payload["message"]
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

    artifact_raw = _artifact_update_raw(payload, kind=kind)
    if artifact_raw is not None:
        return _task_from_artifact_update_raw(artifact_raw)

    if kind == "task" or ("id" in payload and "status" in payload):
        if _is_proto_format(payload):
            payload = _normalize_proto_payload(payload)
        return Task.model_validate(payload)

    raise ValueError(
        "Invalid StreamResponse: expected 'task', 'statusUpdate', 'message', "
        "or 'artifactUpdate' key (or kind-based status-update/artifact-update)"
    )


__all__ = [
    "_is_proto_format",
    "_normalize_proto_payload",
    "parse_stream_response_payload",
]
