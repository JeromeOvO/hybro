from __future__ import annotations

from typing import Any

from a2a.types import GetTaskRequest, JSONRPCErrorResponse, TaskQueryParams


def build_get_task_request(task_id: str) -> GetTaskRequest:
    return GetTaskRequest(id=task_id, params=TaskQueryParams(id=task_id))


def extract_get_task_result(response: Any) -> Any | None:
    if response is None:
        return None
    root = getattr(response, "root", response)
    if isinstance(root, JSONRPCErrorResponse):
        return None
    return getattr(root, "result", None)


__all__ = ["build_get_task_request", "extract_get_task_result"]
