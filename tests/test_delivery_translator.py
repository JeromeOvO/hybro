from datetime import UTC, datetime

from common.dto import (
    AgentMessageFinal,
    AgentMessagePartial,
    ArtifactUpdateEvent,
    CancellationEvent,
    DebateRoundEvent,
    HITLRequestEvent,
    HITLResolvedEvent,
    HubAgentEvent,
    ErrorEvent,
    ProcessingStatusEvent,
    RunEventNotification,
    TaskSubmittedEvent,
    TaskUpdateEvent,
)
from delivery.translator import to_sse_frame

NOW = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)


def test_processing_status_translation_uses_final_frame_without_nested_timestamp():
    event = ProcessingStatusEvent(
        room_id="room-1",
        message_id="msg-1",
        status="awaiting_input",
        details={"reason": "hitl"},
        agent_id="agent-1",
        related_message_id="umsg-1",
        client_request_id="cr-1",
        agents=[{"agent_id": "agent-1"}],
        trace_id="trace-1",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "processing_status",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "status": "awaiting_input",
            "message_id": "msg-1",
            "details": {"reason": "hitl"},
            "agent_id": "agent-1",
            "related_message_id": "umsg-1",
            "client_request_id": "cr-1",
            "agents": [{"agent_id": "agent-1"}],
            "trace_id": "trace-1",
        },
    }


def test_processing_status_accepts_all_final_statuses():
    statuses = [
        "queued",
        "processing",
        "awaiting_input",
        "completed",
        "failed",
        "canceled",
        "rejected",
        "rate_limited",
        "error",
    ]

    for status in statuses:
        event = ProcessingStatusEvent(
            room_id="room-1",
            message_id="msg-1",
            status=status,
            details=None,
        )
        frame = to_sse_frame(event, timestamp=NOW)
        assert frame["type"] == "processing_status"
        assert frame["data"]["status"] == status
        assert "timestamp" not in frame["data"]


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
            "content": "done",
            "parts": [{"text": "done"}],
        },
    }


def test_agent_message_final_translation_drops_reserved_timestamp_from_content():
    event = AgentMessageFinal(
        room_id="room-1",
        message_id="msg-1",
        agent_id="agent-1",
        content={"content": "done", "timestamp": "nested"},
    )

    frame = to_sse_frame(event, timestamp=NOW)

    assert frame["timestamp"] == NOW.isoformat()
    assert "timestamp" not in frame["data"]


def test_cancellation_translation():
    event = CancellationEvent(room_id="room-1", message_id="msg-1", reason="user")

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "cancellation",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "message_id": "msg-1",
            "reason": "user",
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
        "type": "hitl_request",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "request_id": "hitl-1",
            "message_id": "msg-1",
            "prompt": "Continue?",
            "prompt_type": "text",
            "source": "agent",
        },
    }


def test_hitl_request_translation_preserves_full_payload():
    event = HITLRequestEvent(
        room_id="room-1",
        request_id="hitl-1",
        message_id="msg-1",
        source="agent",
        prompt="Pick one",
        prompt_type="choice",
        choices=["a", "b"],
        agent_id="agent-1",
        agent_name="Agent",
            source_step_id="step-1",
            group_id="group-1",
            group_total=2,
            group_index=1,
            related_message_id="umsg-1",
            client_request_id="cr-1",
        )

    frame = to_sse_frame(event, timestamp=NOW)

    assert frame == {
        "type": "hitl_request",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "request_id": "hitl-1",
            "message_id": "msg-1",
            "source": "agent",
            "prompt": "Pick one",
            "prompt_type": "choice",
            "choices": ["a", "b"],
            "agent_id": "agent-1",
            "agent_name": "Agent",
            "source_step_id": "step-1",
            "group_id": "group-1",
            "group_total": 2,
            "group_index": 1,
            "related_message_id": "umsg-1",
            "client_request_id": "cr-1",
        },
    }


def test_hitl_resolved_translation():
    event = HITLResolvedEvent(
        room_id="room-1",
        request_id="hitl-1",
        message_id="msg-1",
        source="agent",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "hitl_response",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "request_id": "hitl-1",
            "message_id": "msg-1",
            "status": "resolved",
            "source": "agent",
        },
    }


def test_hitl_status_translation_preserves_status_source_and_error():
    event = HITLResolvedEvent(
        room_id="room-1",
        request_id="hitl-1",
        message_id="msg-1",
        source="agent",
        related_message_id="umsg-1",
        status="error",
        error_message="expired",
        client_request_id="cr-1",
    )

    frame = to_sse_frame(event, timestamp=NOW)

    assert frame == {
        "type": "hitl_response",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "request_id": "hitl-1",
            "message_id": "msg-1",
            "source": "agent",
            "status": "error",
            "error_message": "expired",
            "related_message_id": "umsg-1",
            "client_request_id": "cr-1",
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
        },
    }


def test_task_submitted_translation():
    event = TaskSubmittedEvent(
        room_id="room-1",
        message_id="agent-msg-1",
        task_id="task-1",
        agent_name="Agent",
        agent_id="agent-1",
        status="working",
        related_message_id="user-msg-1",
        created_at="created",
        step_number=1,
        total_steps=2,
        task_content="do work",
        client_request_id="cr-1",
    )

    assert to_sse_frame(event, timestamp=NOW) == {
        "type": "task_submitted",
        "timestamp": NOW.isoformat(),
        "room_id": "room-1",
        "data": {
            "message_id": "agent-msg-1",
            "task_id": "task-1",
            "agent_name": "Agent",
            "agent_id": "agent-1",
            "status": "working",
            "related_message_id": "user-msg-1",
            "created_at": "created",
            "step_number": 1,
            "total_steps": 2,
            "task_content": "do work",
            "client_request_id": "cr-1",
        },
    }


def test_task_update_translation():
    event = TaskUpdateEvent(
        room_id="room-1",
        message_id="agent-msg-1",
        status="input-required",
        content="content",
        error=None,
        requires_input=True,
        requires_auth=False,
        status_message="waiting",
        agent_name="Agent",
        agent_id="agent-1",
        related_message_id="user-msg-1",
        created_at="created",
        step_number=1,
        total_steps=2,
        task_content="do work",
        parts=[{"kind": "text"}],
        client_request_id="cr-1",
    )

    frame = to_sse_frame(event, timestamp=NOW)
    assert frame["type"] == "task_update"
    assert frame["data"]["message_id"] == "agent-msg-1"
    assert frame["data"]["requires_input"] is True
    assert frame["data"]["created_at"] == "created"
    assert frame["data"]["parts"] == [{"kind": "text"}]
    assert "timestamp" not in frame["data"]


def test_artifact_update_translation():
    event = ArtifactUpdateEvent(
        room_id="room-1",
        message_id="agent-msg-1",
        agent_id="agent-1",
        artifact={"kind": "file"},
        append=True,
        last_chunk=False,
        client_request_id="cr-1",
    )

    assert to_sse_frame(event, timestamp=NOW)["data"] == {
        "message_id": "agent-msg-1",
        "agent_id": "agent-1",
        "artifact": {"kind": "file"},
        "append": True,
        "last_chunk": False,
        "client_request_id": "cr-1",
    }


def test_error_event_translation():
    event = ErrorEvent(
        room_id="room-1",
        error="slow down",
        error_type="rate_limit_exceeded",
        message_id="msg-1",
        agent_id="agent-1",
        retry_after_seconds=5,
        client_request_id="cr-1",
    )

    frame = to_sse_frame(event, timestamp=NOW)
    assert frame["type"] == "error"
    assert frame["data"]["error_type"] == "rate_limit_exceeded"


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
        },
    }
