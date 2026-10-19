"""System task state mapping for runtime statuses."""

from enum import StrEnum

import pytest

from common.types import TaskState as CommonTaskState
from execution.state.task_status_mapping import system_task_state_from_runtime_status


class _RuntimeStatus(StrEnum):
    AWAITING_INPUT = "awaiting_input"


@pytest.mark.parametrize(
    ("runtime_status", "task_state"),
    [
        (_RuntimeStatus.AWAITING_INPUT, CommonTaskState.input_required),
        ("awaiting_input", CommonTaskState.input_required),
        ("input_required", CommonTaskState.input_required),
        ("auth_required", CommonTaskState.auth_required),
        ("completed", CommonTaskState.completed),
    ],
)
def test_maps_runtime_status_to_a2a_task_state(runtime_status, task_state):
    assert system_task_state_from_runtime_status(runtime_status) == task_state
