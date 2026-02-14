"""
A2A Task State Constants and Helpers

This module defines constants and helper functions for working with A2A task states
and SSE processing statuses. It provides a single source of truth for state categorization.
"""

from enum import Enum

from a2a.types import TaskState


class TaskStateCategory(Enum):
    """Helper enum for categorizing A2A task states."""

    PENDING = "pending"
    INTERACTIVE = "interactive"
    TERMINAL = "terminal"


# ---------------------------------------------------------------------------
# Synthetic Task IDs for degraded/fallback modes
# ---------------------------------------------------------------------------


class SyntheticTaskId(str, Enum):
    """Task IDs used when real A2A task tracking is unavailable.

    These replace hard-coded magic strings such as ``"pending"`` or
    ``"degraded"`` that were previously scattered across the codebase.
    """

    PENDING = "pending"  # Task submitted but no real task_id yet
    DEGRADED = "degraded"  # Degraded mode — no A2A task tracking available
    FAILED = "failed"  # Placeholder task created on agent-call failure


# ---------------------------------------------------------------------------
# A2A Task State groupings (values come from the a2a.types.TaskState enum)
# ---------------------------------------------------------------------------

PENDING_STATES = {TaskState.submitted, TaskState.working}
INTERACTIVE_STATES = {TaskState.input_required, TaskState.auth_required}
TERMINAL_STATES = {
    TaskState.completed,
    TaskState.failed,
    TaskState.canceled,
    TaskState.rejected,
}

# Terminal states that indicate failure (terminal minus completed)
FAILURE_STATES = {TaskState.failed, TaskState.canceled, TaskState.rejected}

# States that need monitoring/polling
NON_TERMINAL_STATES = PENDING_STATES | INTERACTIVE_STATES


# ---------------------------------------------------------------------------
# SSE Processing Status constants (sent via send_processing_status)
# ---------------------------------------------------------------------------

class SSEProcessingStatus(str, Enum):
    """Status values for the processing_status SSE event.

    These are the values sent to the frontend via the 'processing_status' SSE event
    to control the processing indicator (spinner) in the UI.
    """

    PROCESSING = "processing"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


# Statuses that indicate processing is done (clear the spinner)
PROCESSING_DONE_STATUSES = {
    SSEProcessingStatus.COMPLETED,
    SSEProcessingStatus.CANCELED,
    SSEProcessingStatus.FAILED,
    SSEProcessingStatus.REJECTED,
}


def get_state_category(state: TaskState) -> TaskStateCategory:
    """Get the category for an A2A task state."""
    if state in PENDING_STATES:
        return TaskStateCategory.PENDING
    if state in INTERACTIVE_STATES:
        return TaskStateCategory.INTERACTIVE
    return TaskStateCategory.TERMINAL


def is_terminal_state(state: TaskState) -> bool:
    """Check if a task state is terminal (task is done)."""
    return state in TERMINAL_STATES


def is_interactive_state(state: TaskState) -> bool:
    """Check if a task state requires user interaction."""
    return state in INTERACTIVE_STATES


def is_failure_state(state: TaskState) -> bool:
    """Check if a task state indicates failure (failed, canceled, or rejected)."""
    return state in FAILURE_STATES


def is_pending_state(state: TaskState) -> bool:
    """Check if a task state is pending (still processing)."""
    return state in PENDING_STATES


def get_retry_after_seconds(state: TaskState) -> int | None:
    """
    Get recommended polling interval for a task state.

    Returns:
        Seconds to wait before polling again, or None for terminal states.
    """
    if is_terminal_state(state):
        return None
    if is_interactive_state(state):
        return 60  # User action needed, poll less frequently
    return 30  # Default for pending states
