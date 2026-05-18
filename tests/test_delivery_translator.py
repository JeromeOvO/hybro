from datetime import datetime, timezone

from common.dto import (
    AgentMessageFinal,
    AgentMessagePartial,
    CancellationEvent,
    DebateRoundEvent,
    HITLRequestEvent,
    HITLResolvedEvent,
    HubAgentEvent,
    ProcessingStatusEvent,
    RunEventNotification,
)
from delivery.translator import to_sse_frame


NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def test_processing_status_translation():
    event = ProcessingStatusEvent(
        room_id="room-1",
        message_id="msg-1",
        status="processing",
        details={"step": 1},
        agent_id="agent-1",
        client_request_id="cr-1",
        agents=[{"agent_id": "agent-1"}],
        trace_id="trace-1",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "processing_status",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "status": "processing",
            "message_id": "msg-1",
            "details": {"step": 1},
            "timestamp": NOW.isoformat(),
            "agent_id": "agent-1",
            "client_request_id": "cr-1",
            "agents": [{"agent_id": "agent-1"}],
            "trace_id": "trace-1",
        },
    }


def test_run_event_translation_always_includes_correlation_id():
    event = RunEventNotification(
        room_id="room-1",
        event_id="evt-1",
        run_id="run-1",
        seq=2,
        run_event_type="agent_started",
        payload={"agent_id": "agent-1"},
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "run_event",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "event_id": "evt-1",
            "run_id": "run-1",
            "seq": 2,
            "type": "agent_started",
            "payload": {"agent_id": "agent-1"},
            "correlation_id": None,
        },
    }


def test_agent_message_partial_translation():
    event = AgentMessagePartial(
        room_id="room-1",
        message_id="msg-1",
        agent_id="agent-1",
        content_delta="hello",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "agent_response_partial",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "message_id": "msg-1",
            "agent_id": "agent-1",
            "content_delta": "hello",
            "timestamp": NOW.isoformat(),
        },
    }


def test_agent_message_final_translation_merges_content():
    event = AgentMessageFinal(
        room_id="room-1",
        message_id="msg-1",
        agent_id="agent-1",
        content={"content": "done", "parts": [{"text": "done"}]},
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "agent_response",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "message_id": "msg-1",
            "agent_id": "agent-1",
            "timestamp": NOW.isoformat(),
            "content": "done",
            "parts": [{"text": "done"}],
        },
    }


def test_cancellation_translation():
    event = CancellationEvent(room_id="room-1", message_id="msg-1", reason="user")

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "cancellation",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "message_id": "msg-1",
            "reason": "user",
            "timestamp": NOW.isoformat(),
        },
    }


def test_hitl_request_translation():
    event = HITLRequestEvent(
        room_id="room-1",
        request_id="hitl-1",
        message_id="msg-1",
        prompt="Continue?",
        prompt_type="text",
        source="agent",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "hitl_input_requested",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "request_id": "hitl-1",
            "message_id": "msg-1",
            "prompt": "Continue?",
            "prompt_type": "text",
            "source": "agent",
            "timestamp": NOW.isoformat(),
        },
    }


def test_hitl_resolved_translation():
    event = HITLResolvedEvent(room_id="room-1", request_id="hitl-1", message_id="msg-1")

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "hitl_status_update",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "request_id": "hitl-1",
            "message_id": "msg-1",
            "status": "resolved",
            "timestamp": NOW.isoformat(),
        },
    }


def test_hub_agent_event_translation():
    event = HubAgentEvent(
        room_id="room-1",
        hub_id="hub-1",
        agent_id="agent-1",
        message_id="msg-1",
        status="working",
        partial="hello",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "hub_agent_event",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "hub_id": "hub-1",
            "agent_id": "agent-1",
            "message_id": "msg-1",
            "status": "working",
            "partial": "hello",
            "timestamp": NOW.isoformat(),
        },
    }


def test_debate_round_translation():
    event = DebateRoundEvent(
        room_id="room-1",
        round_number=3,
        agent_id="agent-1",
        message_id="msg-1",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "debate_round",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "round_number": 3,
            "agent_id": "agent-1",
            "message_id": "msg-1",
            "timestamp": NOW.isoformat(),
        },
    }
