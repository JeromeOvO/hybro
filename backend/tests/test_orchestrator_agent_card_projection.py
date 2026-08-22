"""Focused tests for orchestrator agent-card terminal-state mapping (#3).

The legacy supervisor card renderer distinguishes ``rejected`` / ``canceled`` /
``expired`` from ``failed``. The orchestrator projection path used to collapse
every non-``completed`` tool result into ``TaskState.failed``, so a rejected
agent call rendered as "Failed". These tests pin the faithful mapping.
"""

from __future__ import annotations

import pytest

from common.types import TaskState
from container import _map_orchestrator_terminal_state


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("completed", TaskState.completed),
        ("failed", TaskState.failed),
        ("canceled", TaskState.canceled),
        ("rejected", TaskState.rejected),
        ("expired", TaskState.expired),
    ],
)
def test_map_orchestrator_terminal_state_maps_each_status(status, expected):
    assert TaskState(_map_orchestrator_terminal_state(status)) is expected


@pytest.mark.parametrize("status", [None, "unknown", "rate_limited"])
def test_map_orchestrator_terminal_state_falls_back_to_failed(status):
    assert TaskState(_map_orchestrator_terminal_state(status)) is TaskState.failed
