"""Work-log mapping for orchestrator session lifecycle events."""

from __future__ import annotations

from datetime import UTC, datetime

from execution.orchestrator.lifecycle import (
    SessionEvent,
    orchestrator_lifecycle_log_message,
)


def _event(event_type: str, payload: dict | None = None) -> SessionEvent:
    return SessionEvent(
        event_type=event_type,  # type: ignore[arg-type]
        session_id="session-1",
        run_id="run-1",
        causation_id="msg-1",
        sequence=1,
        timestamp=datetime.now(UTC),
        payload=payload or {},
    )


def test_run_started_maps_to_planning_entry():
    assert orchestrator_lifecycle_log_message(_event("run_started")) == (
        "Planning the next actions",
        "collecting",
    )


def test_tool_execution_maps_agent_label_entries():
    started = _event(
        "tool_execution_started",
        {"call_id": "call-1", "tool_name": "t1", "agent_label": "Cyber Broker Agent"},
    )
    assert orchestrator_lifecycle_log_message(started) == (
        "Delegating to Cyber Broker Agent",
        "collecting",
    )
    completed = _event(
        "tool_execution_completed",
        {"call_id": "call-1", "agent_label": "Cyber Insurer Agent"},
    )
    assert orchestrator_lifecycle_log_message(completed) == (
        "Cyber Insurer Agent finished",
        "collecting",
    )


def test_tool_result_message_maps_responded_entry():
    event = _event(
        "message_completed",
        {
            "call_id": "call-1",
            "message_kind": "tool_result",
            "agent_label": "Weather Agent",
        },
    )
    assert orchestrator_lifecycle_log_message(event) == (
        "Weather Agent responded",
        "collecting",
    )


def test_waiting_and_terminal_preparation_entries():
    assert orchestrator_lifecycle_log_message(_event("run_waiting_external")) == (
        "Waiting for agents to respond",
        "collecting",
    )
    assert orchestrator_lifecycle_log_message(_event("run_awaiting_user")) == (
        "Waiting for your input",
        "collecting",
    )
    assert orchestrator_lifecycle_log_message(_event("run_final_answer_ready")) == (
        "Preparing the final answer",
        "synthesizing",
    )


def test_internal_events_are_silent():
    for event_type in (
        "session_started",
        "turn_started",
        "model_attempt_started",
        "model_retry_scheduled",
        "model_attempt_failed",
        "run_budget_exhausted",
        "run_failed",
        "run_canceled",
        "session_idle",
    ):
        assert orchestrator_lifecycle_log_message(_event(event_type)) is None
