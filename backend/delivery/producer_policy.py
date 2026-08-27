"""Checked privacy policy inventory for top-level room-event producers."""

from __future__ import annotations

from typing import Literal

from common.dto.delivery import (
    AgentMessageFinal,
    AgentMessagePartial,
    ArtifactUpdateEvent,
    CancellationEvent,
    ErrorEvent,
    HITLRequestEvent,
    HITLResolvedEvent,
    ProcessingStatusEvent,
    RunEventNotification,
    TaskSubmittedEvent,
    TaskUpdateEvent,
)

FieldPolicy = Literal[
    "transport",
    "identity",
    "intentional_content",
    "sanitized_text",
    "safe_summary",
    "allowlisted_control",
    "private_legacy_only",
    "prohibited_canonical",
]


def _fields(model, overrides: dict[str, FieldPolicy]) -> dict[str, FieldPolicy]:
    base: dict[str, FieldPolicy] = {
        name: "prohibited_canonical" for name in model.model_fields
    }
    for name in ("room_id", "timestamp", "trace_id"):
        if name in base:
            base[name] = "transport"
    base.update(overrides)
    return base


ROOM_EVENT_PRODUCER_POLICY: dict[type, dict[str, FieldPolicy]] = {
    ProcessingStatusEvent: _fields(
        ProcessingStatusEvent,
        {
            "event_type": "allowlisted_control",
            "message_id": "identity",
            "status": "allowlisted_control",
            "agent_id": "private_legacy_only",
            "details": "private_legacy_only",
            "related_message_id": "identity",
            "client_request_id": "identity",
        },
    ),
    RunEventNotification: _fields(
        RunEventNotification,
        {
            "event_type": "allowlisted_control",
            "event_id": "identity",
            "delivery_id": "identity",
            "run_id": "identity",
            "seq": "allowlisted_control",
            "run_event_type": "allowlisted_control",
            "payload": "safe_summary",
            "correlation_id": "identity",
        },
    ),
    AgentMessagePartial: _fields(
        AgentMessagePartial,
        {
            "event_type": "prohibited_canonical",
            "message_id": "identity",
            "agent_id": "private_legacy_only",
            "content_delta": "private_legacy_only",
        },
    ),
    AgentMessageFinal: _fields(
        AgentMessageFinal,
        {
            "event_type": "allowlisted_control",
            "message_id": "identity",
            "agent_id": "allowlisted_control",
            "content": "sanitized_text",
            "delivery_id": "identity",
        },
    ),
    TaskSubmittedEvent: _fields(
        TaskSubmittedEvent,
        {
            "event_type": "allowlisted_control",
            "run_id": "identity",
            "opaque_public_call_id": "identity",
            "message_id": "identity",
            "task_id": "identity",
            "agent_name": "safe_summary",
            "status": "allowlisted_control",
            "related_message_id": "identity",
            "created_at": "allowlisted_control",
            "client_request_id": "identity",
        },
    ),
    TaskUpdateEvent: _fields(
        TaskUpdateEvent,
        {
            "event_type": "allowlisted_control",
            "run_id": "identity",
            "opaque_public_call_id": "identity",
            "message_id": "identity",
            "status": "allowlisted_control",
            "delivery_id": "identity",
            "requires_input": "allowlisted_control",
            "requires_auth": "allowlisted_control",
            "agent_name": "safe_summary",
            "related_message_id": "identity",
            "created_at": "allowlisted_control",
            "client_request_id": "identity",
        },
    ),
    ArtifactUpdateEvent: _fields(
        ArtifactUpdateEvent,
        {
            "event_type": "prohibited_canonical",
            "message_id": "identity",
            "agent_id": "private_legacy_only",
            "artifact": "private_legacy_only",
            "append": "private_legacy_only",
            "last_chunk": "private_legacy_only",
            "client_request_id": "identity",
        },
    ),
    ErrorEvent: _fields(
        ErrorEvent,
        {
            "event_type": "prohibited_canonical",
            "message_id": "identity",
            "client_request_id": "identity",
        },
    ),
    CancellationEvent: _fields(
        CancellationEvent,
        {
            "event_type": "prohibited_canonical",
            "message_id": "identity",
        },
    ),
    HITLRequestEvent: _fields(
        HITLRequestEvent,
        {
            "event_type": "allowlisted_control",
            "run_id": "identity",
            "request_id": "identity",
            "message_id": "identity",
            "source": "allowlisted_control",
            "prompt": "sanitized_text",
            "prompt_type": "allowlisted_control",
            "choices": "sanitized_text",
            "agent_label": "sanitized_text",
            "interaction_id": "identity",
            "question_count": "allowlisted_control",
            "question_index": "allowlisted_control",
            "related_user_message_id": "identity",
            "client_request_id": "identity",
        },
    ),
    HITLResolvedEvent: _fields(
        HITLResolvedEvent,
        {
            "event_type": "allowlisted_control",
            "run_id": "identity",
            "request_id": "identity",
            "message_id": "identity",
            "source": "allowlisted_control",
            "status": "allowlisted_control",
            "interaction_id": "identity",
            "question_count": "allowlisted_control",
            "question_index": "allowlisted_control",
            "answer_ref": "identity",
            "related_user_message_id": "identity",
            "client_request_id": "identity",
        },
    ),
}

CANONICAL_PROCESSING_STATUS_ADAPTER_STATUSES = frozenset(
    {"processing", "awaiting_input", "completed", "failed", "canceled"}
)


def canonical_processing_status_adapter(
    *,
    room_id: str,
    user_message_id: str,
    client_request_id: str,
    status: str,
) -> ProcessingStatusEvent:
    """Build the sole content-free stale-browser canonical compatibility DTO."""

    if not room_id or not user_message_id or not client_request_id:
        raise ValueError("canonical processing adapter requires exact nonempty roots")
    if status not in CANONICAL_PROCESSING_STATUS_ADAPTER_STATUSES:
        raise ValueError("canonical processing adapter status is not allowlisted")
    return ProcessingStatusEvent(
        room_id=room_id,
        message_id=user_message_id,
        related_message_id=user_message_id,
        client_request_id=client_request_id,
        status=status,  # type: ignore[arg-type]
        details=None,
        agent_id=None,
        agents=None,
    )


__all__ = [
    "CANONICAL_PROCESSING_STATUS_ADAPTER_STATUSES",
    "ROOM_EVENT_PRODUCER_POLICY",
    "FieldPolicy",
    "canonical_processing_status_adapter",
]
