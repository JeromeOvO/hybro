"""Helpers for reporting and projecting A2A artifact delivery failures."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from common.types import DataPart, Message, Part, TaskState, TaskStatus, TextPart
from common.types import MessageRole as Role

OUTPUT_DELIVERY_FAILURE_CODE = "artifact_delivery_failed"
OUTPUT_DELIVERY_FAILURE_MESSAGE = "Agent output could not be processed."


def new_materialization_report() -> dict[str, Any]:
    """Return the mutable, payload-free report populated by artifact storage."""
    return {
        "attempted": 0,
        "stored": 0,
        "unavailable": 0,
        "failures": [],
    }


def record_materialization_failure(
    report: dict[str, Any] | None,
    *,
    code: str,
    artifact_ref: str,
    part_slot: int,
    source: str,
    exception_type: str,
) -> None:
    if report is None:
        return
    report["unavailable"] = int(report.get("unavailable", 0)) + 1
    failures = report.setdefault("failures", [])
    failures.append(
        {
            "code": code,
            "artifact_ref": artifact_ref,
            "part_slot": part_slot,
            "source": source,
            "exception_type": exception_type,
        }
    )


def record_materialization_attempt(report: dict[str, Any] | None) -> None:
    if report is not None:
        report["attempted"] = int(report.get("attempted", 0)) + 1


def record_materialization_success(report: dict[str, Any] | None) -> None:
    if report is not None:
        report["stored"] = int(report.get("stored", 0)) + 1


def _value(item: Any, *names: str) -> Any:
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return None
    for name in names:
        value = getattr(item, name, None)
        if value is not None:
            return value
    return None


def _part_root(part: Any) -> Any:
    return _value(part, "root") or part


def _is_meaningful_part(part: Any) -> bool:
    root = _part_root(part)
    kind = _value(root, "kind")
    if kind == "text":
        text = _value(root, "text")
        return isinstance(text, str) and bool(text.strip())
    if kind == "file":
        file_content = _value(root, "file")
        metadata = _value(root, "metadata") or {}
        return bool(_value(file_content, "uri") or _value(metadata, "file_id"))
    if kind == "data":
        data = _value(root, "data")
        return (
            isinstance(data, dict)
            and bool(data)
            and data.get("type") != "file_unavailable"
        )
    return False


def has_usable_artifact_output(artifacts: list[Any] | None) -> bool:
    return any(
        _is_meaningful_part(part)
        for artifact in artifacts or []
        for part in (_value(artifact, "parts") or [])
    )


def has_unavailable_artifact_output(artifacts: list[Any] | None) -> bool:
    for artifact in artifacts or []:
        for part in _value(artifact, "parts") or []:
            root = _part_root(part)
            data = _value(root, "data")
            if isinstance(data, dict) and data.get("type") == "file_unavailable":
                return True
    return False


def output_delivery_failed(
    artifacts: list[Any] | None,
    report: dict[str, Any] | None,
    *,
    text: str | None = None,
) -> bool:
    """Whether advertised files all failed and no other useful output survived."""
    if report is None:
        return False
    has_unavailable = int(
        report.get("unavailable", 0)
    ) > 0 or has_unavailable_artifact_output(artifacts)
    has_text = isinstance(text, str) and bool(text.strip())
    return (
        has_unavailable
        and int(report.get("stored", 0)) == 0
        and not has_text
        and not has_usable_artifact_output(artifacts)
    )


def mark_unresolved_file_parts_unavailable(
    artifacts: list[Any] | None,
    *,
    reason: str = "invalid_content",
) -> int:
    """Replace unresolved file parts after an exceptional materialization abort."""
    replaced = 0
    for artifact in artifacts or []:
        parts = _value(artifact, "parts") or []
        for index, part in enumerate(list(parts)):
            root = _part_root(part)
            if _value(root, "kind") != "file":
                continue
            file_content = _value(root, "file")
            payload = {
                "type": "file_unavailable",
                "file_name": _value(file_content, "name") or "file",
                "mime_type": (
                    _value(file_content, "mime_type", "mimeType")
                    or "application/octet-stream"
                ),
                "reason": reason,
            }
            if isinstance(part, dict):
                parts[index] = {"kind": "data", "data": payload}
            else:
                parts[index] = Part(root=DataPart(data=payload))
            replaced += 1
    return replaced


def mark_task_output_delivery_failed(task: Any) -> None:
    """Project a remote completed task as a safe local delivery failure."""
    task.status = TaskStatus(
        state=TaskState.failed,
        message=Message(
            role=Role.AGENT,
            parts=[Part(root=TextPart(text=OUTPUT_DELIVERY_FAILURE_MESSAGE))],
            message_id=str(uuid4()),
        ),
    )
    metadata = dict(getattr(task, "metadata", None) or {})
    metadata["output_failure_code"] = OUTPUT_DELIVERY_FAILURE_CODE
    metadata["remote_task_state"] = TaskState.completed.value
    task.metadata = metadata


__all__ = [
    "OUTPUT_DELIVERY_FAILURE_CODE",
    "OUTPUT_DELIVERY_FAILURE_MESSAGE",
    "has_unavailable_artifact_output",
    "has_usable_artifact_output",
    "mark_task_output_delivery_failed",
    "mark_unresolved_file_parts_unavailable",
    "new_materialization_report",
    "output_delivery_failed",
    "record_materialization_attempt",
    "record_materialization_failure",
    "record_materialization_success",
]
