"""SDK-free A2A task state and processing-status constants."""

from __future__ import annotations

from enum import Enum
from typing import Any


class TaskStateCategory(Enum):
    PENDING = "pending"
    INTERACTIVE = "interactive"
    TERMINAL = "terminal"


class SyntheticTaskId(str, Enum):
    PENDING = "pending"
    DEGRADED = "degraded"
    FAILED = "failed"


class CommonTaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    AUTH_REQUIRED = "auth-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"


PENDING_STATES = {CommonTaskState.SUBMITTED, CommonTaskState.WORKING}
INTERACTIVE_STATES = {
    CommonTaskState.INPUT_REQUIRED,
    CommonTaskState.AUTH_REQUIRED,
}
TERMINAL_STATES = {
    CommonTaskState.COMPLETED,
    CommonTaskState.FAILED,
    CommonTaskState.CANCELED,
    CommonTaskState.REJECTED,
}
FAILURE_STATES = {
    CommonTaskState.FAILED,
    CommonTaskState.CANCELED,
    CommonTaskState.REJECTED,
}
NON_TERMINAL_STATES = PENDING_STATES | INTERACTIVE_STATES


class SSEProcessingStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"
    AWAITING_INPUT = "awaiting_input"


PROCESSING_DONE_STATUSES = {
    SSEProcessingStatus.COMPLETED,
    SSEProcessingStatus.CANCELED,
    SSEProcessingStatus.FAILED,
    SSEProcessingStatus.REJECTED,
    SSEProcessingStatus.RATE_LIMITED,
    SSEProcessingStatus.ERROR,
}


def normalize_task_state_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    return str(raw)


def _state_member(state: Any) -> CommonTaskState | None:
    value = normalize_task_state_value(state)
    if value is None:
        return None
    try:
        return CommonTaskState(value)
    except ValueError:
        return None


def get_state_category(state: Any) -> TaskStateCategory:
    member = _state_member(state)
    if member in PENDING_STATES:
        return TaskStateCategory.PENDING
    if member in INTERACTIVE_STATES:
        return TaskStateCategory.INTERACTIVE
    return TaskStateCategory.TERMINAL


def is_terminal_state(state: Any) -> bool:
    return _state_member(state) in TERMINAL_STATES


def is_interactive_state(state: Any) -> bool:
    return _state_member(state) in INTERACTIVE_STATES


def is_failure_state(state: Any) -> bool:
    return _state_member(state) in FAILURE_STATES


def is_pending_state(state: Any) -> bool:
    return _state_member(state) in PENDING_STATES


def get_retry_after_seconds(state: Any) -> int | None:
    if is_terminal_state(state):
        return None
    if is_interactive_state(state):
        return 60
    return 30


__all__ = [
    "CommonTaskState",
    "FAILURE_STATES",
    "INTERACTIVE_STATES",
    "NON_TERMINAL_STATES",
    "PENDING_STATES",
    "PROCESSING_DONE_STATUSES",
    "SSEProcessingStatus",
    "SyntheticTaskId",
    "TERMINAL_STATES",
    "TaskStateCategory",
    "get_retry_after_seconds",
    "get_state_category",
    "is_failure_state",
    "is_interactive_state",
    "is_pending_state",
    "is_terminal_state",
    "normalize_task_state_value",
]
