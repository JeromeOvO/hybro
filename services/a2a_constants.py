"""
A2A Task State Constants and Helpers

This module defines constants and helper functions for working with A2A task states.
It provides a single source of truth for task state categorization.
"""

from enum import Enum

from a2a.types import TaskState


class TaskStateCategory(Enum):
    """Helper enum for categorizing A2A task states."""

    PENDING = "pending"
    INTERACTIVE = "interactive"
    TERMINAL = "terminal"


# Use A2A TaskState enum values for state sets
PENDING_STATES = {TaskState.submitted, TaskState.working}
INTERACTIVE_STATES = {TaskState.input_required, TaskState.auth_required}
TERMINAL_STATES = {
    TaskState.completed,
    TaskState.failed,
    TaskState.canceled,
    TaskState.rejected,
}

# States that need monitoring/polling
NON_TERMINAL_STATES = PENDING_STATES | INTERACTIVE_STATES


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
