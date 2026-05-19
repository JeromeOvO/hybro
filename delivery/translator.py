from datetime import datetime
from typing import Any

from common.dto import (
    AgentMessageFinal,
    AgentMessagePartial,
    CancellationEvent,
    DebateRoundEvent,
    DeliveryEvent,
    HITLRequestEvent,
    HITLResolvedEvent,
    HubAgentEvent,
    ProcessingStatusEvent,
    RunEventNotification,
)


def to_sse_frame(event: DeliveryEvent, *, timestamp: datetime) -> dict[str, Any]:
    frame_timestamp = timestamp.isoformat()
    if isinstance(event, ProcessingStatusEvent):
        data: dict[str, Any] = {
            "status": event.status,
            "message_id": event.message_id,
            "details": event.details,
            "timestamp": frame_timestamp,
        }
        _add_optional(data, "agent_id", event.agent_id)
        _add_optional(data, "client_request_id", event.client_request_id)
        _add_optional(data, "agents", event.agents)
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "processing_status", data, frame_timestamp)

    if isinstance(event, RunEventNotification):
        data = {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "seq": event.seq,
            "type": event.run_event_type,
            "payload": event.payload,
            "correlation_id": event.correlation_id,
        }
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "run_event", data, frame_timestamp)

    if isinstance(event, AgentMessagePartial):
        data = {
            "message_id": event.message_id,
            "agent_id": event.agent_id,
            "content_delta": event.content_delta,
            "timestamp": frame_timestamp,
        }
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "agent_response_partial", data, frame_timestamp)

    if isinstance(event, AgentMessageFinal):
        data = {
            "message_id": event.message_id,
            "agent_id": event.agent_id,
            "timestamp": frame_timestamp,
        }
        data.update(event.content)
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "agent_response", data, frame_timestamp)

    if isinstance(event, CancellationEvent):
        data = {
            "message_id": event.message_id,
            "reason": event.reason,
            "timestamp": frame_timestamp,
        }
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "cancellation", data, frame_timestamp)

    if isinstance(event, HITLRequestEvent):
        data = {
            "request_id": event.request_id,
            "message_id": event.message_id,
            "prompt": event.prompt,
            "prompt_type": event.prompt_type,
            "source": event.source,
            "timestamp": frame_timestamp,
        }
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "hitl_input_requested", data, frame_timestamp)

    if isinstance(event, HITLResolvedEvent):
        data = {
            "request_id": event.request_id,
            "message_id": event.message_id,
            "status": "resolved",
            "timestamp": frame_timestamp,
        }
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "hitl_status_update", data, frame_timestamp)

    if isinstance(event, HubAgentEvent):
        data = {
            "hub_id": event.hub_id,
            "agent_id": event.agent_id,
            "message_id": event.message_id,
            "status": event.status,
            "timestamp": frame_timestamp,
        }
        _add_optional(data, "partial", event.partial)
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "hub_agent_event", data, frame_timestamp)

    if isinstance(event, DebateRoundEvent):
        data = {
            "round_number": event.round_number,
            "agent_id": event.agent_id,
            "message_id": event.message_id,
            "timestamp": frame_timestamp,
        }
        _add_trace_id(data, event.trace_id)
        return _frame(event.room_id, "debate_round", data, frame_timestamp)

    raise TypeError(f"Unsupported delivery event: {type(event)!r}")


def _frame(
    room_id: str,
    event_type: str,
    data: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    return {
        "type": event_type,
        "timestamp": timestamp,
        "room_id": room_id,
        "data": data,
    }


def _add_optional(data: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        data[key] = value


def _add_trace_id(data: dict[str, Any], trace_id: str | None) -> None:
    if trace_id:
        data["trace_id"] = trace_id


__all__ = ["to_sse_frame"]
