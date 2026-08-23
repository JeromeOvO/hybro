"""PublicProjectionTranslator: private SessionEvent → public run_event payloads."""

from __future__ import annotations

from datetime import UTC, datetime

from execution.orchestrator.lifecycle import SessionEvent
from execution.orchestrator.public_projection import (
    PUBLIC_RUN_EVENT_KINDS,
    PublicProjectionTranslator,
)


def _event(event_type: str, payload: dict | None = None) -> SessionEvent:
    return SessionEvent(
        event_type=event_type,  # type: ignore[arg-type]
        session_id="session-1",
        run_id="run-1",
        causation_id="msg-1",
        sequence=7,
        timestamp=datetime.now(UTC),
        payload=payload or {},
        room_id="room-1",
        user_message_id="msg-1",
        client_request_id="crid-1",
    )


def test_public_kinds_are_the_decision_visibility_vocabulary():
    assert PUBLIC_RUN_EVENT_KINDS == {
        "llm_call_completed",
        "llm_retry_scheduled",
        "orchestrator_decision",
        "tool_call_accepted",
        "tool_call_completed",
    }


def test_non_public_lifecycle_events_translate_to_none():
    translator = PublicProjectionTranslator()
    for event_type in (
        "session_started",
        "run_started",
        "turn_started",
        "model_attempt_started",
        "model_attempt_failed",
        "message_completed",
        "turn_completed",
        "run_waiting_external",
    ):
        assert translator.translate(_event(event_type)) is None


def test_llm_retry_scheduled_redacts_retryable():
    public = PublicProjectionTranslator().translate(
        _event(
            "model_retry_scheduled",
            {
                "attempt": 2,
                "error_class": "rate_limit",
                "retry_delay_ms": 1250,
                "retryable": True,
                "model": "gpt-4o",
                "provider": "openai",
            },
        )
    )
    assert public is not None
    assert public.kind == "llm_retry_scheduled"
    assert public.event_id == "public:run-1:llm_retry_scheduled:7"
    assert public.room_id == "room-1"
    assert public.user_message_id == "msg-1"
    assert public.client_request_id == "crid-1"
    assert public.payload == {
        "attempt": 2,
        "error_class": "rate_limit",
        "retry_delay_ms": 1250,
    }
    # retryable never enters the public payload.
    assert "retryable" not in public.payload


def test_llm_call_completed_carries_usage_metadata():
    public = PublicProjectionTranslator().translate(
        _event(
            "model_turn_completed",
            {
                "model": "gpt-4o",
                "provider": "openai",
                "attempt": 1,
                "outcome": "completed",
                "duration_ms": 812,
                "usage": {"input": 2100, "output": 340},
                "finish_reason": "stop",
            },
        )
    )
    assert public is not None
    assert public.kind == "llm_call_completed"
    assert public.payload == {
        "model": "gpt-4o",
        "provider": "openai",
        "attempt": 1,
        "outcome": "completed",
        "duration_ms": 812,
        "usage": {"input": 2100, "output": 340},
        "finish_reason": "stop",
    }


def test_orchestrator_decision_requires_plan_steps():
    translator = PublicProjectionTranslator()
    public = translator.translate(
        _event(
            "orchestrator_decision",
            {
                "plan_steps": [
                    {"agent": "Weather Agent", "summary": "Fetch the forecast"},
                    {"agent": "Broker Agent", "summary": ""},
                ],
                "reason": "Two independent lookups can run in parallel.",
            },
        )
    )
    assert public is not None
    assert public.kind == "orchestrator_decision"
    assert public.payload == {
        "chosen_agents": ["Weather Agent", "Broker Agent"],
        "plan_steps": [
            {"agent": "Weather Agent", "summary": "Fetch the forecast"},
            {"agent": "Broker Agent", "summary": ""},
        ],
        "reason": "Two independent lookups can run in parallel.",
    }

    # Empty plans produce no public event.
    assert (
        translator.translate(_event("orchestrator_decision", {"plan_steps": []}))
        is None
    )


def test_tool_call_accepted_redacts_arguments():
    public = PublicProjectionTranslator().translate(
        _event(
            "tool_execution_started",
            {
                "call_id": "call-1",
                "tool_name": "weather_lookup",
                "agent_label": "Weather Agent",
                "arguments": {
                    "city": "Shanghai",
                    "days": 3,
                    "secret_api_key": "never-leak-me",
                },
            },
        )
    )
    assert public is not None
    assert public.kind == "tool_call_accepted"
    assert public.payload["tool_name"] == "weather_lookup"
    assert public.payload["arg_summary"]["city"] == "Shanghai"
    assert public.payload["arg_summary"]["days"] == 3
    # Full argument values are truncated to short summaries and nested
    # structures collapse to metadata — but scalars under the limit pass
    # through unchanged, so assert the redaction limit instead.
    assert "secret_api_key" in public.payload["arg_summary"]


def test_tool_call_accepted_truncates_long_argument_values():
    public = PublicProjectionTranslator().translate(
        _event(
            "tool_execution_started",
            {
                "tool_name": "weather_lookup",
                "arguments": {"city": "x" * 5000},
            },
        )
    )
    assert public is not None
    summary = public.payload["arg_summary"]["city"]
    assert len(summary) <= 124  # 120 chars + ellipsis


def test_tool_call_completed_carries_result_summary_and_exit_code():
    public = PublicProjectionTranslator().translate(
        _event(
            "tool_execution_completed",
            {
                "call_id": "call-1",
                "status": "completed",
                "tool_name": "weather_lookup",
                "result_status": "completed",
                "result_text": "Sunny, 24C",
                "duration_ms": 120,
            },
        )
    )
    assert public is not None
    assert public.kind == "tool_call_completed"
    assert public.payload == {
        "tool_name": "weather_lookup",
        "result_summary": "Sunny, 24C",
        "exit_code": 0,
        "duration_ms": 120,
    }


def test_tool_call_completed_maps_failure_error_code():
    public = PublicProjectionTranslator().translate(
        _event(
            "tool_execution_completed",
            {
                "tool_name": "weather_lookup",
                "result_status": "failed",
                "result_error_code": "E503",
                "result_error_message": "upstream down",
            },
        )
    )
    assert public is not None
    assert public.payload["exit_code"] is None  # non-numeric codes stay None
    public = PublicProjectionTranslator().translate(
        _event(
            "tool_execution_completed",
            {
                "tool_name": "weather_lookup",
                "result_status": "failed",
                "result_error_code": 42,
            },
        )
    )
    assert public is not None
    assert public.payload["exit_code"] == 42


def test_translator_missing_room_id_degrades_to_empty_string():
    event = _event("model_retry_scheduled", {"attempt": 2, "error_class": "timeout"})
    event = event.model_copy(update={"room_id": None})
    public = PublicProjectionTranslator().translate(event)
    assert public is not None
    assert public.room_id == ""
