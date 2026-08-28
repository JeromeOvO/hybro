"""PublicProjectionTranslator: private SessionEvent → public run_event payloads."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from common.dto import RunEventNotification
from delivery.room_events import InMemoryRoomEventStore
from execution.orchestrator.lifecycle import SessionEvent
from execution.orchestrator.public_projection import (
    PUBLIC_RUN_EVENT_KINDS,
    PublicProjectionTranslator,
)
from tests.test_delivery_event_publisher import FakeTransport, make_publisher


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


def test_public_kinds_are_the_canonical_turn_lifecycle_vocabulary():
    assert PUBLIC_RUN_EVENT_KINDS == {
        "run_started",
        "turn_start",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
        "turn_end",
        "retry_scheduled",
        "model_decision",
        "run_waiting_input",
        "run_resumed",
        "run_settled",
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


def test_canonical_tool_label_enforces_configured_secret_policy():
    event = _event(
        "tool_execution_started",
        {
            "internal_turn_id": "turn-1",
            "call_id": "private-call",
            "public_call_id": "inv_weather_0001",
            "tool_name": "weather_lookup",
            "agent_label": "Weather token=hunter2",
            "arguments": {},
        },
    ).model_copy(update={"lifecycle_family": "canonical"})
    public = PublicProjectionTranslator(
        lifecycle_family="canonical",
        secret_values=("hunter2",),
    ).translate(event)
    assert public is not None
    assert public.payload["tool_name"] == "Weather token[REDACTED]"
    assert "hunter2" not in str(public.payload)


@pytest.mark.asyncio
async def test_canonical_label_secret_never_reaches_persisted_room_event():
    sentinel = "PRIVATE_LABEL_SENTINEL"
    event = _event(
        "tool_execution_started",
        {
            "internal_turn_id": "turn-1",
            "call_id": "private-call",
            "public_call_id": "inv_weather_0001",
            "tool_name": "weather_lookup",
            "agent_label": f"Weather {sentinel}",
            "arguments": {},
        },
    ).model_copy(update={"lifecycle_family": "canonical"})
    projected = PublicProjectionTranslator(
        lifecycle_family="canonical", secret_values=(sentinel,)
    ).translate(event)
    assert projected is not None
    store = InMemoryRoomEventStore()
    publisher = make_publisher(transport=FakeTransport(), room_events=store)
    await publisher.emit_checked_identified(
        RunEventNotification(
            room_id=projected.room_id,
            event_id=projected.event_id,
            run_id=projected.run_id,
            seq=projected.seq,
            run_event_type=projected.kind,
            payload=projected.payload,
            correlation_id=projected.client_request_id,
        )
    )
    persisted = await store.read_range("room-1")
    assert sentinel not in str(persisted)
    assert "[REDACTED]" in str(persisted)


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
    assert public.payload["call_id"].startswith("inv_")
    assert public.payload["call_id"] != "call-1"
    assert public.payload["tool_name"] == "Weather Agent"
    assert public.payload["arg_summary"] == {}
    assert "never-leak-me" not in str(public.payload)


def test_tool_call_accepted_denies_unknown_argument_projection():
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
    assert public.payload["arg_summary"] == {}


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
    assert public.payload["call_id"].startswith("inv_")
    assert public.payload | {"call_id": "<opaque>"} == {
        "call_id": "<opaque>",
        "tool_name": "weather_lookup",
        "result_summary": "",
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
    # A non-numeric provider error still has a truthful failed public outcome.
    assert public.payload["exit_code"] == 1
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


def _agent_catalog():
    from execution.orchestrator.models import (
        FrozenToolCatalogEntry,
        FrozenToolCatalogSnapshot,
        ToolBindingRef,
        ToolDefinition,
    )

    return FrozenToolCatalogSnapshot(
        catalog_id="catalog-1",
        entries=[
            FrozenToolCatalogEntry(
                definition=ToolDefinition(
                    name="agent_weather",
                    label="Weather Agent - Forecast",
                    description="Weather",
                    input_schema={
                        "type": "object",
                        "properties": {"task": {"type": "string"}},
                    },
                    execution_mode="parallel",
                    side_effect_level="read",
                ),
                binding=ToolBindingRef(binding_id="binding-1", binding_digest="digest"),
                agent_display_name="Weather Agent",
            )
        ],
        created_at=datetime.now(UTC),
    )


def _canonical_tool_event(event_type: str, payload: dict):
    return _event(event_type, payload).model_copy(
        update={"lifecycle_family": "canonical"}
    )


def test_canonical_run_started_identity_ignores_recovery_payload_ids():
    translator = PublicProjectionTranslator(lifecycle_family="canonical")
    durable_started_at = datetime(2030, 1, 1, tzinfo=UTC)
    initial = _event("run_started", {"mode": "ultimate"}).model_copy(
        update={"lifecycle_family": "canonical"}
    )
    recovery = _event(
        "run_started",
        {
            "mode": "ultimate",
            "public_event_id": "public:run-1:run_started",
            "started_at": durable_started_at,
        },
    ).model_copy(update={"lifecycle_family": "canonical", "sequence": 99})

    initial_public = translator.translate(initial)
    recovery_public = translator.translate(recovery)

    assert initial_public is not None
    assert recovery_public is not None
    assert initial_public.event_id == recovery_public.event_id
    assert recovery_public.payload["started_at"] == durable_started_at


def test_canonical_agent_execution_exposes_base_name_and_kind():
    translator = PublicProjectionTranslator(lifecycle_family="canonical")
    public = translator.translate(
        _canonical_tool_event(
            "tool_execution_started",
            {
                "internal_turn_id": "turn-1",
                "call_id": "private-call",
                "public_call_id": "inv_weather_0001",
                "tool_name": "agent_weather",
                "agent_label": "Weather Agent - Forecast",
                "arguments": {"task": "Check San Jose weather"},
            },
        ),
        catalog=_agent_catalog(),
    )
    assert public is not None
    assert public.kind == "tool_execution_start"
    assert public.payload["execution_kind"] == "agent"
    assert public.payload["target"] == {"name": "Weather Agent", "source": None}
    assert public.payload["tool_name"] == "Weather Agent - Forecast"
    assert public.payload["request_summary"] == "Check San Jose weather"


def test_canonical_agent_execution_omits_private_a2a_transport_metadata():
    translator = PublicProjectionTranslator(lifecycle_family="canonical")
    public = translator.translate(
        _canonical_tool_event(
            "tool_execution_started",
            {
                "internal_turn_id": "turn-1",
                "call_id": "private-call",
                "public_call_id": "inv_weather_0001",
                "tool_name": "agent_weather",
                "agent_label": "Weather Agent - Forecast",
                "arguments": {"task": "Check weather"},
                "message_metadata": {
                    "hybro.ai/a2a/selected-skill": {
                        "schema_version": 1,
                        "skill_id": "PRIVATE_SKILL_SENTINEL",
                    }
                },
                "part_metadata": {
                    "hybro.ai/a2a/part-provenance": {
                        "schema_version": 1,
                        "role": "orchestrator_instruction",
                    }
                },
            },
        ),
        catalog=_agent_catalog(),
    )

    assert public is not None
    assert "PRIVATE_SKILL_SENTINEL" not in str(public.payload)
    assert "hybro.ai/a2a/selected-skill" not in str(public.payload)
    assert "hybro.ai/a2a/part-provenance" not in str(public.payload)


def test_canonical_agent_execution_end_marks_private_detail_available():
    translator = PublicProjectionTranslator(lifecycle_family="canonical")
    public = translator.translate(
        _canonical_tool_event(
            "tool_execution_completed",
            {
                "internal_turn_id": "turn-1",
                "call_id": "private-call",
                "public_call_id": "inv_weather_0001",
                "tool_name": "agent_weather",
                "agent_label": "Weather Agent - Forecast",
                "result_status": "completed",
                "result_text": "Clear, 22C",
                "duration_ms": 120,
            },
        ),
        catalog=_agent_catalog(),
    )
    assert public is not None
    assert public.payload["execution_kind"] == "agent"
    assert public.payload["target"] == {"name": "Weather Agent", "source": None}
    assert public.payload["detail_available"] is True
    assert public.payload["result"] == ""
    assert "Clear, 22C" not in str(public.payload)


def test_canonical_unknown_tool_is_plain_tool_execution_without_target():
    translator = PublicProjectionTranslator(lifecycle_family="canonical")
    public = translator.translate(
        _canonical_tool_event(
            "tool_execution_started",
            {
                "internal_turn_id": "turn-1",
                "call_id": "private-call",
                "public_call_id": "inv_tool_0000001",
                "tool_name": "request_user_input",
                "arguments": {"question": "Which city?"},
            },
        ),
        catalog=_agent_catalog(),
    )
    assert public is not None
    assert public.payload["execution_kind"] == "tool"
    assert "target" not in public.payload
    assert public.payload["request_summary"] == ""
    assert public.payload["tool_name"] == "request_user_input"


def test_model_decision_projects_answered_from_context_and_no_progress():
    translator = PublicProjectionTranslator(lifecycle_family="canonical")

    answered = translator.translate(
        _event(
            "model_decision",
            {
                "internal_turn_id": "turn-1",
                "decision": "answered_from_context",
                "agent_label": "Cyber Broker Agent",
                "question_summary": "Which cloud provider?",
                "source_summary": "from earlier messages and attachments",
            },
        )
    )
    assert answered is not None
    assert answered.kind == "model_decision"
    assert answered.payload["decision"] == "answered_from_context"
    assert answered.payload["agent_label"] == "Cyber Broker Agent"
    assert answered.payload["question_summary"] == "Which cloud provider?"
    assert answered.payload["source_summary"] == "from earlier messages and attachments"
    # The semantic event id must not embed raw public text.
    assert "Which cloud" not in answered.event_id

    no_progress = translator.translate(
        _event(
            "model_decision",
            {
                "internal_turn_id": "turn-1",
                "decision": "no_progress",
                "agent_label": "Cyber Broker Agent",
                "question_summary": "Which cloud provider?",
                "reason": "auto_reply_limit_reached",
            },
        )
    )
    assert no_progress is not None
    assert no_progress.payload["decision"] == "no_progress"
    assert no_progress.payload["reason"] == "auto_reply_limit_reached"
