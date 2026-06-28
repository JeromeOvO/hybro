"""Mappings for Execution runtime statuses written into persisted task state."""

from __future__ import annotations

from typing import Any

from common.types import TaskState

_INTERACTIVE_RUNTIME_TASK_STATES = {
    "awaiting_input": TaskState.input_required,
    "input_required": TaskState.input_required,
    "input-required": TaskState.input_required,
    "auth_required": TaskState.auth_required,
    "auth-required": TaskState.auth_required,
}


def _status_value(status: Any) -> str:
    return str(getattr(status, "value", status))


def system_task_state_from_runtime_status(status: Any) -> TaskState:
    """Convert Execution runtime status values to A2A-compatible task states."""
    value = _status_value(status)
    mapped = _INTERACTIVE_RUNTIME_TASK_STATES.get(value)
    if mapped is not None:
        return mapped
    return TaskState(value)


__all__ = ["system_task_state_from_runtime_status"]
