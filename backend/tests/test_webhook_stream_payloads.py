"""Regression tests for A2A JSON-RPC SSE StreamResponse parsing."""

from __future__ import annotations

import pytest
from a2a.types import TaskState

from a2a_adapter.webhook_payloads import parse_stream_response_payload


def test_parse_kind_based_task_frame():
    payload = {
        "id": "1",
        "jsonrpc": "2.0",
        "result": {
            "contextId": "ctx-1",
            "id": "task-1",
            "kind": "task",
            "status": {"state": "submitted"},
        },
    }

    task = parse_stream_response_payload(payload, "msg-1")

    assert task.id == "task-1"
    assert task.context_id == "ctx-1"
    assert task.status.state == TaskState.submitted


def test_parse_kind_based_working_status_update():
    payload = {
        "id": "1",
        "jsonrpc": "2.0",
        "result": {
            "contextId": "ctx-1",
            "final": False,
            "kind": "status-update",
            "status": {"state": "working"},
            "taskId": "task-1",
        },
    }

    task = parse_stream_response_payload(payload, "msg-1")

    assert task.id == "task-1"
    assert task.context_id == "ctx-1"
    assert task.status.state == TaskState.working


def test_parse_kind_based_artifact_update():
    payload = {
        "id": "1",
        "jsonrpc": "2.0",
        "result": {
            "append": False,
            "artifact": {
                "artifactId": "art-1",
                "parts": [{"kind": "text", "text": "Hawaii itinerary"}],
            },
            "contextId": "ctx-1",
            "kind": "artifact-update",
            "lastChunk": True,
            "taskId": "task-1",
        },
    }

    task = parse_stream_response_payload(payload, "msg-1")

    assert task.id == "task-1"
    assert task.context_id == "ctx-1"
    assert task.status.state == TaskState.working
    assert task.artifacts is not None
    assert len(task.artifacts) == 1
    assert task.artifacts[0].parts[0].root.text == "Hawaii itinerary"


def test_parse_kind_based_completed_status_without_message_id():
    """SDK agents often omit messageId on embedded status messages."""
    payload = {
        "id": "1",
        "jsonrpc": "2.0",
        "result": {
            "contextId": "ctx-1",
            "final": True,
            "kind": "status-update",
            "status": {
                "message": {
                    "parts": [{"kind": "text", "text": "Trip plan ready"}],
                    "role": "agent",
                },
                "state": "completed",
            },
            "taskId": "task-1",
        },
    }

    task = parse_stream_response_payload(payload, "msg-1")

    assert task.id == "task-1"
    assert task.status.state == TaskState.completed
    assert task.artifacts is not None
    assert task.artifacts[0].parts[0].root.text == "Trip plan ready"


def test_parse_legacy_wrapped_status_update_still_works():
    payload = {
        "statusUpdate": {
            "contextId": "ctx-1",
            "final": True,
            "taskId": "task-1",
            "status": {"state": "completed"},
        }
    }

    task = parse_stream_response_payload(payload, "msg-1")

    assert task.id == "task-1"
    assert task.status.state == TaskState.completed


def test_parse_legacy_wrapped_artifact_update_still_works():
    payload = {
        "artifactUpdate": {
            "contextId": "ctx-1",
            "taskId": "task-1",
            "artifact": {
                "artifactId": "art-1",
                "parts": [{"kind": "text", "text": "chunk"}],
            },
        }
    }

    task = parse_stream_response_payload(payload, "msg-1")

    assert task.id == "task-1"
    assert task.status.state == TaskState.working
    assert task.artifacts[0].parts[0].root.text == "chunk"


def test_parse_travel_planner_stream_sequence():
    """Exact frame shapes that previously stalled the travel planner use case."""
    frames = [
        {
            "id": "1",
            "jsonrpc": "2.0",
            "result": {
                "append": False,
                "artifact": {
                    "artifactId": "tp-current-result",
                    "name": "current_result",
                    "parts": [{"kind": "text", "text": "7-day Hawaii plan"}],
                },
                "contextId": "ctx-tp",
                "kind": "artifact-update",
                "lastChunk": True,
                "taskId": "task-tp",
            },
        },
        {
            "id": "1",
            "jsonrpc": "2.0",
            "result": {
                "contextId": "ctx-tp",
                "final": True,
                "kind": "status-update",
                "status": {
                    "message": {
                        "parts": [{"kind": "text", "text": "7-day Hawaii plan"}],
                        "role": "agent",
                    },
                    "state": "completed",
                },
                "taskId": "task-tp",
            },
        },
    ]

    artifact_task = parse_stream_response_payload(frames[0], "msg-tp")
    terminal_task = parse_stream_response_payload(frames[1], "msg-tp")

    assert artifact_task.status.state == TaskState.working
    assert artifact_task.artifacts[0].parts[0].root.text == "7-day Hawaii plan"
    assert terminal_task.status.state == TaskState.completed
    assert terminal_task.artifacts[0].parts[0].root.text == "7-day Hawaii plan"


def test_parse_weather_agent_stream_sequence():
    frames = [
        {
            "id": "1",
            "jsonrpc": "2.0",
            "result": {
                "contextId": "ctx-wx",
                "id": "task-wx",
                "kind": "task",
                "status": {"state": "submitted"},
            },
        },
        {
            "id": "1",
            "jsonrpc": "2.0",
            "result": {
                "contextId": "ctx-wx",
                "final": False,
                "kind": "status-update",
                "status": {"state": "working"},
                "taskId": "task-wx",
            },
        },
        {
            "id": "1",
            "jsonrpc": "2.0",
            "result": {
                "append": False,
                "artifact": {
                    "artifactId": "art-wx",
                    "parts": [{"kind": "text", "text": "Honolulu is clear"}],
                },
                "contextId": "ctx-wx",
                "kind": "artifact-update",
                "lastChunk": True,
                "taskId": "task-wx",
            },
        },
        {
            "id": "1",
            "jsonrpc": "2.0",
            "result": {
                "contextId": "ctx-wx",
                "final": True,
                "kind": "status-update",
                "status": {
                    "message": {
                        "contextId": "ctx-wx",
                        "kind": "message",
                        "messageId": "m-wx",
                        "parts": [{"kind": "text", "text": "Honolulu is clear"}],
                        "role": "agent",
                        "taskId": "task-wx",
                    },
                    "state": "completed",
                },
                "taskId": "task-wx",
            },
        },
    ]

    states = [
        parse_stream_response_payload(frame, "msg-wx").status.state for frame in frames
    ]

    assert states == [
        TaskState.submitted,
        TaskState.working,
        TaskState.working,
        TaskState.completed,
    ]


def test_parse_rejects_unknown_payload():
    with pytest.raises(ValueError, match="Invalid StreamResponse"):
        parse_stream_response_payload({"foo": "bar"}, "msg-1")
