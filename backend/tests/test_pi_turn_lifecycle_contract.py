from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from common.dto import (
    HITLRequestEvent,
    HITLResolvedEvent,
    RunEventNotification,
    TaskSubmittedEvent,
    TaskUpdateEvent,
)
from common.dto.pi_lifecycle import CANONICAL_RUN_EVENT_PAYLOADS

NOW = datetime(2030, 1, 1, tzinfo=UTC)

PAYLOADS = {
    "run_started": {
        "hybro_turn_id": "run-1",
        "user_message_id": "user-1",
        "started_at": NOW,
        "mode": "fast",
    },
    "turn_start": {"internal_turn_id": "turn-1", "attempt": 1},
    "message_start": {
        "internal_turn_id": "turn-1",
        "message_id": "assistant-1",
        "role": "assistant",
    },
    "message_update": {
        "internal_turn_id": "turn-1",
        "message_id": "assistant-1",
        "assistant_message_event": {
            "type": "text_delta",
            "content_index": 0,
            "delta_index": 0,
            "start_offset": 0,
            "end_offset": 2,
            "delta": "好a",
        },
    },
    "message_end": {
        "internal_turn_id": "turn-1",
        "message_id": "assistant-1",
        "stop_reason": "stop",
        "disposition": "final",
        "text": "done",
    },
    "tool_execution_start": {
        "internal_turn_id": "turn-1",
        "tool_call_id": "inv_abcdefgh",
        "tool_name": "Weather Agent",
        "input": {"task": "weather"},
    },
    "tool_execution_update": {
        "internal_turn_id": "turn-1",
        "tool_call_id": "inv_abcdefgh",
        "tool_name": "Weather Agent",
        "update_index": 1,
        "status": "running",
        "partial_result": "ready",
    },
    "tool_execution_end": {
        "internal_turn_id": "turn-1",
        "tool_call_id": "inv_abcdefgh",
        "tool_name": "Weather Agent",
        "outcome": "completed",
        "result": "sunny",
        "is_error": False,
        "duration_ms": 2,
    },
    "turn_end": {
        "internal_turn_id": "turn-1",
        "message_id": "assistant-1",
        "tool_call_ids": [],
        "status": "completed",
    },
    "retry_scheduled": {
        "internal_turn_id": "turn-1",
        "attempt": 2,
        "delay_ms": 1,
        "error_class": "provider_timeout",
    },
    "model_decision": {
        "internal_turn_id": "turn-1",
        "decision": "interaction_received",
        "agent_label": "Weather Agent",
        "question_summary": "Which city?",
    },
    "run_waiting_input": {
        "interaction_id": "interaction-1",
        "request_ids": ["request-1"],
        "requested_at": NOW,
    },
    "run_resumed": {
        "interaction_id": "interaction-1",
        "resolved_request_ids": ["request-1"],
        "resumed_at": NOW,
    },
    "run_settled": {
        "status": "completed",
        "started_at": NOW,
        "settled_at": NOW + timedelta(seconds=1),
        "duration_ms": 1000,
        "final_message_id": "assistant-1",
    },
}


@pytest.mark.parametrize("kind", sorted(CANONICAL_RUN_EVENT_PAYLOADS))
def test_every_canonical_payload_is_typed(kind: str):
    event = RunEventNotification(
        room_id="room-1",
        event_id=f"event-{kind}",
        run_id="run-1",
        seq=1,
        run_event_type=kind,
        payload=PAYLOADS[kind],
        correlation_id="client-1",
    )
    assert type(event.payload) is CANONICAL_RUN_EVENT_PAYLOADS[kind]


@pytest.mark.parametrize(
    ("kind", "mutation"),
    [
        ("message_update", {"reasoning": "private"}),
        ("message_end", {"stop_reason": "tool_use", "disposition": "final"}),
        ("tool_execution_start", {"tool_call_id": "provider-call-raw"}),
        ("tool_execution_end", {"outcome": "completed", "is_error": True}),
        ("run_settled", {"status": "completed", "failure_code": "internal_error"}),
    ],
)
def test_canonical_payload_rejects_private_or_contradictory_fields(
    kind: str, mutation: dict[str, object]
):
    payload = {**PAYLOADS[kind], **mutation}
    with pytest.raises(ValidationError):
        RunEventNotification(
            room_id="room-1",
            event_id="event-1",
            run_id="run-1",
            seq=1,
            run_event_type=kind,
            payload=payload,
            correlation_id="client-1",
        )


def test_canonical_hitl_dto_forbids_private_ids_and_raw_errors():
    request = HITLRequestEvent(
        room_id="room-1",
        run_id="run-1",
        request_id="request-1",
        message_id="hitl-message-1",
        source="agent",
        prompt="Approve?",
        prompt_type="confirmation",
        interaction_id="interaction-1",
        question_count=1,
        question_index=0,
        related_user_message_id="user-1",
        client_request_id="client-1",
        agent_label="Weather Agent",
    )
    assert request.agent_id is None
    with pytest.raises(ValidationError):
        HITLRequestEvent.model_validate(
            {**request.model_dump(), "agent_id": "private-agent"}
        )
    with pytest.raises(ValidationError):
        HITLResolvedEvent(
            room_id="room-1",
            run_id="run-1",
            request_id="request-1",
            message_id="hitl-message-1",
            source="agent",
            status="error",
            interaction_id="interaction-1",
            related_user_message_id="user-1",
            client_request_id="client-1",
            error_message="private traceback",
        )


def test_canonical_card_dto_rejects_private_ids_and_raw_content():
    submitted = TaskSubmittedEvent(
        room_id="room-1",
        run_id="run-1",
        opaque_public_call_id="inv_abcdefgh",
        message_id="orchestrator:run-1:inv_abcdefgh",
        task_id="task-inv_abcdefgh",
        agent_name="Weather Agent",
        related_message_id="user-1",
        client_request_id="client-1",
    )
    assert submitted.agent_id is None
    with pytest.raises(ValidationError):
        TaskSubmittedEvent.model_validate(
            {**submitted.model_dump(), "agent_id": "private-agent"}
        )
    with pytest.raises(ValidationError):
        TaskUpdateEvent(
            room_id="room-1",
            run_id="run-1",
            opaque_public_call_id="inv_abcdefgh",
            message_id="orchestrator:run-1:inv_abcdefgh",
            status="completed",
            content="raw private output",
            related_message_id="user-1",
            client_request_id="client-1",
        )


def test_canonical_root_requires_exact_run_and_correlation():
    with pytest.raises(ValidationError):
        RunEventNotification(
            room_id="room-1",
            event_id="event-1",
            run_id="different-run",
            seq=1,
            run_event_type="run_started",
            payload=PAYLOADS["run_started"],
            correlation_id=None,
        )
