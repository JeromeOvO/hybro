"""Unit tests for run FSM transition guard."""

import pytest

from execution.run_reducer import (
    RunTransitionError,
    ensure_transition_allowed,
    next_state_for_terminal_event,
)
from models.run import RunEventType, RunState


def test_same_state_allowed():
    ensure_transition_allowed(RunState.PROCESSING, RunState.PROCESSING)


def test_cannot_leave_terminal():
    with pytest.raises(RunTransitionError):
        ensure_transition_allowed(RunState.COMPLETED, RunState.PROCESSING)


def test_queued_to_processing():
    ensure_transition_allowed(RunState.QUEUED, RunState.PROCESSING)


def test_processing_to_completed():
    ensure_transition_allowed(RunState.PROCESSING, RunState.COMPLETED)


def test_illegal_processing_to_queued():
    with pytest.raises(RunTransitionError):
        ensure_transition_allowed(RunState.PROCESSING, RunState.QUEUED)


def test_next_state_for_terminal_event():
    assert (
        next_state_for_terminal_event(RunEventType.RUN_COMPLETED) == RunState.COMPLETED
    )
    assert next_state_for_terminal_event(RunEventType.RUN_FAILED) == RunState.FAILED
    assert next_state_for_terminal_event(RunEventType.RUN_CANCELED) == RunState.CANCELED
    with pytest.raises(RunTransitionError):
        next_state_for_terminal_event(RunEventType.RUN_STARTED)
