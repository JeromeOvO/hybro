"""Public, persistence-safe projections for A2A task data.

This module is common-owned so HTTP routes and runtime modules can apply the
same privacy boundary without importing one another's implementations.
"""

from typing import Any
from uuid import uuid4

from common.types import Message, Part, Task, TextPart
from common.types import MessageRole as Role

_TRUSTED_LOCAL_HITL_METADATA_KEYS = frozenset(
    {
        "hitl_request_id",
        "hitl_prompt",
        "hitl_prompt_type",
        "hitl_choices",
        "hitl_a2a_task_id",
        "hitl_a2a_context_id",
        "hitl_group_id",
        "hitl_group_total",
        "hitl_group_index",
        "user_answer",
    }
)
_PUBLIC_SAFE_STATUS_TEXT = {
    "failed": "Task failed",
    "rejected": "Task was rejected by the agent",
    "canceled": "Task was canceled",
    "expired": "Task expired",
}
_PUBLIC_METADATA_KEYS = frozenset(
    {"file_id", "file_name", "mime_type", "size_bytes", "sha256"}
)
_COMPLETED_STATE = "completed"
_OUTPUT_DELIVERY_FAILURE_CODE = "artifact_delivery_failed"


def _plain_model_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value)


def _state_value_from_task_data(task_data: dict[str, Any]) -> str | None:
    status = task_data.get("status")
    if not isinstance(status, dict):
        return None
    raw_state = status.get("state")
    if raw_state is None:
        return None
    return raw_state.value if hasattr(raw_state, "value") else str(raw_state)


def _public_metadata_subset(metadata: Any) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    public = {
        key: value
        for key, value in metadata.items()
        if key in _PUBLIC_METADATA_KEYS and value is not None
    }
    return public or None


def public_part_data(part: Any) -> dict[str, Any] | None:
    part_data = _plain_model_data(part)
    root = part_data.get("root")
    if isinstance(root, dict):
        public_root = _public_part_payload(root)
        if public_root is None:
            return None
        part_data["root"] = public_root
        return part_data
    return _public_part_payload(part_data)


def _public_part_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    public_payload = dict(payload)
    public_metadata = _public_metadata_subset(public_payload.get("metadata"))
    public_payload["metadata"] = public_metadata

    file_payload = public_payload.get("file")
    if isinstance(file_payload, dict):
        if not public_metadata or not public_metadata.get("file_id"):
            return None
        # A2A FileContent is a transport type. Persisted/API projections keep
        # only the stable room-file reference and never retain bytes or URI.
        public_payload.pop("file", None)
    elif public_payload.get("kind") == "file" and (
        not public_metadata or not public_metadata.get("file_id")
    ):
        return None
    return public_payload


def public_message_data(message: Any) -> dict[str, Any] | None:
    if message is None:
        return None
    message_data = _plain_model_data(message)
    if message_data.get("role") != Role.AGENT.value:
        return None
    message_data["metadata"] = None
    message_data["parts"] = _public_parts_data(message_data.get("parts"))
    if not message_data["parts"]:
        return None
    return message_data


def public_artifact_data(artifact: Any) -> dict[str, Any]:
    artifact_data = _plain_model_data(artifact)
    artifact_data["metadata"] = None
    artifact_data["parts"] = _public_parts_data(artifact_data.get("parts"))
    return artifact_data


def _public_parts_data(parts: Any) -> list[dict[str, Any]]:
    public_parts = []
    for part in parts or []:
        public_part = public_part_data(part)
        if public_part is not None:
            public_parts.append(public_part)
    return public_parts


def _public_unavailable_artifacts(artifacts: list[Any]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for artifact in artifacts:
        public_artifact = public_artifact_data(artifact)
        unavailable_parts = []
        for part in public_artifact.get("parts") or []:
            root = part.get("root", part)
            data = root.get("data") if isinstance(root, dict) else None
            if isinstance(data, dict) and data.get("type") == "file_unavailable":
                unavailable_parts.append(part)
        if unavailable_parts:
            public_artifact["parts"] = unavailable_parts
            projected.append(public_artifact)
    return projected


def _public_status_message(text: str) -> dict[str, Any]:
    return Message(
        role=Role.AGENT,
        parts=[Part(root=TextPart(text=text))],
        message_id=str(uuid4()),
    ).model_dump(mode="json")


def public_persisted_task_data(
    task: Task,
    *,
    trusted_local_hitl_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_data = _plain_model_data(task)
    state_value = _state_value_from_task_data(task_data)

    artifacts = task_data.get("artifacts")
    metadata = task_data.get("metadata")
    output_delivery_failed = (
        state_value == "failed"
        and isinstance(metadata, dict)
        and metadata.get("output_failure_code") == _OUTPUT_DELIVERY_FAILURE_CODE
    )
    if state_value == _COMPLETED_STATE and isinstance(artifacts, list):
        task_data["artifacts"] = [
            public_artifact_data(artifact) for artifact in artifacts
        ]
    elif output_delivery_failed and isinstance(artifacts, list):
        task_data["artifacts"] = _public_unavailable_artifacts(artifacts) or None
    else:
        task_data["artifacts"] = None

    task_data["history"] = None

    status = task_data.get("status")
    if isinstance(status, dict) and isinstance(status.get("message"), dict):
        if state_value == _COMPLETED_STATE:
            status["message"] = None
        elif state_value in _PUBLIC_SAFE_STATUS_TEXT:
            status["message"] = _public_status_message(
                _PUBLIC_SAFE_STATUS_TEXT[state_value]
            )
        else:
            status["message"] = None
    elif isinstance(status, dict) and state_value in _PUBLIC_SAFE_STATUS_TEXT:
        status["message"] = _public_status_message(
            _PUBLIC_SAFE_STATUS_TEXT[state_value]
        )

    if isinstance(trusted_local_hitl_metadata, dict):
        trusted_metadata = {
            key: value
            for key, value in trusted_local_hitl_metadata.items()
            if key in _TRUSTED_LOCAL_HITL_METADATA_KEYS
        }
        task_data["metadata"] = trusted_metadata or None
    elif output_delivery_failed:
        task_data["metadata"] = {
            "output_failure_code": _OUTPUT_DELIVERY_FAILURE_CODE,
            "remote_task_state": _COMPLETED_STATE,
        }
    else:
        task_data["metadata"] = None
    return task_data


__all__ = [
    "public_artifact_data",
    "public_message_data",
    "public_part_data",
    "public_persisted_task_data",
]
